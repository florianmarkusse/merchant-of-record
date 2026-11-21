"""Issuing and reading invoices (docs/plan-payments.md §6.1).

    order_paid ─▶ invoice_issue (local): lock the order; if its invoice
        exists, nothing to do; else lock the series row, take its next
        number, insert the invoice and its lines, and invoice_issued

One transaction, so a rollback gives the number back and the numbers run
without a gap. Locks are taken order first, then series, always. Kept
apart from `order_create` on purpose: a bug here must not hold back the
order and the capture's ledger entry. No ledger entry: the capture booked
the tax as owed already.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from mor.audit import Actor, ActorKind
from mor.checkout.quotes import MadeQuote, read_quote
from mor.checkout.sessions import read_address, session_by_id
from mor.db.connect import Connection
from mor.entity import ENTITY, registration_for
from mor.invoices.lines import invoice_lines
from mor.invoices.numbering import SERIES, number_text, series_for
from mor.invoices.records import (
    BuyerAddress,
    Invoice,
    InvoiceLine,
    Series,
)
from mor.merchants.store import read_merchant
from mor.messaging.facts import Fact, InvoiceIssuedV1, OrderPaidV1
from mor.messaging.outcome import Applied, ConsumerOutcome, Stale
from mor.messaging.store import append_fact
from mor.money.currency import Currency
from mor.orders.flow import WrongFact
from mor.orders.store import lock_order


ACTOR = Actor(ActorKind.SYSTEM, "invoice_issue")


async def _take_number(conn: Connection, code: str) -> tuple[Series, int]:
    """The series' next number, under its counter row's lock until the
    caller's transaction ends: a rollback gives the number back. The row is
    made on the series' first use; a conflict means it exists already."""
    series = SERIES[code]
    await conn.execute(
        """
        INSERT INTO invoice_series (code, next_number) VALUES (%s, 1)
        ON CONFLICT (code) DO NOTHING
        """,
        (code,),
    )
    cur = await conn.execute(
        "SELECT next_number FROM invoice_series WHERE code = %s FOR UPDATE",
        (code,),
    )
    row = await cur.fetchone()
    assert row is not None
    number: int = row[0]
    await conn.execute(
        "UPDATE invoice_series SET next_number = next_number + 1 WHERE code = %s",
        (code,),
    )
    return series, number


async def issue_invoice(conn: Connection, fact: Fact, now: datetime) -> ConsumerOutcome:
    match fact:
        case OrderPaidV1(order_id=order_id):
            pass
        case _:
            raise WrongFact(f"invoice_issue got {fact!r}")
    order = await lock_order(conn, order_id)
    assert order is not None, f"no order {order_id}"
    cur = await conn.execute("SELECT 1 FROM invoice WHERE order_id = %s", (order.id,))
    if await cur.fetchone() is not None:
        return Stale()  # issued already
    quote = await read_quote(conn, order.quote_id)
    assert isinstance(quote, MadeQuote)
    session = await session_by_id(conn, order.session_id)
    assert session is not None
    address = await read_address(conn, order.address_id)
    assert address is not None
    merchant = await read_merchant(conn, order.merchant_id)
    assert merchant is not None
    lines = invoice_lines(quote, order.option_no, session.lines)
    total = 0
    for line in lines:
        total += line.amount_minor
    option = quote.options[order.option_no]
    assert total == option.total_minor, (total, option.total_minor)
    series, number = await _take_number(conn, series_for(ENTITY, address.country))
    registration = registration_for(ENTITY, address.country)
    invoice_id = uuid.uuid7()
    await conn.execute(
        """
        INSERT INTO invoice
          (id, order_id, series, number, number_text, issued_at, paid_at,
           currency, total_minor, seller_name, seller_address, seller_tax_label,
           seller_tax_id, merchant_legal_name, buyer_name, buyer_line1,
           buyer_line2, buyer_city, buyer_region, buyer_postcode, buyer_country)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s)
        """,
        (
            invoice_id,
            order.id,
            series.code,
            number,
            number_text(series, number, now),
            now,
            order.paid_at,
            quote.currency.value,
            total,
            ENTITY.legal_name,
            ENTITY.address,
            None if registration is None else registration.label,
            None if registration is None else registration.number,
            merchant.legal_name,
            address.name,
            address.line1,
            address.line2,
            address.city,
            address.region,
            address.postcode,
            address.country,
        ),
    )
    for line_no, line in enumerate(lines):
        await conn.execute(
            """
            INSERT INTO invoice_line
              (invoice_id, line_no, section, description, quantity, amount_minor,
               rule_text, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                invoice_id,
                line_no,
                line.section.value,
                line.description,
                line.quantity,
                line.amount_minor,
                line.rule_text,
                line.source,
            ),
        )
    await append_fact(
        conn,
        InvoiceIssuedV1(
            invoice_id=invoice_id, order_id=order.id, merchant_id=order.merchant_id
        ),
        ACTOR,
        now,
    )
    return Applied()


@dataclass(frozen=True)
class _InvoiceRow:
    id: uuid.UUID
    order_id: uuid.UUID
    series: str
    number: int
    number_text: str
    issued_at: datetime
    paid_at: datetime
    currency: Currency
    total_minor: int
    seller_name: str
    seller_address: str
    seller_tax_label: str | None
    seller_tax_id: str | None
    merchant_legal_name: str
    buyer_name: str
    buyer_line1: str
    buyer_line2: str | None
    buyer_city: str
    buyer_region: str | None
    buyer_postcode: str | None
    buyer_country: str


async def invoice_for_order(conn: Connection, order_id: uuid.UUID) -> Invoice | None:
    """The order's invoice, once issued."""
    async with conn.cursor(row_factory=class_row(_InvoiceRow)) as cur:
        await cur.execute(
            """
            SELECT id, order_id, series, number, number_text, issued_at, paid_at,
                   currency, total_minor, seller_name, seller_address,
                   seller_tax_label, seller_tax_id, merchant_legal_name,
                   buyer_name, buyer_line1, buyer_line2, buyer_city, buyer_region,
                   buyer_postcode, buyer_country
            FROM invoice WHERE order_id = %s
            """,
            (order_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    async with conn.cursor(row_factory=class_row(InvoiceLine)) as cur:
        await cur.execute(
            """
            SELECT section, description, quantity, amount_minor, rule_text,
                   source
            FROM invoice_line WHERE invoice_id = %s ORDER BY line_no
            """,
            (row.id,),
        )
        lines = await cur.fetchall()
    return Invoice(
        row.id,
        row.order_id,
        row.series,
        row.number,
        row.number_text,
        row.issued_at,
        row.paid_at,
        row.currency,
        row.total_minor,
        row.seller_name,
        row.seller_address,
        row.seller_tax_label,
        row.seller_tax_id,
        row.merchant_legal_name,
        BuyerAddress(
            row.buyer_name,
            row.buyer_line1,
            row.buyer_line2,
            row.buyer_city,
            row.buyer_region,
            row.buyer_postcode,
            row.buyer_country,
        ),
        tuple(lines),
    )

"""Checkout sessions in the database. Persistence functions take the
connection first and never commit: a session and its
`checkout_session_created` commit together."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from mor.audit import Actor
from mor.checkout.addresses import Address
from mor.checkout.records import (
    SESSION_LIFETIME,
    AfterPayment,
    CheckoutSession,
    Customs,
    LockedSession,
    SessionDraft,
    SessionState,
    SoldLine,
)
from mor.couriers.records import Parcel
from mor.db.connect import Connection
from mor.db.fetch import one_value
from mor.ids import CheckoutAddressId, CheckoutSessionId, MerchantId, QuoteId
from mor.messaging.facts import CheckoutSessionCreatedV1, CheckoutSessionExpiryDueV1
from mor.messaging.store import append_fact
from mor.money.amount import Money
from mor.money.currency import Currency


async def create_session(
    conn: Connection,
    merchant_id: MerchantId,
    draft: SessionDraft,
    actor: Actor,
    now: datetime,
) -> CheckoutSession:
    session_id = CheckoutSessionId(uuid.uuid7())
    expires_at = now + SESSION_LIFETIME
    parcel = draft.parcel
    await conn.execute(
        """
        INSERT INTO checkout_session
          (id, merchant_id, state, merchant_reference, return_url, cancel_url,
           after_payment, weight_g, length_mm, width_mm, height_mm, created_at,
           expires_at, actor_kind, actor_id)
        VALUES (%s, %s, 'open', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            session_id,
            merchant_id,
            draft.merchant_reference,
            draft.return_url,
            draft.cancel_url,
            draft.after_payment.value,
            parcel.weight_g,
            parcel.length_mm,
            parcel.width_mm,
            parcel.height_mm,
            now,
            expires_at,
            actor.kind.value,
            actor.id,
        ),
    )
    for line_no, line in enumerate(draft.lines):
        await conn.execute(
            """
            INSERT INTO checkout_session_line
              (session_id, line_no, name, quantity, unit_price_minor, currency,
               category, merchant_sku, customs_description, origin_country,
               weight_g, hs_code)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                session_id,
                line_no,
                line.name,
                line.quantity,
                line.unit_price.amount_minor,
                line.unit_price.currency.value,
                line.category,
                line.merchant_sku,
                line.customs.description,
                line.customs.origin_country,
                line.customs.weight_g,
                line.customs.hs_code,
            ),
        )
    await append_fact(
        conn,
        CheckoutSessionCreatedV1(
            checkout_session_id=session_id, merchant_id=merchant_id
        ),
        actor,
        now,
    )
    # Time only starts the step; it expires the session under its lock.
    await append_fact(
        conn,
        CheckoutSessionExpiryDueV1(checkout_session_id=session_id),
        actor,
        now,
        deliver_at=expires_at,
    )
    return CheckoutSession(
        id=session_id,
        merchant_id=merchant_id,
        state=SessionState.OPEN,
        lines=draft.lines,
        parcel=draft.parcel,
        merchant_reference=draft.merchant_reference,
        return_url=draft.return_url,
        cancel_url=draft.cancel_url,
        after_payment=draft.after_payment,
        created_at=now,
        expires_at=expires_at,
        address_id=None,
        quote_id=None,
    )


@dataclass(frozen=True, kw_only=True)
class _SessionRow:
    id: CheckoutSessionId
    merchant_id: MerchantId
    state: SessionState
    merchant_reference: str | None
    return_url: str
    cancel_url: str
    after_payment: AfterPayment
    weight_g: int
    length_mm: int
    width_mm: int
    height_mm: int
    created_at: datetime
    expires_at: datetime
    address_id: CheckoutAddressId | None
    quote_id: QuoteId | None


@dataclass(frozen=True, kw_only=True)
class _LineRow:
    name: str
    quantity: int
    unit_price_minor: int
    currency: Currency
    category: str
    merchant_sku: str | None
    customs_description: str
    origin_country: str
    weight_g: int
    hs_code: str | None


async def _session(conn: Connection, row: _SessionRow | None) -> CheckoutSession | None:
    if row is None:
        return None
    async with conn.cursor(row_factory=class_row(_LineRow)) as cur:
        await cur.execute(
            """
            SELECT name, quantity, unit_price_minor, currency, category,
                   merchant_sku, customs_description, origin_country, weight_g,
                   hs_code
            FROM checkout_session_line WHERE session_id = %s ORDER BY line_no
            """,
            (row.id,),
        )
        line_rows = await cur.fetchall()
    lines: list[SoldLine] = []
    for line in line_rows:
        lines.append(
            SoldLine(
                name=line.name,
                quantity=line.quantity,
                unit_price=Money(line.unit_price_minor, line.currency),
                category=line.category,
                merchant_sku=line.merchant_sku,
                customs=Customs(
                    description=line.customs_description,
                    origin_country=line.origin_country,
                    weight_g=line.weight_g,
                    hs_code=line.hs_code,
                ),
            )
        )
    return CheckoutSession(
        id=row.id,
        merchant_id=row.merchant_id,
        state=row.state,
        lines=tuple(lines),
        parcel=Parcel(
            weight_g=row.weight_g,
            length_mm=row.length_mm,
            width_mm=row.width_mm,
            height_mm=row.height_mm,
        ),
        merchant_reference=row.merchant_reference,
        return_url=row.return_url,
        cancel_url=row.cancel_url,
        after_payment=row.after_payment,
        created_at=row.created_at,
        expires_at=row.expires_at,
        address_id=row.address_id,
        quote_id=row.quote_id,
    )


async def read_session(
    conn: Connection, merchant_id: MerchantId, session_id: CheckoutSessionId
) -> CheckoutSession | None:
    """The merchant's own session; another merchant's reads as none."""
    async with conn.cursor(row_factory=class_row(_SessionRow)) as cur:
        await cur.execute(
            """
            SELECT id, merchant_id, state, merchant_reference, return_url,
                   cancel_url, after_payment, weight_g, length_mm,
                   width_mm, height_mm, created_at, expires_at, address_id,
                   quote_id
            FROM checkout_session WHERE id = %s AND merchant_id = %s
            """,
            (session_id, merchant_id),
        )
        row = await cur.fetchone()
    return await _session(conn, row)


async def session_by_id(
    conn: Connection, session_id: CheckoutSessionId
) -> CheckoutSession | None:
    """A session by its id alone: the buyer's link is its own bearer token."""
    async with conn.cursor(row_factory=class_row(_SessionRow)) as cur:
        await cur.execute(
            """
            SELECT id, merchant_id, state, merchant_reference, return_url,
                   cancel_url, after_payment, weight_g, length_mm,
                   width_mm, height_mm, created_at, expires_at, address_id,
                   quote_id
            FROM checkout_session WHERE id = %s
            """,
            (session_id,),
        )
        row = await cur.fetchone()
    return await _session(conn, row)


async def lock_session(
    conn: Connection, session_id: CheckoutSessionId
) -> LockedSession | None:
    """The session under its row lock, for a step that changes it."""
    async with conn.cursor(row_factory=class_row(_SessionRow)) as cur:
        await cur.execute(
            """
            SELECT id, merchant_id, state, merchant_reference, return_url,
                   cancel_url, after_payment, weight_g, length_mm,
                   width_mm, height_mm, created_at, expires_at, address_id,
                   quote_id
            FROM checkout_session WHERE id = %s FOR UPDATE
            """,
            (session_id,),
        )
        row = await cur.fetchone()
    found = await _session(conn, row)
    return None if found is None else LockedSession(found)


async def set_session_state(
    conn: Connection,
    session: LockedSession,
    state: SessionState,
    address_id: CheckoutAddressId | None,
    quote_id: QuoteId | None,
) -> None:
    """Never commits."""
    await conn.execute(
        """
        UPDATE checkout_session SET state = %s, address_id = %s, quote_id = %s
        WHERE id = %s
        """,
        (state.value, address_id, quote_id, session.id),
    )


async def add_address(
    conn: Connection, session: LockedSession, address: Address, now: datetime
) -> CheckoutAddressId:
    """A new address for the session; addresses are never changed. The
    caller has counted them under the session's lock. Never commits."""
    address_id = CheckoutAddressId(uuid.uuid7())
    await conn.execute(
        """
        INSERT INTO checkout_address
          (id, session_id, name, email, phone_dial_code, phone_number, line1,
           line2, city, region, postcode, country, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            address_id,
            session.id,
            address.name,
            address.email,
            address.phone_dial_code,
            address.phone_number,
            address.line1,
            address.line2,
            address.city,
            address.region,
            address.postcode,
            address.country,
            now,
        ),
    )
    return address_id


async def address_count(conn: Connection, session_id: CheckoutSessionId) -> int:
    return await one_value(
        conn,
        "SELECT count(*) FROM checkout_address WHERE session_id = %s",
        (session_id,),
        int,
    )


async def read_address(
    conn: Connection, address_id: CheckoutAddressId
) -> Address | None:
    async with conn.cursor(row_factory=class_row(Address)) as cur:
        await cur.execute(
            """
            SELECT name, email, phone_dial_code, phone_number, line1, line2, city,
                   region, postcode, country
            FROM checkout_address WHERE id = %s
            """,
            (address_id,),
        )
        return await cur.fetchone()


@dataclass(frozen=True, kw_only=True)
class SessionSummary:
    """One row of the staff page's session list."""

    id: CheckoutSessionId
    merchant_name: str
    state: SessionState
    created_at: datetime
    country: str | None
    quote_id: QuoteId | None
    open_waits: int  # lookups the current address still waits on


async def recent_sessions(conn: Connection, limit: int) -> list[SessionSummary]:
    async with conn.cursor(row_factory=class_row(SessionSummary)) as cur:
        await cur.execute(
            """
            SELECT s.id, m.display_name AS merchant_name, s.state,
                   s.created_at, a.country, s.quote_id,
                   (SELECT count(*) FROM quote_wait w
                      LEFT JOIN duty_lookup d ON d.id = w.duty_lookup_id
                      LEFT JOIN shipping_price_lookup p
                        ON p.id = w.shipping_price_lookup_id
                    WHERE w.address_id = s.address_id
                      AND (d.state = 'requested' OR p.state = 'requested')
                   )::int AS open_waits
            FROM checkout_session s
            JOIN merchant m ON m.id = s.merchant_id
            LEFT JOIN checkout_address a ON a.id = s.address_id
            ORDER BY s.id DESC LIMIT %s
            """,
            (limit,),
        )
        return await cur.fetchall()


async def address_countries(
    conn: Connection, session_id: CheckoutSessionId
) -> dict[str, str]:
    """Each of the session's addresses (id in hex) to its country only."""
    async with conn.cursor(row_factory=class_row(_AddressCountryRow)) as cur:
        await cur.execute(
            "SELECT id, country FROM checkout_address WHERE session_id = %s",
            (session_id,),
        )
        found = await cur.fetchall()
    countries: dict[str, str] = {}
    for address in found:
        countries[address.id.hex] = address.country
    return countries


@dataclass(frozen=True)
class _AddressCountryRow:
    id: CheckoutAddressId
    country: str

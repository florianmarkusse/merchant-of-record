"""The stored quote (docs/plan-quote.md §5, 4d-2; plan-architecture §8):
immutable, with every part exact and rounded, and the inputs it used.

`stored_from` is the pure projection of a built `Quote` into what is
stored; `read_quote` gives the same record back, which a property checks.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction

from psycopg.rows import class_row

from mor.db.connect import Connection
from mor.markets.records import Collector, ServiceTier, Standing
from mor.money.currency import Currency
from mor.money.rounding import RoundingMode
from mor.quote.build import Part, Quote
from mor.quote.groups import PartKind
from mor.quote.refusals import RefusalCode


@dataclass(frozen=True)
class StoredPart:
    kind: PartKind
    label: str
    line: int | None
    exact: Fraction  # minor units of the quote's currency
    amount_minor: int
    rounding: RoundingMode
    collector: Collector | None
    source: str


@dataclass(frozen=True)
class StoredOption:
    key: str
    service: str
    service_name: str
    tier: ServiceTier
    transit: tuple[int, int] | None
    total_minor: int
    standing: Standing
    parts: tuple[StoredPart, ...]  # shipping, then duties and taxes


@dataclass(frozen=True)
class QuoteInputs:
    """The vendor answers the quote used."""

    duty_lookups: tuple[uuid.UUID, ...]
    price_lookups: tuple[uuid.UUID, ...]


@dataclass(frozen=True)
class MadeQuote:
    id: uuid.UUID
    session_id: uuid.UUID
    address_id: uuid.UUID
    load_id: uuid.UUID
    made_at: datetime
    valid_until: datetime
    currency: Currency
    goods: tuple[StoredPart, ...]  # each line's price, then the fees
    options: tuple[StoredOption, ...]  # cheapest first
    placeholders: tuple[str, ...]
    not_offered: tuple[tuple[str, str], ...]  # (key, reason)
    inputs: QuoteInputs


@dataclass(frozen=True)
class RefusedQuote:
    id: uuid.UUID
    session_id: uuid.UUID
    address_id: uuid.UUID
    load_id: uuid.UUID
    made_at: datetime
    code: RefusalCode
    message: str


type StoredQuote = MadeQuote | RefusedQuote


def _stored_part(part: Part) -> StoredPart:
    return StoredPart(
        part.kind,
        part.label,
        part.line,
        part.exact,
        part.amount.amount_minor,
        part.mode,
        part.collector,
        part.source,
    )


def stored_from(
    quote: Quote,
    quote_id: uuid.UUID,
    session_id: uuid.UUID,
    address_id: uuid.UUID,
    load_id: uuid.UUID,
    made_at: datetime,
    valid_until: datetime,
    inputs: QuoteInputs,
) -> MadeQuote:
    """What is stored of a built quote. Pure."""
    goods: list[StoredPart] = []
    for part in quote.goods:
        goods.append(_stored_part(part))
    options: list[StoredOption] = []
    for option in quote.options:
        parts: list[StoredPart] = []
        for part in option.parts:
            parts.append(_stored_part(part))
        options.append(
            StoredOption(
                option.key,
                option.service,
                option.service_name,
                option.tier,
                option.transit_days,
                option.total.amount_minor,
                option.standing,
                tuple(parts),
            )
        )
    not_offered: list[tuple[str, str]] = []
    for refused in quote.not_offered:
        not_offered.append((refused.key, refused.reason))
    return MadeQuote(
        quote_id,
        session_id,
        address_id,
        load_id,
        made_at,
        valid_until,
        quote.currency,
        tuple(goods),
        tuple(options),
        quote.placeholders,
        tuple(not_offered),
        inputs,
    )


async def _insert_part(
    conn: Connection,
    quote_id: uuid.UUID,
    option_no: int | None,
    part_no: int,
    part: StoredPart,
) -> None:
    collector = None if part.collector is None else part.collector.value
    await conn.execute(
        """
        INSERT INTO quote_part
          (quote_id, option_no, part_no, kind, label, line, exact_numerator,
           exact_denominator, amount_minor, rounding, collector, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            quote_id,
            option_no,
            part_no,
            part.kind.value,
            part.label,
            part.line,
            part.exact.numerator,
            part.exact.denominator,
            part.amount_minor,
            part.rounding.value,
            collector,
            part.source,
        ),
    )


async def store_made(conn: Connection, made: MadeQuote) -> None:
    """Never commits."""
    await conn.execute(
        """
        INSERT INTO quote
          (id, session_id, address_id, market_load_id, state, made_at, currency,
           valid_until)
        VALUES (%s, %s, %s, %s, 'made', %s, %s, %s)
        """,
        (
            made.id,
            made.session_id,
            made.address_id,
            made.load_id,
            made.made_at,
            made.currency.value,
            made.valid_until,
        ),
    )
    for part_no, part in enumerate(made.goods):
        await _insert_part(conn, made.id, None, part_no, part)
    for option_no, option in enumerate(made.options):
        transit_min: int | None = None
        transit_max: int | None = None
        if option.transit is not None:
            transit_min, transit_max = option.transit
        await conn.execute(
            """
            INSERT INTO quote_option
              (quote_id, option_no, key, service, service_name, tier,
               transit_min, transit_max, total_minor, standing)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                made.id,
                option_no,
                option.key,
                option.service,
                option.service_name,
                option.tier.value,
                transit_min,
                transit_max,
                option.total_minor,
                option.standing.value,
            ),
        )
        for part_no, part in enumerate(option.parts):
            await _insert_part(conn, made.id, option_no, part_no, part)
    for placeholder_no, placeholder in enumerate(made.placeholders):
        await conn.execute(
            """
            INSERT INTO quote_placeholder (quote_id, placeholder_no, key)
            VALUES (%s, %s, %s)
            """,
            (made.id, placeholder_no, placeholder),
        )
    for not_offered_no, (key, reason) in enumerate(made.not_offered):
        await conn.execute(
            """
            INSERT INTO quote_not_offered (quote_id, not_offered_no, key, reason)
            VALUES (%s, %s, %s, %s)
            """,
            (made.id, not_offered_no, key, reason),
        )
    for lookup_id in made.inputs.duty_lookups:
        await conn.execute(
            "INSERT INTO quote_input (quote_id, duty_lookup_id) VALUES (%s, %s)",
            (made.id, lookup_id),
        )
    for lookup_id in made.inputs.price_lookups:
        await conn.execute(
            "INSERT INTO quote_input (quote_id, shipping_price_lookup_id)"
            " VALUES (%s, %s)",
            (made.id, lookup_id),
        )


async def store_refused(conn: Connection, refused: RefusedQuote) -> None:
    """Never commits."""
    await conn.execute(
        """
        INSERT INTO quote
          (id, session_id, address_id, market_load_id, state, made_at,
           refusal_code, refusal_message)
        VALUES (%s, %s, %s, %s, 'refused', %s, %s, %s)
        """,
        (
            refused.id,
            refused.session_id,
            refused.address_id,
            refused.load_id,
            refused.made_at,
            refused.code,
            refused.message,
        ),
    )


@dataclass(frozen=True)
class _QuoteRow:
    id: uuid.UUID
    session_id: uuid.UUID
    address_id: uuid.UUID
    market_load_id: uuid.UUID
    state: str
    made_at: datetime
    currency: Currency | None
    valid_until: datetime | None
    refusal_code: str | None
    refusal_message: str | None


@dataclass(frozen=True)
class _PartRow:
    option_no: int | None
    kind: PartKind
    label: str
    line: int | None
    exact_numerator: Decimal
    exact_denominator: Decimal
    amount_minor: int
    rounding: RoundingMode
    collector: Collector | None
    source: str


@dataclass(frozen=True)
class _OptionRow:
    option_no: int
    key: str
    service: str
    service_name: str
    tier: ServiceTier
    transit_min: int | None
    transit_max: int | None
    total_minor: int
    standing: Standing


REFUSAL_CODES: tuple[RefusalCode, ...] = (
    "country_not_supported",
    "country_closed",
    "classification_needed",
    "duty_rate_unavailable",
    "no_service_for_lane",
    "pricing_unavailable",
)


def _refusal_code(text: str) -> RefusalCode:
    for code in REFUSAL_CODES:
        if code == text:
            return code
    raise AssertionError(f"unknown refusal code {text!r}")


def _part(row: _PartRow) -> StoredPart:
    return StoredPart(
        row.kind,
        row.label,
        row.line,
        Fraction(int(row.exact_numerator), int(row.exact_denominator)),
        row.amount_minor,
        row.rounding,
        row.collector,
        row.source,
    )


async def read_quote(conn: Connection, quote_id: uuid.UUID) -> StoredQuote | None:
    async with conn.cursor(row_factory=class_row(_QuoteRow)) as cur:
        await cur.execute(
            """
            SELECT id, session_id, address_id, market_load_id, state::text,
                   made_at, currency, valid_until, refusal_code,
                   refusal_message
            FROM quote WHERE id = %s
            """,
            (quote_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    if row.state == "refused":
        assert row.refusal_code is not None and row.refusal_message is not None
        return RefusedQuote(
            row.id,
            row.session_id,
            row.address_id,
            row.market_load_id,
            row.made_at,
            _refusal_code(row.refusal_code),
            row.refusal_message,
        )
    assert row.currency is not None and row.valid_until is not None
    async with conn.cursor(row_factory=class_row(_PartRow)) as cur:
        await cur.execute(
            """
            SELECT option_no, kind, label, line, exact_numerator,
                   exact_denominator, amount_minor, rounding,
                   collector, source
            FROM quote_part WHERE quote_id = %s ORDER BY option_no NULLS FIRST, part_no
            """,
            (quote_id,),
        )
        part_rows = await cur.fetchall()
    async with conn.cursor(row_factory=class_row(_OptionRow)) as cur:
        await cur.execute(
            """
            SELECT option_no, key, service, service_name, tier,
                   transit_min, transit_max, total_minor, standing
            FROM quote_option WHERE quote_id = %s ORDER BY option_no
            """,
            (quote_id,),
        )
        option_rows = await cur.fetchall()
    goods: list[StoredPart] = []
    by_option: dict[int, list[StoredPart]] = {}
    for part_row in part_rows:
        if part_row.option_no is None:
            goods.append(_part(part_row))
        else:
            by_option.setdefault(part_row.option_no, []).append(_part(part_row))
    options: list[StoredOption] = []
    for option_row in option_rows:
        transit: tuple[int, int] | None = None
        if option_row.transit_min is not None and option_row.transit_max is not None:
            transit = (option_row.transit_min, option_row.transit_max)
        options.append(
            StoredOption(
                option_row.key,
                option_row.service,
                option_row.service_name,
                option_row.tier,
                transit,
                option_row.total_minor,
                option_row.standing,
                tuple(by_option.get(option_row.option_no, [])),
            )
        )
    cur = await conn.execute(
        "SELECT key FROM quote_placeholder WHERE quote_id = %s ORDER BY placeholder_no",
        (quote_id,),
    )
    placeholders: list[str] = []
    for (key,) in await cur.fetchall():
        placeholders.append(key)
    cur = await conn.execute(
        """
        SELECT key, reason::text FROM quote_not_offered
        WHERE quote_id = %s ORDER BY not_offered_no
        """,
        (quote_id,),
    )
    not_offered: list[tuple[str, str]] = []
    for key, reason in await cur.fetchall():
        not_offered.append((key, reason))
    cur = await conn.execute(
        """
        SELECT duty_lookup_id, shipping_price_lookup_id FROM quote_input
        WHERE quote_id = %s ORDER BY duty_lookup_id, shipping_price_lookup_id
        """,
        (quote_id,),
    )
    duty_lookups: list[uuid.UUID] = []
    price_lookups: list[uuid.UUID] = []
    for duty_lookup_id, price_lookup_id in await cur.fetchall():
        if duty_lookup_id is not None:
            duty_lookups.append(duty_lookup_id)
        else:
            price_lookups.append(price_lookup_id)
    return MadeQuote(
        row.id,
        row.session_id,
        row.address_id,
        row.market_load_id,
        row.made_at,
        row.valid_until,
        row.currency,
        tuple(goods),
        tuple(options),
        tuple(placeholders),
        tuple(not_offered),
        QuoteInputs(tuple(duty_lookups), tuple(price_lookups)),
    )


async def session_quotes(conn: Connection, session_id: uuid.UUID) -> list[StoredQuote]:
    """Every quote of a session, newest first: for the staff page."""
    cur = await conn.execute(
        "SELECT id FROM quote WHERE session_id = %s ORDER BY id DESC", (session_id,)
    )
    found: list[StoredQuote] = []
    for (quote_id,) in await cur.fetchall():
        stored = await read_quote(conn, quote_id)
        assert stored is not None
        found.append(stored)
    return found

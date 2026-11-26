"""Live prices from carriers, asked by the worker and held while usable
(docs/plan-quote.md §4, 4c-2).

    request_shipping_price ──shipping_price_needed──▶ shipping_price_ask
        requested ──quote_rate──▶ answered | not_served | needs_person
        (Unavailable, OutcomeUnknown: asked again after the worker's backoff)

A price depends on the parcel and expires, so it is not asked once for
good like a duty rate. The guard is one open request per combination at a
time (a partial unique index on the `requested` rows). A finished lookup is
held until `usable_until`, by the account's rule in force when it is read.
A rate call is a read, so asking again after a lost answer is safe; each
call is counted before it is made. The consumer is three steps, run by
`mor.messaging.delivery.run_steps`: `prepare_rate` and `complete_rate` in
its transactions under the lookup's row lock (they never commit),
`ask_rate` with no transaction open and no connection.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx2
from psycopg.rows import class_row

from mor.audit import Actor, ActorKind
from mor.carriers import registry
from mor.carriers.accounts import carrier_account
from mor.carriers.port import (
    CarrierPrice,
    NotServed,
    QuoteRateResult,
)
from mor.carriers.records import (
    CarrierAccount,
    FinishedLookup,
    OwnPrice,
    Parcel,
    PriceExpiry,
    PriceHeld,
    PricePart,
    PriceQuestion,
    PriceState,
    StoredPrice,
    usable_until,
)
from mor.config import Environment
from mor.db.connect import Connection
from mor.ids import (
    CarrierCode,
    CountryCode,
    MarketLoadId,
    ServiceCode,
    ShippingPriceLookupId,
)
from mor.markets.store import latest_load, read_market
from mor.messaging.facts import (
    ShippingPriceFoundV1,
    ShippingPriceNeededV1,
    ShippingPriceNeedsPersonV1,
    ShippingPriceNotServedV1,
)
from mor.messaging.outcome import Applied, Done, NoCall, RetryLater, Stale
from mor.messaging.store import append_fact
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.money.rounding import RoundingMode, round_fraction
from mor.quote.shipping import own_card_price
from mor.vendor_failures import OutcomeUnknown, Unavailable, UndecodableBody


ACTOR = Actor(ActorKind.SYSTEM, "shipping_price_ask")


@dataclass(frozen=True)
class NoCarrierAccount:
    """This environment has no account for the carrier a route names:
    nothing can be asked."""

    carrier: CarrierCode


@dataclass(frozen=True)
class PriceCall:
    """What `prepare_rate` committed to ask, for `ask_rate`."""

    account: CarrierAccount
    question: PriceQuestion


# --- rows -----------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class _LookupRow:
    id: ShippingPriceLookupId
    carrier: CarrierCode
    service: ServiceCode
    origin: CountryCode
    destination: CountryCode
    weight_g: int
    length_mm: int
    width_mm: int
    height_mm: int
    state: PriceState
    calls: int
    detail: str | None
    total_minor: int | None
    currency: Currency | None
    finished_at: datetime | None
    carrier_valid_until: datetime | None
    own_load_id: MarketLoadId | None
    own_rate_card: str | None
    own_total_minor: int | None
    own_currency: Currency | None


# --- accounts ---------------------------------------------------------------


async def set_price_rule(
    conn: Connection, carrier: CarrierCode, valid_for: timedelta, expiry: PriceExpiry
) -> None:
    """How long the carrier's prices are used, from now on, for every price
    already held too: nothing stored changes. Never commits."""
    await conn.execute(
        """
        UPDATE carrier_account SET price_valid_for_s = %s, price_expiry = %s
        WHERE carrier = %s
        """,
        (int(valid_for.total_seconds()), expiry.value, carrier),
    )


# --- asking -----------------------------------------------------------------


async def request_shipping_price(
    conn: Connection, question: PriceQuestion, actor: Actor, now: datetime
) -> ShippingPriceLookupId | NoCarrierAccount:
    """The lookup that serves this question at `now`: the newest finished
    one while usable, else the open request, opened here if there is none.
    Insert first; a conflict means another request is open, and that one is
    the answer. Never commits."""
    account = await carrier_account(conn, question.carrier)
    if account is None:
        return NoCarrierAccount(question.carrier)
    held = await read_price(conn, question)
    if held.latest is not None and held.latest.usable_until > now:
        return held.latest.id
    parcel = question.parcel
    cur = await conn.execute(
        """
        INSERT INTO shipping_price_lookup
          (id, carrier, service, origin, destination, weight_g, length_mm,
           width_mm, height_mm, state, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'requested', %s, %s)
        ON CONFLICT (carrier, service, origin, destination, weight_g, length_mm,
                     width_mm, height_mm) WHERE state = 'requested'
        DO NOTHING
        RETURNING id
        """,
        (
            uuid.uuid7(),
            question.carrier,
            question.service,
            question.origin,
            question.destination,
            parcel.weight_g,
            parcel.length_mm,
            parcel.width_mm,
            parcel.height_mm,
            now,
            now,
        ),
    )
    row = await cur.fetchone()
    if row is not None:
        lookup_id: ShippingPriceLookupId = row[0]
        await append_fact(
            conn, ShippingPriceNeededV1(shipping_price_lookup_id=lookup_id), actor, now
        )
        return lookup_id
    # Another request is open, or was answered since our read: either is
    # the answer.
    held = await read_price(conn, question)
    if held.open_id is not None:
        return held.open_id
    assert held.latest is not None, "a conflicting lookup must exist"
    return held.latest.id


async def read_price(conn: Connection, question: PriceQuestion) -> PriceHeld:
    """What we hold for a question: its newest finished lookup, with when
    it stops being usable by the account's rule now, and the open request."""
    parcel = question.parcel
    async with conn.cursor(row_factory=class_row(_LookupRow)) as cur:
        await cur.execute(
            """
            SELECT id, carrier, service, origin, destination, weight_g, length_mm,
                   width_mm, height_mm, state, calls, detail, total_minor,
                   currency, finished_at, carrier_valid_until, own_load_id,
                   own_rate_card, own_total_minor, own_currency
            FROM shipping_price_lookup
            WHERE carrier = %s AND service = %s AND origin = %s
              AND destination = %s AND weight_g = %s AND length_mm = %s
              AND width_mm = %s AND height_mm = %s
            ORDER BY finished_at DESC NULLS FIRST, id DESC
            """,
            (
                question.carrier,
                question.service,
                question.origin,
                question.destination,
                parcel.weight_g,
                parcel.length_mm,
                parcel.width_mm,
                parcel.height_mm,
            ),
        )
        rows = await cur.fetchall()
    open_id: ShippingPriceLookupId | None = None
    newest: _LookupRow | None = None
    for row in rows:
        if row.state == PriceState.REQUESTED:
            open_id = row.id
        elif newest is None:
            newest = row
    if newest is None:
        return PriceHeld(None, open_id)
    account = await carrier_account(conn, question.carrier)
    assert account is not None, "a lookup's account is never removed"
    return PriceHeld(await _finished(conn, newest, account), open_id)


async def _finished(
    conn: Connection, row: _LookupRow, account: CarrierAccount
) -> FinishedLookup:
    assert row.finished_at is not None, "a finished lookup has its moment"
    price: StoredPrice | None = None
    if row.total_minor is not None and row.currency is not None:
        async with conn.cursor(row_factory=class_row(PricePart)) as cur:
            await cur.execute(
                """
                SELECT kind, name, amount_minor FROM shipping_price_part
                WHERE lookup_id = %s ORDER BY line_no
                """,
                (row.id,),
            )
            parts = await cur.fetchall()
        own: OwnPrice | None = None
        if (
            row.own_load_id is not None
            and row.own_rate_card is not None
            and row.own_total_minor is not None
            and row.own_currency is not None
        ):
            own = OwnPrice(
                row.own_load_id,
                row.own_rate_card,
                Money(row.own_total_minor, row.own_currency),
            )
        price = StoredPrice(
            total=Money(row.total_minor, row.currency),
            parts=tuple(parts),
            carrier_valid_until=row.carrier_valid_until,
            own=own,
        )
    return FinishedLookup(
        id=row.id,
        state=row.state,
        calls=row.calls,
        detail=row.detail,
        finished_at=row.finished_at,
        usable_until=usable_until(
            row.finished_at,
            row.carrier_valid_until,
            account.price_valid_for,
            account.price_expiry,
        ),
        price=price,
    )


async def _lock_lookup(
    conn: Connection, lookup_id: ShippingPriceLookupId
) -> _LookupRow:
    async with conn.cursor(row_factory=class_row(_LookupRow)) as cur:
        await cur.execute(
            """
            SELECT id, carrier, service, origin, destination, weight_g, length_mm,
                   width_mm, height_mm, state, calls, detail, total_minor,
                   currency, finished_at, carrier_valid_until, own_load_id,
                   own_rate_card, own_total_minor, own_currency
            FROM shipping_price_lookup WHERE id = %s FOR UPDATE
            """,
            (lookup_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"shipping price lookup {lookup_id} does not exist")
    return row


def _question(row: _LookupRow) -> PriceQuestion:
    return PriceQuestion(
        carrier=row.carrier,
        service=row.service,
        origin=row.origin,
        destination=row.destination,
        parcel=Parcel(
            weight_g=row.weight_g,
            length_mm=row.length_mm,
            width_mm=row.width_mm,
            height_mm=row.height_mm,
        ),
    )


# --- the consumer shipping_price_ask: prepare_rate, ask_rate, complete_rate ---


async def prepare_rate(
    conn: Connection, lookup_id: ShippingPriceLookupId, now: datetime
) -> NoCall | PriceCall:
    lookup = await _lock_lookup(conn, lookup_id)
    if lookup.state != PriceState.REQUESTED:
        return NoCall(Stale())
    account = await carrier_account(conn, lookup.carrier)
    assert account is not None, f"no account for {lookup.carrier}"
    # Counted before the call and committed with this step: a call that is
    # made is always counted, even if the worker dies right after.
    await conn.execute(
        """
        UPDATE shipping_price_lookup SET calls = calls + 1, updated_at = %s
        WHERE id = %s
        """,
        (now, lookup.id),
    )
    return PriceCall(account, _question(lookup))


async def ask_rate(
    http: httpx2.AsyncClient, environment: Environment, call: PriceCall
) -> QuoteRateResult:
    return await registry.quote_rate(http, environment, call.account, call.question)


async def complete_rate(
    conn: Connection,
    lookup_id: ShippingPriceLookupId,
    call: PriceCall,
    result: QuoteRateResult,
    now: datetime,
) -> Done | RetryLater:
    lookup = await _lock_lookup(conn, lookup_id)
    if lookup.state != PriceState.REQUESTED:
        return Done(Stale())
    match result:
        case CarrierPrice():
            await _store_price(conn, lookup, result, now)
            await append_fact(
                conn,
                ShippingPriceFoundV1(shipping_price_lookup_id=lookup.id),
                ACTOR,
                now,
            )
            return Done(Applied())
        case NotServed(detail=detail):
            await _finish(conn, lookup.id, PriceState.NOT_SERVED, detail, now)
            await append_fact(
                conn,
                ShippingPriceNotServedV1(
                    shipping_price_lookup_id=lookup.id, detail=detail
                ),
                ACTOR,
                now,
            )
            return Done(Applied())
        case UndecodableBody(detail=detail):
            await _finish(conn, lookup.id, PriceState.NEEDS_PERSON, detail, now)
            await append_fact(
                conn,
                ShippingPriceNeedsPersonV1(
                    shipping_price_lookup_id=lookup.id, detail=detail
                ),
                ACTOR,
                now,
            )
            return Done(Applied())
        case Unavailable(detail=detail):
            return RetryLater(f"unavailable: {detail}")
        case OutcomeUnknown(detail=detail):
            return RetryLater(f"outcome unknown: {detail}")


async def _finish(
    conn: Connection,
    lookup_id: ShippingPriceLookupId,
    state: PriceState,
    detail: str | None,
    now: datetime,
) -> None:
    await conn.execute(
        """
        UPDATE shipping_price_lookup
        SET state = %s, detail = %s, finished_at = %s, updated_at = %s
        WHERE id = %s
        """,
        (state.value, detail, now, now, lookup_id),
    )


async def _store_price(
    conn: Connection, lookup: _LookupRow, price: CarrierPrice, now: datetime
) -> None:
    """The price and its parts, beside our own card's price for the same
    service, lane and parcel in the load in force (in the carrier's
    currency, rounded up as shipping is), when one can price it."""
    own_load_id: MarketLoadId | None = None
    own_card: str | None = None
    own_total: int | None = None
    load = await latest_load(conn)
    if load is not None:
        market = await read_market(conn, load.id)
        question = _question(lookup)
        own = own_card_price(
            market,
            question.service,
            question.destination,
            question.parcel,
            price.currency,
            now.date(),
        )
        if own is not None:
            own_load_id = load.id
            own_card, exact = own
            own_total = round_fraction(exact, RoundingMode.CEILING)
    await conn.execute(
        """
        UPDATE shipping_price_lookup
        SET state = 'answered', detail = NULL, total_minor = %s, currency = %s,
            finished_at = %s, carrier_valid_until = %s, own_load_id = %s,
            own_rate_card = %s, own_total_minor = %s, own_currency = %s,
            updated_at = %s
        WHERE id = %s
        """,
        (
            price.total_minor,
            price.currency.value,
            now,
            price.valid_until,
            own_load_id,
            own_card,
            own_total,
            None if own_total is None else price.currency.value,
            now,
            lookup.id,
        ),
    )
    line_no = 0
    for part in price.parts:
        line_no += 1
        await conn.execute(
            """
            INSERT INTO shipping_price_part
              (lookup_id, line_no, kind, name, amount_minor)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (lookup.id, line_no, part.kind.value, part.name, part.amount_minor),
        )


# --- the answers page -----------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class LivePriceRow:
    """One carrier answer for a person: its price beside our card's."""

    lookup_id: ShippingPriceLookupId
    question: PriceQuestion
    state: PriceState
    calls: int
    detail: str | None
    finished_at: datetime | None
    usable_until: datetime | None
    total: Money | None
    own: OwnPrice | None


async def live_prices(conn: Connection, limit: int) -> list[LivePriceRow]:
    """The newest lookups first, with each one's usable_until by its
    account's rule now."""
    async with conn.cursor(row_factory=class_row(_LookupRow)) as cur:
        await cur.execute(
            """
            SELECT id, carrier, service, origin, destination, weight_g, length_mm,
                   width_mm, height_mm, state, calls, detail, total_minor,
                   currency, finished_at, carrier_valid_until, own_load_id,
                   own_rate_card, own_total_minor, own_currency
            FROM shipping_price_lookup ORDER BY id DESC LIMIT %s
            """,
            (limit,),
        )
        rows = await cur.fetchall()
    accounts: dict[str, CarrierAccount] = {}
    result: list[LivePriceRow] = []
    for row in rows:
        if row.carrier not in accounts:
            account = await carrier_account(conn, row.carrier)
            assert account is not None, "a lookup's account is never removed"
            accounts[row.carrier] = account
        finished: FinishedLookup | None = None
        if row.finished_at is not None:
            finished = await _finished(conn, row, accounts[row.carrier])
        total: Money | None = None
        own: OwnPrice | None = None
        if finished is not None and finished.price is not None:
            total = finished.price.total
            own = finished.price.own
        result.append(
            LivePriceRow(
                lookup_id=row.id,
                question=_question(row),
                state=row.state,
                calls=row.calls,
                detail=row.detail,
                finished_at=row.finished_at,
                usable_until=None if finished is None else finished.usable_until,
                total=total,
                own=own,
            )
        )
    return result

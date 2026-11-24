"""What a quote reads besides the market data: the vendor lookups and
carrier prices we hold for its questions, and asking for what is missing
(docs/plan-quote.md §4 and §5). Asking only writes rows and facts; the
worker makes the calls, so no handler calls a vendor."""

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from mor.audit import Actor
from mor.couriers.prices import NoCourierAccount, read_price, request_shipping_price
from mor.couriers.records import Parcel, PriceHeld, PriceQuestion, PriceState
from mor.db.connect import Connection
from mor.duties.lookups import NoVendorAccount, read_lookup, request_duty_rate
from mor.duties.port import RateQuestion
from mor.duties.records import LookupState, VendorLookup
from mor.ids import DutyLookupId, ShippingPriceLookupId
from mor.markets.records import DutyMode, MarketSet, PriceSource
from mor.quote.build import InputsMissing, QuoteRequest
from mor.quote.shipping import price_question, routes_to
from mor.tax.line_rates import duty_source_for


async def held_prices(
    conn: Connection, market: MarketSet, destination: str, parcel: Parcel
) -> dict[PriceQuestion, PriceHeld]:
    """What we hold from carriers for every live route to the destination."""
    prices: dict[PriceQuestion, PriceHeld] = {}
    for route in routes_to(market, destination):
        if route.source != PriceSource.CARRIER_API:
            continue
        for service in market.services:
            if service.code == route.service:
                question = price_question(service, destination, parcel)
                prices[question] = await read_price(conn, question)
    return prices


async def held_lookups(
    conn: Connection, market: MarketSet, request: QuoteRequest
) -> dict[RateQuestion, VendorLookup]:
    """What the destination's duty vendor answered for each line's
    combination, where the line has an HS code and a vendor decides."""
    source = duty_source_for(market, request.destination)
    lookups: dict[RateQuestion, VendorLookup] = {}
    if source.mode == DutyMode.OWN_ONLY or source.vendor is None:
        return lookups
    for line in request.lines:
        if line.hs_code is None:
            continue
        question = RateQuestion(request.destination, line.origin, line.hs_code)
        if question in lookups:
            continue
        lookup = await read_lookup(conn, source.vendor, question)
        if lookup is not None:
            lookups[question] = lookup
    return lookups


@dataclass(frozen=True)
class NoAccount:
    """A vendor or carrier the market data routes to has no account in
    this environment."""

    name: str


def new_asks(missing: InputsMissing) -> int:
    """How many lookups asking for `missing` starts: each ask is for a
    question nothing is held or open for."""
    return len(missing.asks) + len(missing.price_asks)


@dataclass(frozen=True)
class Asked:
    """The lookups now answering what was missing: asked here, or already
    open."""

    duty_lookups: tuple[DutyLookupId, ...]
    price_lookups: tuple[ShippingPriceLookupId, ...]


async def ask_missing(
    conn: Connection, missing: InputsMissing, actor: Actor, now: datetime
) -> Asked | NoAccount:
    """Ask for every missing input: lookup rows and their facts, in the
    caller's transaction. Never commits."""
    duty_lookups = list(missing.waiting)
    price_lookups = list(missing.price_waiting)
    for ask in missing.asks:
        asked = await request_duty_rate(conn, ask.vendor, ask.question, actor, now)
        if isinstance(asked, NoVendorAccount):
            return NoAccount(asked.vendor)
        duty_lookups.append(asked)
    for question in missing.price_asks:
        priced = await request_shipping_price(conn, question, actor, now)
        if isinstance(priced, NoCourierAccount):
            return NoAccount(priced.carrier)
        price_lookups.append(priced)
    return Asked(tuple(duty_lookups), tuple(price_lookups))


@dataclass(frozen=True)
class _DutyStateRow:
    state: LookupState


@dataclass(frozen=True)
class _PriceStateRow:
    state: PriceState


async def still_open(conn: Connection, asked: Asked) -> bool:
    """Whether every lookup is still being asked, holding a share lock on
    each until the caller commits. A lookup finishes under its own row lock
    and writes its outcome fact in that transaction, so it cannot finish
    between this check and the caller's commit: whoever waits on it from
    that commit is told when it does. False: one finished meanwhile, so
    build again instead of waiting."""
    for lookup_id in asked.duty_lookups:
        async with conn.cursor(row_factory=class_row(_DutyStateRow)) as cur:
            await cur.execute(
                "SELECT state FROM duty_lookup WHERE id = %s FOR SHARE",
                (lookup_id,),
            )
            duty = await cur.fetchone()
        if duty is None or duty.state != LookupState.REQUESTED:
            return False
    for lookup_id in asked.price_lookups:
        async with conn.cursor(row_factory=class_row(_PriceStateRow)) as cur:
            await cur.execute(
                "SELECT state FROM shipping_price_lookup WHERE id = %s FOR SHARE",
                (lookup_id,),
            )
            price = await cur.fetchone()
        if price is None or price.state != PriceState.REQUESTED:
            return False
    return True

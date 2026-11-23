"""The quote flow (docs/plan-quote.md §5, 4d-2; plan-architecture §8,
"Waiting"): a buyer's address in, a stored quote or refusal out, or the
missing inputs asked for and waited on. Every step locks the session row
and decides under that lock; no step calls a vendor.

    open ──address──▶ quoting ──inputs held──▶ quoted
                         └──────refused──────▶ refused
    a new address (from open, quoting, quoted, refused) quotes again
    any of those ──expiry due, now >= expires_at──▶ expired
    quoted ──pay──▶ paying ──captured──▶ completed   (mor.payments, mor.orders)
    paying ──the attempt ended without money──▶ quoted, or expired if its
        time passed meanwhile (`end_payment`). `paying` never expires by
        itself: a payment made is always honoured.

A finished lookup is announced to the sessions waiting on it by
`notify_waiting`, which locks no session; each session then builds again
in `build_waiting`, under its own lock (one step locks one aggregate).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from mor.audit import Actor, ActorKind
from mor.checkout.addresses import Address
from mor.checkout.quotes import (
    MadeQuote,
    QuoteInputs,
    RefusedQuote,
    read_quote,
    store_made,
    store_refused,
    stored_from,
)
from mor.checkout.records import MAX_ADDRESSES, CheckoutSession, SessionState
from mor.checkout.sessions import (
    add_address,
    address_count,
    lock_session,
    read_address,
    set_session_state,
)
from mor.config import Environment
from mor.db.connect import Connection
from mor.duties.records import LookupState
from mor.markets.store import load_in_force
from mor.messaging.facts import (
    CheckoutSessionExpiredV1,
    CheckoutSessionExpiryDueV1,
    DutyRateFoundV1,
    DutyRateNeedsPersonV1,
    DutyRateUnknownCodeV1,
    Fact,
    PaymentCancelledV1,
    PaymentDeclinedV1,
    PaymentExpiredV1,
    QuoteInputsArrivedV1,
    QuoteMadeV1,
    QuoteRefusedV1,
    QuoteRequestedV1,
    ShippingPriceFoundV1,
    ShippingPriceNeedsPersonV1,
    ShippingPriceNotServedV1,
)
from mor.messaging.outcome import Applied, ConsumerOutcome, Stale
from mor.messaging.routing import WrongFact
from mor.messaging.store import append_fact
from mor.quote.build import InputsMissing, Quote, QuoteLine, QuoteRequest, build_quote
from mor.quote.inputs import (
    Asked,
    NoAccount,
    ask_missing,
    held_lookups,
    held_prices,
    still_open,
)
from mor.quote.refusals import Refusal, offered_in, refusal_of


QUOTE_VALID_FOR = timedelta(minutes=30)  # answered 19-Nov-2026
# Builds in one step when a lookup finishes between building and waiting;
# past this the step asks the worker to build again.
MAX_BUILD_ROUNDS = 3
FLOW = Actor(ActorKind.SYSTEM, "quote_flow")

# The states a buyer may still give an address in, and that expire.
LIVE_STATES = (
    SessionState.OPEN,
    SessionState.QUOTING,
    SessionState.QUOTED,
    SessionState.REFUSED,
)


@dataclass(frozen=True)
class Quoted:
    quote_id: uuid.UUID


@dataclass(frozen=True)
class Refused:
    quote_id: uuid.UUID
    code: str


@dataclass(frozen=True)
class Waiting:
    pass


type Attempt = Quoted | Refused | Waiting


@dataclass(frozen=True)
class NoSession:
    pass


@dataclass(frozen=True)
class SessionOver:
    """Expired or canceled: no more quotes."""


@dataclass(frozen=True)
class TooManyAddresses:
    pass


@dataclass(frozen=True)
class NotRefreshable:
    """Nothing to refresh: no address, or the quote still holds."""


type SubmitResult = Attempt | NoSession | SessionOver | TooManyAddresses
type RefreshResult = Attempt | NoSession | SessionOver | NotRefreshable


def _quote_request(
    session: CheckoutSession, country: str, now: datetime
) -> QuoteRequest:
    lines: list[QuoteLine] = []
    for line in session.lines:
        lines.append(
            QuoteLine(
                category=line.category,
                hs_code=line.customs.hs_code,
                origin=line.customs.origin_country,
                quantity=line.quantity,
                unit_price=line.unit_price,
            )
        )
    return QuoteRequest(
        destination=country,
        lines=tuple(lines),
        parcel=session.parcel,
        on=now.date(),
        now=now,
    )


async def _refuse(
    conn: Connection,
    session: CheckoutSession,
    address_id: uuid.UUID,
    load_id: uuid.UUID,
    refusal: Refusal,
    actor: Actor,
    now: datetime,
) -> Refused:
    quote_id = uuid.uuid7()
    await store_refused(
        conn,
        RefusedQuote(
            id=quote_id,
            session_id=session.id,
            address_id=address_id,
            load_id=load_id,
            made_at=now,
            code=refusal.code,
            message=refusal.message,
        ),
    )
    await set_session_state(
        conn, session.id, SessionState.REFUSED, address_id, quote_id
    )
    await append_fact(
        conn,
        QuoteRefusedV1(
            checkout_session_id=session.id, quote_id=quote_id, code=refusal.code
        ),
        actor,
        now,
    )
    return Refused(quote_id, refusal.code)


async def _wait(
    conn: Connection,
    session: CheckoutSession,
    address_id: uuid.UUID,
    asked: Asked,
) -> Waiting:
    for lookup_id in asked.duty_lookups:
        await conn.execute(
            """
            INSERT INTO quote_wait (session_id, address_id, duty_lookup_id)
            VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
            """,
            (session.id, address_id, lookup_id),
        )
    for lookup_id in asked.price_lookups:
        await conn.execute(
            """
            INSERT INTO quote_wait (session_id, address_id, shipping_price_lookup_id)
            VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
            """,
            (session.id, address_id, lookup_id),
        )
    await set_session_state(conn, session.id, SessionState.QUOTING, address_id, None)
    return Waiting()


def _inputs(quote: Quote, lookups_used: list[uuid.UUID]) -> QuoteInputs:
    """The answered duty lookups the quote read, and the carrier prices
    its options charge; sorted, as they are read back."""
    prices: set[uuid.UUID] = set()
    for option in quote.options:
        if option.live is not None:
            prices.add(option.live.lookup_id)
    return QuoteInputs(tuple(sorted(set(lookups_used))), tuple(sorted(prices)))


async def _attempt(
    conn: Connection,
    session: CheckoutSession,
    address_id: uuid.UUID,
    country: str,
    environment: Environment,
    actor: Actor,
    now: datetime,
) -> Attempt:
    """Build the quote for the session's address and decide: store it,
    store the refusal, or ask and wait. Under the session's lock."""
    loaded = await load_in_force(conn)
    assert loaded is not None, "an address is only taken with market data loaded"
    market = loaded.market
    request = _quote_request(session, country, now)
    for _ in range(MAX_BUILD_ROUNDS):
        lookups = await held_lookups(conn, market, request)
        prices = await held_prices(conn, market, country, request.parcel)
        result = build_quote(market, request, lookups, prices)
        match result:
            case Quote():
                offered = offered_in(result, environment)
                if isinstance(offered, Refusal):
                    return await _refuse(
                        conn, session, address_id, loaded.load_id, offered, actor, now
                    )
                answered: list[uuid.UUID] = []
                for lookup in lookups.values():
                    if lookup.state == LookupState.ANSWERED:
                        answered.append(lookup.id)
                quote_id = uuid.uuid7()
                made = stored_from(
                    offered,
                    quote_id,
                    session.id,
                    address_id,
                    loaded.load_id,
                    now,
                    now + QUOTE_VALID_FOR,
                    _inputs(offered, answered),
                )
                await store_made(conn, made)
                await set_session_state(
                    conn, session.id, SessionState.QUOTED, address_id, quote_id
                )
                await append_fact(
                    conn,
                    QuoteMadeV1(checkout_session_id=session.id, quote_id=quote_id),
                    actor,
                    now,
                )
                return Quoted(quote_id)
            case InputsMissing():
                asked = await ask_missing(conn, result, actor, now)
                if isinstance(asked, NoAccount):
                    refusal = Refusal(
                        code="pricing_unavailable",
                        message=f"pricing through {asked.name} is not set up here",
                        param=None,
                        ours=True,
                    )
                    return await _refuse(
                        conn, session, address_id, loaded.load_id, refusal, actor, now
                    )
                if await still_open(conn, asked):
                    return await _wait(conn, session, address_id, asked)
                # One finished meanwhile: build again with it.
            case _:
                return await _refuse(
                    conn,
                    session,
                    address_id,
                    loaded.load_id,
                    refusal_of(result, country),
                    actor,
                    now,
                )
    # Lookups kept finishing under this step: let the worker build again.
    await set_session_state(conn, session.id, SessionState.QUOTING, address_id, None)
    await append_fact(
        conn,
        QuoteInputsArrivedV1(checkout_session_id=session.id, address_id=address_id),
        actor,
        now,
    )
    return Waiting()


def _over(session: CheckoutSession, now: datetime) -> bool:
    return session.state not in LIVE_STATES or now >= session.expires_at


async def submit_address(
    conn: Connection,
    session_id: uuid.UUID,
    address: Address,
    environment: Environment,
    now: datetime,
) -> SubmitResult:
    """A buyer's address for the session, and its quote decided. Never
    commits."""
    session = await lock_session(conn, session_id)
    if session is None:
        return NoSession()
    if _over(session, now):
        return SessionOver()
    if await address_count(conn, session_id) >= MAX_ADDRESSES:
        return TooManyAddresses()
    address_id = await add_address(conn, session_id, address, now)
    attempt = await _attempt(
        conn, session, address_id, address.country, environment, FLOW, now
    )
    if isinstance(attempt, Waiting):
        await append_fact(
            conn,
            QuoteRequestedV1(checkout_session_id=session_id, address_id=address_id),
            FLOW,
            now,
        )
    return attempt


async def refresh_quote(
    conn: Connection, session_id: uuid.UUID, environment: Environment, now: datetime
) -> RefreshResult:
    """Quote the same address again once its quote no longer holds. Never
    commits."""
    session = await lock_session(conn, session_id)
    if session is None:
        return NoSession()
    if _over(session, now):
        return SessionOver()
    if session.state != SessionState.QUOTED or session.quote_id is None:
        return NotRefreshable()
    assert session.address_id is not None
    stored = await read_quote(conn, session.quote_id)
    if not isinstance(stored, MadeQuote) or now < stored.valid_until:
        return NotRefreshable()
    address = await read_address(conn, session.address_id)
    assert address is not None
    return await _attempt(
        conn, session, session.address_id, address.country, environment, FLOW, now
    )


async def notify_waiting(
    conn: Connection, fact: Fact, now: datetime
) -> ConsumerOutcome:
    """Consumer `quote_wait_notify`, on a lookup's outcome: tell each
    session waiting on it. Locks no session."""
    match fact:
        case (
            DutyRateFoundV1(duty_lookup_id=lookup_id)
            | DutyRateUnknownCodeV1(duty_lookup_id=lookup_id)
            | DutyRateNeedsPersonV1(duty_lookup_id=lookup_id)
        ):
            cur = await conn.execute(
                """
                SELECT DISTINCT session_id, address_id FROM quote_wait
                WHERE duty_lookup_id = %s ORDER BY session_id, address_id
                """,
                (lookup_id,),
            )
        case (
            ShippingPriceFoundV1(shipping_price_lookup_id=lookup_id)
            | ShippingPriceNotServedV1(shipping_price_lookup_id=lookup_id)
            | ShippingPriceNeedsPersonV1(shipping_price_lookup_id=lookup_id)
        ):
            cur = await conn.execute(
                """
                SELECT DISTINCT session_id, address_id FROM quote_wait
                WHERE shipping_price_lookup_id = %s ORDER BY session_id, address_id
                """,
                (lookup_id,),
            )
        case _:
            raise WrongFact(f"quote_wait_notify got {fact!r}")
    waiting = await cur.fetchall()
    for session_id, address_id in waiting:
        await append_fact(
            conn,
            QuoteInputsArrivedV1(checkout_session_id=session_id, address_id=address_id),
            FLOW,
            now,
        )
    if not waiting:
        return Stale()
    return Applied()


async def build_waiting(
    conn: Connection, fact: Fact, environment: Environment, now: datetime
) -> ConsumerOutcome:
    """Consumer `quote_build`: build again for a session still quoting the
    address the fact names. Anything else (a new address since, already
    quoted, expired) makes it a no-op, so a late or repeated fact is
    harmless."""
    match fact:
        case QuoteInputsArrivedV1(
            checkout_session_id=session_id, address_id=address_id
        ):
            pass
        case _:
            raise WrongFact(f"quote_build got {fact!r}")
    session = await lock_session(conn, session_id)
    if (
        session is None
        or session.state != SessionState.QUOTING
        or session.address_id != address_id
        or _over(session, now)
    ):
        return Stale()
    address = await read_address(conn, address_id)
    assert address is not None
    await _attempt(conn, session, address_id, address.country, environment, FLOW, now)
    return Applied()


async def expire_session(
    conn: Connection, fact: Fact, now: datetime
) -> ConsumerOutcome:
    """Consumer `checkout_session_expire`, delivered at `expires_at`: expire
    the session if it is still live and its time has come, decided under
    its lock."""
    match fact:
        case CheckoutSessionExpiryDueV1(checkout_session_id=session_id):
            pass
        case _:
            raise WrongFact(f"checkout_session_expire got {fact!r}")
    session = await lock_session(conn, session_id)
    if session is None or session.state not in LIVE_STATES:
        return Stale()
    if now < session.expires_at:
        # Delivered early (a clock behind): try again at the time.
        await append_fact(conn, fact, FLOW, now, deliver_at=session.expires_at)
        return Stale()
    await set_session_state(
        conn, session.id, SessionState.EXPIRED, session.address_id, session.quote_id
    )
    await append_fact(
        conn, CheckoutSessionExpiredV1(checkout_session_id=session.id), FLOW, now
    )
    return Applied()


async def end_payment(conn: Connection, fact: Fact, now: datetime) -> ConsumerOutcome:
    """Consumer `session_payment_ended`: an attempt ended without money
    taken, so the buyer may pay another way, or the session expires now if
    its time passed while it was paying. Decided under the session's
    lock."""
    match fact:
        case (
            PaymentDeclinedV1(checkout_session_id=session_id)
            | PaymentExpiredV1(checkout_session_id=session_id)
            | PaymentCancelledV1(checkout_session_id=session_id)
        ):
            pass
        case _:
            raise WrongFact(f"session_payment_ended got {fact!r}")
    session = await lock_session(conn, session_id)
    if session is None or session.state != SessionState.PAYING:
        return Stale()
    if now >= session.expires_at:
        await set_session_state(
            conn, session.id, SessionState.EXPIRED, session.address_id, session.quote_id
        )
        await append_fact(
            conn, CheckoutSessionExpiredV1(checkout_session_id=session.id), FLOW, now
        )
        return Applied()
    await set_session_state(
        conn, session.id, SessionState.QUOTED, session.address_id, session.quote_id
    )
    return Applied()

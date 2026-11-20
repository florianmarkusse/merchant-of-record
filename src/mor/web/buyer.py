"""The buyer's checkout page handlers (`/c/{session}`, docs/plan-quote.md
§5, 4d-2; paying, docs/plan-payments.md §4). The session link is its own
bearer token, for that cart only; there are no cookies. POSTs pass the
same-origin check, and answer with a 303 back to the page, or the page
with its errors.

The address is personal data: it is never logged.
"""

import uuid
from datetime import datetime
from urllib.parse import parse_qsl

import msgspec
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from mor.audit import Actor, ActorKind
from mor.checkout.addresses import ADDRESS_RULES, AddressErrors, parse_address
from mor.checkout.flow import (
    LIVE_STATES,
    NoSession,
    SessionOver,
    TooManyAddresses,
    refresh_quote,
    submit_address,
)
from mor.checkout.quotes import MadeQuote, RefusedQuote, read_quote
from mor.checkout.records import CheckoutSession, SessionState
from mor.checkout.sessions import read_address, session_by_id
from mor.config import Settings
from mor.db.connect import Connection, connect
from mor.markets.records import MarketSet, MarketStatus
from mor.markets.rules import Open, country_rules
from mor.markets.store import market_in_force
from mor.merchants.store import read_merchant
from mor.payments.attempts import Attempt, session_attempts
from mor.payments.flow import (
    AlreadyPaying,
    CancelAsked,
    NotCancellable,
    NotPayable,
    Paying,
    pay,
    pay_another_way,
)
from mor.psp.accounts import route_for
from mor.psp.port import PaymentMethod
from mor.public_ids import SESSION_PREFIX, parse_id
from mor.web.buyer_html import BuyerView, address_values, render_buyer_page
from mor.web.forms import read_form, same_origin


NOT_FOUND = Response("This checkout link is not valid.", status_code=404)
TOO_MANY = "This checkout has had too many addresses. Please start again from the shop."


def _session_id(request: Request) -> uuid.UUID | None:
    return parse_id(SESSION_PREFIX, request.path_params["session_id"])


# The buyer's own steps are recorded as the session link's: no account.
BUYER = Actor(ActorKind.SYSTEM, "buyer")


def _over(session: CheckoutSession, now: datetime) -> bool:
    """Shown as over even before the expiry step has run: the step decides
    the state under its lock, the page only reads the time. A session being
    paid or paid is never over."""
    if session.state in (SessionState.EXPIRED, SessionState.CANCELED):
        return True
    return session.state in LIVE_STATES and now >= session.expires_at


def status_text(session: CheckoutSession, attempt: Attempt | None) -> str:
    """What the waiting script compares: any change in it is news."""
    attempt_part = "-" if attempt is None else f"{attempt.id.hex}:{attempt.state}"
    quote_part = "-" if session.quote_id is None else session.quote_id.hex
    return f"{session.state}:{quote_part}:{attempt_part}"


async def _latest_attempt(conn: Connection, session_id: uuid.UUID) -> Attempt | None:
    attempts = await session_attempts(conn, session_id)
    return attempts[-1] if attempts else None


def countries_for(
    market: MarketSet, session: CheckoutSession, now: datetime
) -> frozenset[str]:
    """Countries this cart may be sent to: open for every line's category,
    and with an address rule."""
    found: set[str] = set()
    for rule in ADDRESS_RULES:
        open_for_all = True
        for line in session.lines:
            answer = country_rules(market, rule.country, line.category, now.date())
            if not isinstance(answer, Open):
                open_for_all = False
        if open_for_all:
            found.add(rule.country)
    return frozenset(found)


def _country_names(
    market: MarketSet, countries: frozenset[str]
) -> tuple[tuple[str, str], ...]:
    named: list[tuple[str, str]] = []
    for country in market.countries:
        if country.iso2 in countries and country.status == MarketStatus.OPEN:
            named.append((country.iso2, country.name))
    named.sort(key=lambda pair: pair[1])
    return tuple(named)


async def _view(
    conn: Connection,
    session: CheckoutSession,
    market: MarketSet,
    now: datetime,
    option_key: str,
    values: dict[str, str] | None = None,
    errors: AddressErrors | None = None,
    notice: str | None = None,
) -> BuyerView:
    merchant = await read_merchant(conn, session.merchant_id)
    assert merchant is not None
    quote: MadeQuote | None = None
    refused: RefusedQuote | None = None
    if session.quote_id is not None:
        stored = await read_quote(conn, session.quote_id)
        if isinstance(stored, MadeQuote):
            quote = stored
        elif isinstance(stored, RefusedQuote):
            refused = stored
    methods: tuple[PaymentMethod, ...] = ()
    if quote is not None:
        address = await read_address(conn, quote.address_id)
        assert address is not None
        routed = await route_for(conn, session.merchant_id, address.country, now)
        if routed is not None:
            methods = routed.account.methods
    if values is None:
        values = {}
        if session.address_id is not None:
            address = await read_address(conn, session.address_id)
            if address is not None:
                values = address_values(address)
    attempt = await _latest_attempt(conn, session.id)
    return BuyerView(
        session,
        f"{SESSION_PREFIX}{session.id.hex}",
        merchant.display_name,
        _country_names(market, countries_for(market, session, now)),
        values,
        errors or {},
        notice,
        quote,
        refused,
        option_key,
        now,
        attempt,
        methods,
        status_text(session, attempt),
    )


async def buyer_page(request: Request, settings: Settings, now: datetime) -> Response:
    session_id = _session_id(request)
    if session_id is None:
        return NOT_FOUND
    async with await connect(settings.database_url) as conn:
        session = await session_by_id(conn, session_id)
        if session is None:
            return NOT_FOUND
        if _over(session, now):
            return RedirectResponse(session.cancel_url, status_code=303)
        market = await market_in_force(conn)
        if market is None:
            return Response("Checkout is not available right now.", status_code=503)
        view = await _view(
            conn, session, market, now, request.query_params.get("option", "")
        )
    return HTMLResponse(render_buyer_page(view))


async def post_address(request: Request, settings: Settings, now: datetime) -> Response:
    if not same_origin(request):
        return Response("cross-site POST refused", status_code=403)
    session_id = _session_id(request)
    if session_id is None:
        return NOT_FOUND
    body = await request.body()
    try:
        values = dict(parse_qsl(body.decode("utf-8")))
    except UnicodeDecodeError:
        return Response("bad form", status_code=400)
    async with await connect(settings.database_url) as conn:
        session = await session_by_id(conn, session_id)
        if session is None:
            return NOT_FOUND
        if _over(session, now):
            return RedirectResponse(session.cancel_url, status_code=303)
        market = await market_in_force(conn)
        if market is None:
            return Response("Checkout is not available right now.", status_code=503)
        parsed = parse_address(values, countries_for(market, session, now))
        if isinstance(parsed, dict):
            view = await _view(conn, session, market, now, "", values, parsed)
            return HTMLResponse(render_buyer_page(view), status_code=400)
        result = await submit_address(
            conn, session_id, parsed, settings.environment, now
        )
        match result:
            case NoSession():
                await conn.rollback()
                return NOT_FOUND
            case SessionOver():
                await conn.rollback()
                return RedirectResponse(session.cancel_url, status_code=303)
            case TooManyAddresses():
                await conn.rollback()
                view = await _view(
                    conn, session, market, now, "", values, None, TOO_MANY
                )
                return HTMLResponse(render_buyer_page(view), status_code=429)
            case _:
                await conn.commit()
    return RedirectResponse(f"/c/{SESSION_PREFIX}{session_id.hex}", status_code=303)


async def post_refresh(request: Request, settings: Settings, now: datetime) -> Response:
    if not same_origin(request):
        return Response("cross-site POST refused", status_code=403)
    session_id = _session_id(request)
    if session_id is None:
        return NOT_FOUND
    async with await connect(settings.database_url) as conn:
        result = await refresh_quote(conn, session_id, settings.environment, now)
        if isinstance(result, NoSession):
            return NOT_FOUND
        await conn.commit()
    return RedirectResponse(f"/c/{SESSION_PREFIX}{session_id.hex}", status_code=303)


class PayForm(msgspec.Struct, frozen=True):
    option: str
    method: str


def _page_url(session_id: uuid.UUID) -> str:
    return f"/c/{SESSION_PREFIX}{session_id.hex}"


async def post_pay(request: Request, settings: Settings, now: datetime) -> Response:
    """Pay the shown option: an attempt is made and the page waits for the
    PSP. Every check is taken again under the session's lock."""
    if not same_origin(request):
        return Response("cross-site POST refused", status_code=403)
    session_id = _session_id(request)
    if session_id is None:
        return NOT_FOUND
    form = await read_form(request, PayForm)
    if form is None:
        return Response("bad form", status_code=400)
    page = _page_url(session_id)
    return_url = str(request.base_url).rstrip("/") + page
    async with await connect(settings.database_url) as conn:
        result = await pay(
            conn, session_id, form.option, form.method, return_url, BUYER, now
        )
        match result:
            case Paying():
                await conn.commit()
            case AlreadyPaying():
                await conn.rollback()  # a double submit: the page shows it
            case NotPayable(reason=reason):
                await conn.rollback()
                match reason:
                    case "no_session":
                        return NOT_FOUND
                    case "unknown_option" | "unknown_method":
                        return Response("bad form", status_code=400)
                    case "not_quoted" | "session_over" | "quote_lapsed" | "no_route":
                        pass  # the page shows why, as it now stands
    return RedirectResponse(page, status_code=303)


async def post_another_way(
    request: Request, settings: Settings, now: datetime
) -> Response:
    if not same_origin(request):
        return Response("cross-site POST refused", status_code=403)
    session_id = _session_id(request)
    if session_id is None:
        return NOT_FOUND
    async with await connect(settings.database_url) as conn:
        result = await pay_another_way(conn, session_id, BUYER, now)
        match result:
            case CancelAsked():
                await conn.commit()
            case NotCancellable():
                await conn.rollback()
    return RedirectResponse(_page_url(session_id), status_code=303)


async def get_status(request: Request, settings: Settings) -> Response:
    """For the waiting script: the page's state as one string."""
    session_id = _session_id(request)
    if session_id is None:
        return NOT_FOUND
    async with await connect(settings.database_url) as conn:
        session = await session_by_id(conn, session_id)
        if session is None:
            return NOT_FOUND
        attempt = await _latest_attempt(conn, session_id)
    return JSONResponse(
        {"status": status_text(session, attempt)},
        headers={"Cache-Control": "no-store"},
    )

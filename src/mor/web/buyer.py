"""The buyer's checkout page handlers (`/c/{session}`, docs/plan-quote.md
§5, 4d-2). The session link is its own bearer token, for that cart only;
there are no cookies. POSTs pass the same-origin check, and answer with a
303 back to the page, or the page with its errors.

The address is personal data: it is never logged.
"""

import uuid
from datetime import datetime
from urllib.parse import parse_qsl

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from mor.checkout.addresses import ADDRESS_RULES, AddressErrors, parse_address
from mor.checkout.flow import (
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
from mor.public_ids import SESSION_PREFIX, parse_id
from mor.web.buyer_html import BuyerView, address_values, render_buyer_page
from mor.web.forms import same_origin


NOT_FOUND = Response("This checkout link is not valid.", status_code=404)
TOO_MANY = "This checkout has had too many addresses. Please start again from the shop."


def _session_id(request: Request) -> uuid.UUID | None:
    return parse_id(SESSION_PREFIX, request.path_params["session_id"])


def _over(session: CheckoutSession, now: datetime) -> bool:
    """Shown as over even before the expiry step has run: the step decides
    the state under its lock, the page only reads the time."""
    return (
        session.state in (SessionState.EXPIRED, SessionState.CANCELED)
        or now >= session.expires_at
    )


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
    if values is None:
        values = {}
        if session.address_id is not None:
            address = await read_address(conn, session.address_id)
            if address is not None:
                values = address_values(address)
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

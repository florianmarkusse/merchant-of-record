"""The staff markets pages (docs/plan-quote.md §5b): what the market data
in force decides. Three pages under one menu, growing with slice 4:

- Quote (4b, 4c): an order (country, category, price, HS code, origin)
  and a parcel: every shipping option with each part of its price, and
  why the duties and taxes are what they are, from our own rows or the
  country's vendor; a button to ask the vendor about a product not yet
  asked, and one to ask a carrier for a live price (4c-2).
- Vendor answers (4b-2): how the vendor's answers compare with our rows,
  per country and charge, and each answer, differences first.
- Market data (4a): the loads, what the newest changed, and each
  researched country's answer for a category on a day.

    GET  /staff/markets?country=TH&goods=50.00&currency=USD&hs=610910
         &origin=VN&weight=600&length=300&width=250&height=50
    GET  /staff/markets/answers
    GET  /staff/markets/data?category=apparel&on=2026-11-17
    POST /staff/markets/duty_lookups      country, origin, hs, back
    POST /staff/markets/shipping_prices   country, service, weight, length,
                                          width, height, back

Mounted only outside prod until staff sign-in exists. The POSTs ask a
vendor or a carrier (through the outbox, never from the handler).
"""

from dataclasses import dataclass, replace
from datetime import date, datetime
from fractions import Fraction
from urllib.parse import urlencode

import msgspec
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from mor.audit import Actor, ActorKind
from mor.checkout.quotes import session_quotes
from mor.checkout.sessions import address_countries, recent_sessions, session_by_id
from mor.couriers.prices import (
    NoCourierAccount,
    courier_account,
    live_prices,
    request_shipping_price,
)
from mor.couriers.records import Parcel, PriceHeld, PriceQuestion
from mor.db.connect import Connection, connect
from mor.duties.lookups import (
    NoVendorAccount,
    agreement,
    read_lookup,
    request_duty_rate,
    vendor_account,
    vendor_answers,
)
from mor.duties.port import RateQuestion
from mor.duties.records import VendorLookup
from mor.markets.changes import describe_changes
from mor.markets.fx import FxMissing
from mor.markets.fx import rate as fx_rate
from mor.markets.records import (
    Country,
    DutyMode,
    MarketSet,
    MarketStatus,
    PriceSource,
)
from mor.markets.store import market_in_force, read_market, recent_loads
from mor.merchants.store import read_merchant
from mor.money.amount import InvalidAmount, Money, parse_major
from mor.money.currency import Currency
from mor.payments.attempts import session_attempts
from mor.psp.accounts import read_psp_account
from mor.public_ids import SESSION_PREFIX, parse_id
from mor.quote.build import (
    InputsMissing,
    LineRateRefused,
    PartKind,
    Quote,
    QuoteLine,
    QuoteRequest,
    QuoteResult,
    ShippingOption,
    build_quote,
)
from mor.quote.inputs import held_prices
from mor.quote.shipping import price_question, routes_to
from mor.tax.explain import Explained, OrderCheck, explain
from mor.tax.line_rates import RateNeeded, duty_source_for
from mor.web.forms import read_form, same_origin
from mor.web.staff_data_html import DataView, render_answers_page, render_data_page
from mor.web.staff_quote_html import (
    PAGE,
    Ask,
    QuoteView,
    chosen_option,
    render_quote_page,
)
from mor.web.staff_sessions_html import render_session_page, render_sessions_page


LOADS_SHOWN = 10
ANSWERS_SHOWN = 200
PERSON = Actor(ActorKind.STAFF, "staff-markets-page")
FIELDS = (
    "country",
    "category",
    "goods",
    "currency",
    "hs",
    "origin",
    "weight",
    "length",
    "width",
    "height",
    "option",  # the option (its route's key) explained; else the cheapest
)


def _day(text: str | None, default: date) -> date:
    if text is None:
        return default
    try:
        return date.fromisoformat(text)
    except ValueError:
        return default


@dataclass(frozen=True)
class OrderForm:
    """The quote form, read: the order check and the parcel, if given."""

    check: OrderCheck
    parcel: Parcel | None


def read_order(fields: dict[str, str]) -> OrderForm | dict[str, str] | None:
    """The form as an order, the errors per field, or None while the
    country or the price is still empty (marked only once Quote is pressed,
    see `_missing`)."""
    country = fields.get("country", "").strip().upper()
    goods_text = fields.get("goods", "").strip()
    if not country or not goods_text:
        return None
    errors: dict[str, str] = {}
    currency: Currency | None = None
    for option in Currency:
        if option.value == fields.get("currency", "USD"):
            currency = option
    if currency is None:
        errors["goods"] = "Choose a currency."
        currency = Currency.USD
    goods = parse_major(goods_text, currency)
    if isinstance(goods, InvalidAmount):
        errors["goods"] = f"Enter a price ({goods.reason.replace('_', ' ')})."
    hs_code = fields.get("hs", "").replace(".", "").replace(" ", "") or None
    if hs_code is not None and not hs_code.isdigit():
        errors["hs"] = "Digits only."
    origin = fields.get("origin", "").strip().upper() or "VN"
    parcel = _parcel(fields, errors)
    if errors or isinstance(goods, InvalidAmount):
        return errors
    category = fields.get("category", "apparel")
    check = OrderCheck(country, category, goods, None, hs_code, origin)
    return OrderForm(check, parcel)


def _missing(fields: dict[str, str], quote: QuoteResult | None) -> dict[str, str]:
    """What a quote still needs, per field: the country, the price and the
    parcel weight always, the HS code when the country's rates need one."""
    missing: dict[str, str] = {}
    if not fields.get("country", "").strip():
        missing["country"] = "Choose where it ships to."
    if not fields.get("goods", "").strip():
        missing["goods"] = "Enter the merchant's price."
    if not fields.get("weight", "").strip():
        missing["weight"] = "Enter the parcel's weight to see shipping."
    if isinstance(quote, LineRateRefused):
        match quote.problem:
            case RateNeeded(reason="no_hs_code"):
                missing["hs"] = "This country's rates need the HS code."
            case _:
                pass
    return missing


def _parcel(fields: dict[str, str], errors: dict[str, str]) -> Parcel | None:
    """The parcel, or None without a weight. A box left empty is 1 mm a
    side: no volumetric weight."""
    if not fields.get("weight", "").strip():
        return None
    sizes: list[int] = []
    for name in ("weight", "length", "width", "height"):
        text = fields.get(name, "").strip() or "1"
        if not text.isdigit() or int(text) == 0:
            # The box's three sizes share one field on the form.
            where = "weight" if name == "weight" else "length"
            errors[where] = "Whole numbers above zero."
            return None
        sizes.append(int(text))
    return Parcel(sizes[0], sizes[1], sizes[2], sizes[3])


def _researched(market: MarketSet | None) -> list[Country]:
    countries: list[Country] = []
    if market is None:
        return countries
    for country in market.countries:
        if country.status != MarketStatus.NOT_ASSESSED:
            countries.append(country)
    countries.sort(key=lambda country: country.name)
    return countries


async def quote_page(request: Request, database_url: str, now: datetime) -> Response:
    on = _day(request.query_params.get("on"), now.date())
    fields: dict[str, str] = {}
    for name in FIELDS:
        value = request.query_params.get(name)
        if value is not None:
            fields[name] = value
    order = read_order(fields)
    errors: dict[str, str] = {}
    if isinstance(order, dict):
        errors = order
    quote: QuoteResult | None = None
    references: tuple[tuple[Currency, Fraction], ...] = ()
    why: Explained | None = None
    ask: Ask | None = None
    no_account: set[str] = set()
    async with await connect(database_url) as conn:
        market = await market_in_force(conn)
        if isinstance(order, OrderForm) and market is not None:
            lookup = await _lookup_for(conn, market, order.check)
            check = order.check
            if order.parcel is not None:
                prices = await held_prices(conn, market, check.country, order.parcel)
                quote = _quote(market, check, order.parcel, on, now, lookup, prices)
                if isinstance(quote, InputsMissing):
                    for question in quote.price_asks:
                        if await courier_account(conn, question.carrier) is None:
                            no_account.add(question.carrier)
                if isinstance(quote, Quote):
                    # Explained with the shown option's freight, so a band
                    # on goods plus freight is decided as it quoted.
                    shown = chosen_option(quote, fields.get("option", ""))
                    check = replace(check, freight=_freight(shown))
                    references = _references(market, quote.currency)
            why = explain(market, check, on, lookup)
            if why.ask_vendor is not None:
                account = await vendor_account(conn, why.ask_vendor)
                ask = Ask(why.ask_vendor, account is not None)
    if "go" in request.query_params:
        # Quote was pressed: also mark what a quote still needs.
        errors = _missing(fields, quote) | errors
    categories: list[tuple[str, str]] = []
    if market is not None:
        for category in market.categories:
            categories.append((category.category, category.label))
    back = PAGE
    if request.url.query:
        back = f"{PAGE}?{request.url.query}"
    origins: list[Country] = []
    if market is not None:
        origins = sorted(market.countries, key=lambda country: country.name)
    view = QuoteView(
        _researched(market),
        origins,
        categories,
        fields,
        errors,
        on,
        market is not None,
        quote,
        why,
        ask,
        "asked" in request.query_params or "priced" in request.query_params,
        back,
        references,
        frozenset(no_account),
    )
    return HTMLResponse(render_quote_page(view))


async def answers_page(database_url: str, now: datetime) -> Response:
    async with await connect(database_url) as conn:
        rows = await agreement(conn)
        answers = await vendor_answers(conn, ANSWERS_SHOWN)
        prices = await live_prices(conn, ANSWERS_SHOWN)
    return HTMLResponse(render_answers_page(rows, answers, prices, now))


async def data_page(request: Request, database_url: str, now: datetime) -> Response:
    on = _day(request.query_params.get("on"), now.date())
    category = request.query_params.get("category", "apparel")
    async with await connect(database_url) as conn:
        loads = await recent_loads(conn, LOADS_SHOWN)
        market: MarketSet | None = None
        changes: list[str] = []
        if loads:
            market = await read_market(conn, loads[0].id)
        if len(loads) > 1 and market is not None:
            previous = await read_market(conn, loads[1].id)
            changes = describe_changes(previous, market)
    view = DataView(loads, market, changes, category, on)
    return HTMLResponse(render_data_page(view))


# Shown beside a quote's own currency, for reading (asked 18-Nov-2026).
REFERENCE_CURRENCIES = (Currency.USD, Currency.EUR)


def _references(
    market: MarketSet, currency: Currency
) -> tuple[tuple[Currency, Fraction], ...]:
    """Units of each reference currency per unit of the quote's, where the
    load has a confirmed rate; none for the quote's own currency."""
    found: list[tuple[Currency, Fraction]] = []
    for reference in REFERENCE_CURRENCIES:
        if reference == currency:
            continue
        per_unit = fx_rate(market, currency, reference)
        if not isinstance(per_unit, FxMissing):
            found.append((reference, per_unit))
    return tuple(found)


def _freight(option: ShippingOption) -> Money:
    """What customs counts as the freight: freight, surcharges and fuel."""
    total = 0
    currency = option.total.currency
    for part in option.parts:
        if part.kind in (PartKind.FREIGHT, PartKind.SURCHARGE, PartKind.FUEL):
            total += part.amount.amount_minor
    return Money(total, currency)


def _quote(
    market: MarketSet,
    order: OrderCheck,
    parcel: Parcel,
    on: date,
    now: datetime,
    lookup: VendorLookup | None,
    prices: dict[PriceQuestion, PriceHeld],
) -> QuoteResult:
    """The order as one line of one item, quoted."""
    line = QuoteLine(order.category, order.hs_code, order.origin, 1, order.goods)
    request = QuoteRequest(order.country, (line,), parcel, on, now)
    lookups: dict[RateQuestion, VendorLookup] = {}
    if lookup is not None and order.hs_code is not None:
        lookups[RateQuestion(order.country, order.origin, order.hs_code)] = lookup
    return build_quote(market, request, lookups, prices)


async def _lookup_for(
    conn: Connection, market: MarketSet, order: OrderCheck
) -> VendorLookup | None:
    source = duty_source_for(market, order.country)
    if source.mode == DutyMode.OWN_ONLY or order.hs_code is None:
        return None
    assert source.vendor is not None
    question = RateQuestion(order.country, order.origin, order.hs_code)
    return await read_lookup(conn, source.vendor, question)


class AskForm(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    country: str
    origin: str
    hs: str
    back: str


async def ask_vendor(request: Request, database_url: str, now: datetime) -> Response:
    """Ask the country's vendor about one combination, once: the lookup and
    its `duty_rate_needed` commit together, and the worker asks."""
    if not same_origin(request):
        return Response("cross-site POST refused", status_code=403)
    form = await read_form(request, AskForm)
    if form is None or not form.back.startswith(PAGE):
        return Response("bad form", status_code=400)
    country = form.country.strip().upper()
    origin = form.origin.strip().upper() or "VN"
    hs_code = form.hs.replace(".", "").replace(" ", "")
    if not hs_code.isdigit() or not 4 <= len(hs_code) <= 10:
        return Response("HS code: 4 to 10 digits", status_code=400)
    async with await connect(database_url) as conn:
        market = await market_in_force(conn)
        if market is None:
            return Response("no market data loaded", status_code=409)
        source = duty_source_for(market, country)
        if source.vendor is None:
            return Response(f"{country} has no vendor", status_code=409)
        asked = await request_duty_rate(
            conn,
            source.vendor,
            RateQuestion(country, origin, hs_code),
            PERSON,
            now,
        )
        if isinstance(asked, NoVendorAccount):
            return Response(f"no account for {asked.vendor}", status_code=409)
        await conn.commit()
    separator = "&" if "?" in form.back else "?"
    note = urlencode({"asked": str(asked)})
    return RedirectResponse(f"{form.back}{separator}{note}", status_code=303)


class PriceForm(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    country: str
    service: str
    weight: str
    length: str
    width: str
    height: str
    back: str


async def ask_carrier(request: Request, database_url: str, now: datetime) -> Response:
    """Ask a carrier for a live price for one service, lane and parcel,
    unless a usable one is held or a request is open: the lookup and its
    `shipping_price_needed` commit together, and the worker asks."""
    if not same_origin(request):
        return Response("cross-site POST refused", status_code=403)
    form = await read_form(request, PriceForm)
    if form is None or not form.back.startswith(PAGE):
        return Response("bad form", status_code=400)
    sizes: list[int] = []
    for text in (form.weight, form.length, form.width, form.height):
        if not text.isdigit() or int(text) == 0:
            return Response("parcel: whole numbers above zero", status_code=400)
        sizes.append(int(text))
    parcel = Parcel(sizes[0], sizes[1], sizes[2], sizes[3])
    country = form.country.strip().upper()
    async with await connect(database_url) as conn:
        market = await market_in_force(conn)
        if market is None:
            return Response("no market data loaded", status_code=409)
        question: PriceQuestion | None = None
        for route in routes_to(market, country):
            if route.source != PriceSource.CARRIER_API or route.service != form.service:
                continue
            for service in market.services:
                if service.code == route.service:
                    question = price_question(service, country, parcel)
        if question is None:
            return Response(
                f"{form.service} is not priced live into {country}", status_code=409
            )
        asked = await request_shipping_price(conn, question, PERSON, now)
        if isinstance(asked, NoCourierAccount):
            return Response(f"no account for {asked.carrier}", status_code=409)
        await conn.commit()
    separator = "&" if "?" in form.back else "?"
    note = urlencode({"priced": str(asked)})
    return RedirectResponse(f"{form.back}{separator}{note}", status_code=303)


SESSIONS_SHOWN = 100


async def sessions_page(database_url: str, now: datetime) -> Response:
    async with await connect(database_url) as conn:
        rows = await recent_sessions(conn, SESSIONS_SHOWN)
    return HTMLResponse(render_sessions_page(rows, now))


async def session_page(request: Request, database_url: str) -> Response:
    session_id = parse_id(SESSION_PREFIX, request.path_params["session_id"])
    if session_id is None:
        return Response("no such session", status_code=404)
    async with await connect(database_url) as conn:
        session = await session_by_id(conn, session_id)
        if session is None:
            return Response("no such session", status_code=404)
        merchant = await read_merchant(conn, session.merchant_id)
        assert merchant is not None
        countries = await address_countries(conn, session_id)
        quotes = await session_quotes(conn, session_id)
        attempts = await session_attempts(conn, session_id)
        account_names: dict[str, str] = {}
        for attempt in attempts:
            account = await read_psp_account(conn, attempt.psp_account_id)
            assert account is not None
            account_names[account.id.hex] = account.name
    return HTMLResponse(
        render_session_page(
            session, merchant.display_name, countries, quotes, attempts, account_names
        )
    )

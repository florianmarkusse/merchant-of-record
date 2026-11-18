"""The staff markets page (docs/plan-quote.md §5b): what the market data in
force decides. It grows with each step of slice 4:

- 4a: the loads, what the newest one changed, and each researched
  country's answer for a category on a day (open with the rates in force,
  or closed with the reason);
- 4b: a check of one order (country, category, goods value, freight, HS
  code, origin): both tax paths, the row that decided each, and the rates,
  from our own rows or the country's vendor, with the comparison; a button
  to ask the vendor about a combination not yet asked; and how the
  vendor's answers compare with our rows per country.

    GET  /staff/markets?category=apparel&on=2026-11-17
    GET  /staff/markets?country=TH&goods=50.00&currency=USD&hs=610910&origin=VN
    POST /staff/markets/duty_lookups   country, origin, hs, back

Mounted only outside prod until staff sign-in exists. The one POST asks a
vendor (through the outbox, never from the handler).
"""

from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlencode

import htpy
import msgspec
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from mor.audit import Actor, ActorKind
from mor.dates import format_day, format_moment
from mor.db.connect import Connection, connect
from mor.duties.lookups import (
    Agreement,
    NoVendorAccount,
    agreement,
    read_lookup,
    request_duty_rate,
    vendor_account,
)
from mor.duties.port import RateQuestion
from mor.duties.records import VendorLookup
from mor.markets.changes import describe_changes, value_text
from mor.markets.records import DutyMode, LandedRate, MarketSet, MarketStatus
from mor.markets.rules import (
    Closed,
    NotListed,
    NotOpen,
    Open,
    RatesMissing,
    RulesNotConfirmed,
    country_rules,
)
from mor.markets.store import LoadRow, read_market, recent_loads
from mor.money.amount import InvalidAmount, Money, parse_major
from mor.money.currency import Currency
from mor.tax.explain import OrderCheck, explain
from mor.tax.line_rates import duty_source_for
from mor.web.forms import read_form, same_origin


LOADS_SHOWN = 10
PAGE = "/staff/markets"
PERSON = Actor(ActorKind.STAFF, "staff-markets-page")


@dataclass(frozen=True)
class Ask:
    """The order check's vendor still to be asked, and whether this
    environment has an account for it."""

    vendor: str
    has_account: bool


@dataclass(frozen=True)
class View:
    loads: list[LoadRow]
    market: MarketSet | None
    changes: list[str]
    category: str
    on: date
    fields: dict[str, str]  # the order check's fields as entered
    check: list[str] | None  # what the order check says, once asked
    ask: Ask | None
    agreement: list[Agreement]
    back: str  # this page's URL, to come back to after a POST


def _answer_text(answer: Open | Closed) -> str:
    match answer:
        case Open():
            return "open"
        case NotListed():
            return "closed: not listed"
        case NotOpen(status=status, note=note):
            if note:
                return f"{status.value}: {note}"
            return status.value
        case RulesNotConfirmed(line_nos=line_nos):
            lines = ", ".join(str(line_no) for line_no in line_nos)
            return f"closed: rules not confirmed (landed_rates.csv lines {lines})"
        case RatesMissing(category=category):
            return f"closed: no import tax recorded for {category}"


def _rate_text(rate: LandedRate) -> str:
    place = rate.scope.country or rate.scope.group or ""
    band = rate.band.value
    if rate.limit is not None:
        band = f"{band} {value_text(rate.limit)}"
    started = ""
    if rate.effective_from is not None:
        started = f", from {format_day(rate.effective_from)}"
    return (
        f"{rate.charge.value}: {value_text(rate.amount)}, {rate.collector.value},"
        f" {band} [{place}, {rate.status.value}{started}]"
    )


def _country_rows(view: View) -> list[htpy.Node]:
    rows: list[htpy.Node] = []
    if view.market is None:
        return rows
    for country in view.market.countries:
        if country.status == MarketStatus.NOT_ASSESSED:
            continue
        answer = country_rules(view.market, country.iso2, view.category, view.on)
        rates: list[htpy.Node] = []
        if isinstance(answer, Open):
            for candidates in answer.candidates.values():
                for rate in candidates:
                    rates.append(htpy.li[_rate_text(rate)])
        rows.append(
            htpy.tr[
                htpy.td[htpy.code[country.iso2]],
                htpy.td[country.name],
                htpy.td[_answer_text(answer)],
                htpy.td[htpy.ul[rates] if rates else ""],
            ]
        )
    return rows


def _filter_form(view: View) -> htpy.Element:
    options: list[htpy.Node] = []
    if view.market is not None:
        for category in view.market.categories:
            options.append(
                htpy.option(
                    value=category.category,
                    selected=category.category == view.category,
                )[category.label]
            )
    return htpy.form(method="get", action="/staff/markets")[
        htpy.label["Category ", htpy.select(name="category")[options]],
        " ",
        htpy.label[
            "on ", htpy.input(type="date", name="on", value=view.on.isoformat())
        ],
        " ",
        htpy.button(type="submit")["show"],
    ]


def _loads_table(loads: list[LoadRow]) -> htpy.Node:
    if not loads:
        return htpy.p["No loads yet: run `python -m mor.markets.load --write`."]
    rows: list[htpy.Node] = []
    for index, load in enumerate(loads):
        rows.append(
            htpy.tr[
                htpy.td[format_moment(load.loaded_at)],
                htpy.td[htpy.code[load.git_commit]],
                htpy.td[htpy.code[str(load.id)]],
                htpy.td["in force" if index == 0 else ""],
            ]
        )
    return htpy.table[
        htpy.tr[htpy.th["loaded"], htpy.th["commit"], htpy.th["load"], htpy.th[""]],
        rows,
    ]


def _check_form(view: View) -> htpy.Element:
    def field(name: str, label: str, size: int, placeholder: str = "") -> htpy.Node:
        return htpy.label[
            f"{label} ",
            htpy.input(
                name=name,
                value=view.fields.get(name, ""),
                size=size,
                placeholder=placeholder,
            ),
        ]

    currencies: list[htpy.Node] = []
    chosen = view.fields.get("currency", "USD")
    for currency in Currency:
        currencies.append(
            htpy.option(value=currency.value, selected=currency.value == chosen)[
                currency.value
            ]
        )
    return htpy.form(method="get", action="/staff/markets")[
        htpy.input(type="hidden", name="category", value=view.category),
        htpy.input(type="hidden", name="on", value=view.on.isoformat()),
        field("country", "Country", 3, "TH"),
        " ",
        field("goods", "goods", 9, "50.00"),
        " ",
        htpy.select(name="currency")[currencies],
        " ",
        field("freight", "freight", 9, "optional"),
        " ",
        field("hs", "HS code", 10, "610910"),
        " ",
        field("origin", "origin", 3, "VN"),
        " ",
        htpy.button(type="submit")["check"],
    ]


def _check_result(view: View) -> htpy.Node:
    if view.check is None:
        return htpy.p[
            "Enter an order to see who collects the VAT/GST, whether duty is due,"
            " and at which rates. The category and date are those above."
        ]
    items: list[htpy.Node] = []
    for line in view.check:
        items.append(htpy.li[line])
    return [htpy.ul[items], _ask_form(view)]


def _ask_form(view: View) -> htpy.Node:
    if view.ask is None:
        return None
    if not view.ask.has_account:
        return htpy.p[
            f"No account for {view.ask.vendor} in this environment: it cannot be asked."
        ]
    return htpy.form(method="post", action=f"{PAGE}/duty_lookups")[
        htpy.input(type="hidden", name="country", value=view.fields.get("country", "")),
        htpy.input(type="hidden", name="origin", value=view.fields.get("origin", "")),
        htpy.input(type="hidden", name="hs", value=view.fields.get("hs", "")),
        htpy.input(type="hidden", name="back", value=view.back),
        htpy.button(type="submit")[f"Ask {view.ask.vendor}"],
    ]


def _agreement_table(rows: list[Agreement]) -> htpy.Node:
    if not rows:
        return htpy.p["No vendor answers yet."]
    lines: list[htpy.Node] = []
    for row in rows:
        lines.append(
            htpy.tr[
                htpy.td[htpy.code[row.destination]],
                htpy.td[row.charge.value],
                htpy.td[str(row.matched)],
                htpy.td[str(row.differed)],
                htpy.td[str(row.own_none)],
                htpy.td[str(row.not_a_rate)],
            ]
        )
    return htpy.table[
        htpy.tr[
            htpy.th["country"],
            htpy.th["charge"],
            htpy.th["agreed"],
            htpy.th["differed"],
            htpy.th["we had no row"],
            htpy.th["not a plain rate"],
        ],
        lines,
    ]


def render(view: View) -> str:
    changes: htpy.Node = htpy.p["The first load: nothing to compare with."]
    if len(view.loads) > 1:
        items: list[htpy.Node] = []
        for line in view.changes:
            items.append(htpy.li[htpy.code[line]])
        changes = htpy.ul[items] if items else htpy.p["No differences."]
    page = htpy.html(lang="en")[
        htpy.head[htpy.meta(charset="utf-8"), htpy.title["Staff: markets"]],
        htpy.body[
            htpy.h1["Markets"],
            htpy.p[
                "What the market data in force decides. Edit docs/markets/ and"
                " run `python -m mor.markets.load` to change it."
            ],
            htpy.h2["Loads"],
            _loads_table(view.loads),
            htpy.h2["What the newest load changed"],
            changes,
            htpy.h2[f"Check an order ({view.category}, {format_day(view.on)})"],
            _check_form(view),
            _check_result(view),
            htpy.h2["Vendor answers against our own rows"],
            htpy.p[
                "Per country and charge, every answer a vendor gave, compared"
                " with our own row in force when it came. A country moves from"
                " vendor to own_first when its rows hold up here."
            ],
            _agreement_table(view.agreement),
            htpy.h2[f"Countries on {format_day(view.on)}"],
            _filter_form(view),
            htpy.p[
                "Researched countries only. Open means a quote may be made;"
                " the rates are those in force, most specific first. Which"
                " band applies depends on the order's value (step 4b)."
            ],
            htpy.table[
                htpy.tr[
                    htpy.th["code"],
                    htpy.th["country"],
                    htpy.th["answer"],
                    htpy.th["rates in force"],
                ],
                _country_rows(view),
            ],
        ],
    ]
    return str(page)


def _day(text: str | None, default: date) -> date:
    if text is None:
        return default
    try:
        return date.fromisoformat(text)
    except ValueError:
        return default


def _order_check(fields: dict[str, str], category: str) -> OrderCheck | str | None:
    """The order check from the form, an error to show, or None when not
    asked."""
    country = fields.get("country", "").strip().upper()
    if not country:
        return None
    currency: Currency | None = None
    for option in Currency:
        if option.value == fields.get("currency", ""):
            currency = option
    if currency is None:
        return "Choose a currency."
    goods = parse_major(fields.get("goods", "").strip(), currency)
    if isinstance(goods, InvalidAmount):
        return f"Goods value: {goods.reason.replace('_', ' ')}."
    freight: Money | None = None
    if fields.get("freight", "").strip():
        parsed = parse_major(fields["freight"].strip(), currency)
        if isinstance(parsed, InvalidAmount):
            return f"Freight: {parsed.reason.replace('_', ' ')}."
        freight = parsed
    hs_code = fields.get("hs", "").replace(".", "").replace(" ", "") or None
    if hs_code is not None and not hs_code.isdigit():
        return "HS code: digits only."
    origin = fields.get("origin", "").strip().upper() or "VN"
    return OrderCheck(country, category, goods, freight, hs_code, origin)


async def show(request: Request, database_url: str, now: datetime) -> Response:
    on = _day(request.query_params.get("on"), now.date())
    category = request.query_params.get("category", "apparel")
    fields: dict[str, str] = {}
    for name in ("country", "goods", "currency", "freight", "hs", "origin"):
        value = request.query_params.get(name)
        if value is not None:
            fields[name] = value
    order = _order_check(fields, category)
    check: list[str] | None = None
    ask: Ask | None = None
    async with await connect(database_url) as conn:
        loads = await recent_loads(conn, LOADS_SHOWN)
        market: MarketSet | None = None
        changes: list[str] = []
        if loads:
            market = await read_market(conn, loads[0].id)
        if len(loads) > 1 and market is not None:
            previous = await read_market(conn, loads[1].id)
            changes = describe_changes(previous, market)
        rows = await agreement(conn)
        if isinstance(order, str):
            check = [order]
        elif order is not None and market is None:
            check = ["No market data loaded yet."]
        elif order is not None and market is not None:
            lookup = await _lookup_for(conn, market, order)
            explained = explain(market, order, on, lookup)
            check = explained.lines
            if explained.ask_vendor is not None:
                account = await vendor_account(conn, explained.ask_vendor)
                ask = Ask(explained.ask_vendor, account is not None)
    back = PAGE
    if request.url.query:
        back = f"{PAGE}?{request.url.query}"
    view = View(loads, market, changes, category, on, fields, check, ask, rows, back)
    return HTMLResponse(render(view))


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
        load = await recent_loads(conn, 1)
        if not load:
            return Response("no market data loaded", status_code=409)
        market = await read_market(conn, load[0].id)
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

"""The staff markets page (docs/plan-quote.md §5b): what the market data in
force decides. It grows with each step of slice 4; this is step 4a: the
loads, what the newest one changed, and each researched country's answer
for a category on a day (open with the rates in force, or closed with the
reason).

    GET /staff/markets?category=apparel&on=2026-11-17

Read-only. Mounted only outside prod until staff sign-in exists.
"""

from dataclasses import dataclass
from datetime import date, datetime

import htpy
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from mor.dates import format_day, format_moment
from mor.db.connect import connect
from mor.markets.changes import describe_changes, value_text
from mor.markets.records import LandedRate, MarketSet, MarketStatus
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


LOADS_SHOWN = 10


@dataclass(frozen=True)
class View:
    loads: list[LoadRow]
    market: MarketSet | None
    changes: list[str]
    category: str
    on: date


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


async def show(request: Request, database_url: str, now: datetime) -> Response:
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
    return HTMLResponse(render(View(loads, market, changes, category, on)))

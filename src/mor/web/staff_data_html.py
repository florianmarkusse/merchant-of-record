"""The staff pages beside the quote (docs/plan-quote.md §5b): how vendor
answers compare with our own rows, and the market data in force (the
loads, what the newest changed, each researched country's answer).
`ts/table_filter.ts` adds a filter over their tables; without it every
row shows."""

from dataclasses import dataclass
from datetime import date

import htpy

from mor.dates import format_day, format_moment
from mor.duties.lookups import Agreement, AnswerRow
from mor.markets.changes import percent_text, value_text
from mor.markets.records import LandedRate, MarketSet, MarketStatus
from mor.markets.rules import Open, country_rules
from mor.markets.store import LoadRow
from mor.web.staff_layout import day_input, staff_page
from mor.web.staff_quote_html import closed_text


ANSWERS_PAGE = "/staff/markets/answers"
DATA_PAGE = "/staff/markets/data"

COMPARISON_WORDS = {
    "differs": "differs",
    "not_a_rate": "not a plain rate",
    "own_none": "we had no row",
    "agrees": "agrees",
}


def _filters(table_id: str, only_class: str, only_label: str) -> htpy.Element:
    """A text filter and a "only these rows" box over a table; hidden until
    the script shows them, since they need it."""
    return htpy.div(class_="filters", hidden=True, data_filters=table_id)[
        htpy.input(type="search", placeholder="Filter rows", aria_label="Filter rows"),
        htpy.label[htpy.input(type="checkbox", value=only_class), only_label],
        htpy.span(class_="muted", data_count=""),
    ]


# --- vendor answers ------------------------------------------------------------


def _agreement_table(rows: list[Agreement]) -> htpy.Node:
    if not rows:
        return htpy.p(class_="muted")["No vendor answers yet."]
    lines: list[htpy.Node] = []
    for row in rows:
        lines.append(
            htpy.tr[
                htpy.td[htpy.code[row.destination]],
                htpy.td[row.charge.value],
                htpy.td(class_="num")[str(row.matched)],
                htpy.td(class_="num")[str(row.differed)],
                htpy.td(class_="num")[str(row.own_none)],
                htpy.td(class_="num")[str(row.not_a_rate)],
            ]
        )
    return htpy.div(class_="scroll")[
        htpy.table(class_="grid")[
            htpy.tr[
                htpy.th["Country"],
                htpy.th["Charge"],
                htpy.th(class_="num")["Agreed"],
                htpy.th(class_="num")["Differed"],
                htpy.th(class_="num")["We had no row"],
                htpy.th(class_="num")["Not a plain rate"],
            ],
            lines,
        ]
    ]


def _answers_table(rows: list[AnswerRow]) -> htpy.Node:
    if not rows:
        return None
    lines: list[htpy.Node] = []
    for row in rows:
        ours = "none"
        if row.own_rate is not None and row.own_line_no is not None:
            ours = f"{percent_text(row.own_rate)} (hs_rates.csv line {row.own_line_no})"
        said = row.formula
        if row.note is not None:
            said = f"{said}; note: {row.note}"
        lines.append(
            htpy.tr(class_=row.comparison)[
                htpy.td[htpy.code[row.destination]],
                htpy.td[row.origin],
                htpy.td[htpy.code[row.hs_code]],
                htpy.td[row.charge.value],
                htpy.td[f"{row.vendor}: {said}"],
                htpy.td[ours],
                htpy.td[COMPARISON_WORDS[row.comparison]],
                htpy.td[format_moment(row.answered_at)],
            ]
        )
    return [
        _filters("answers", "differs", "differences only"),
        htpy.div(class_="scroll")[
            htpy.table(id="answers", class_="grid")[
                htpy.tr[
                    htpy.th["Country"],
                    htpy.th["Origin"],
                    htpy.th["HS code"],
                    htpy.th["Charge"],
                    htpy.th["Vendor said"],
                    htpy.th["Our row"],
                    htpy.th[""],
                    htpy.th["Answered"],
                ],
                lines,
            ]
        ],
    ]


def render_answers_page(agreement: list[Agreement], answers: list[AnswerRow]) -> str:
    return staff_page(
        "Vendor answers",
        ANSWERS_PAGE,
        ("table_filter.js",),
        htpy.h1["Vendor answers against our own rows"],
        htpy.p(class_="muted")[
            "Every rate a duty vendor gave, beside our own row in force when"
            " it came. A country moves from vendor to own_first when its rows"
            " hold up here."
        ],
        htpy.h2["Per country and charge"],
        _agreement_table(agreement),
        htpy.h2["Each answer, differences first"],
        _answers_table(answers) or htpy.p(class_="muted")["None yet."],
    )


# --- market data -----------------------------------------------------------------


@dataclass(frozen=True)
class DataView:
    loads: list[LoadRow]
    market: MarketSet | None
    changes: list[str]
    category: str
    on: date


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
                htpy.td[htpy.span(class_="tag good")["in force"] if index == 0 else ""],
            ]
        )
    return htpy.div(class_="scroll")[
        htpy.table(class_="grid")[
            htpy.tr[htpy.th["Loaded"], htpy.th["Commit"], htpy.th["Load"], htpy.th[""]],
            rows,
        ]
    ]


def _changes(view: DataView) -> htpy.Node:
    if len(view.loads) < 2:
        return htpy.p(class_="muted")["The first load: nothing to compare with."]
    if not view.changes:
        return htpy.p(class_="muted")["No differences."]
    items: list[htpy.Node] = []
    for line in view.changes:
        items.append(htpy.li[htpy.code[line]])
    return htpy.details[
        htpy.summary[f"{len(view.changes)} changes against the load before"],
        htpy.ul[items],
    ]


def _country_rows(view: DataView) -> list[htpy.Node]:
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
            said: htpy.Node = htpy.span(class_="tag good")["open"]
        else:
            said = closed_text(answer)
        rows.append(
            htpy.tr(class_="open" if isinstance(answer, Open) else "closed")[
                htpy.td[htpy.code[country.iso2]],
                htpy.td[country.name],
                htpy.td[said],
                htpy.td[htpy.ul[rates] if rates else ""],
            ]
        )
    return rows


def _filter_form(view: DataView) -> htpy.Element:
    options: list[htpy.Node] = []
    if view.market is not None:
        for category in view.market.categories:
            options.append(
                htpy.option(
                    value=category.category,
                    selected=category.category == view.category,
                )[category.label]
            )
    return htpy.form(method="get", action=DATA_PAGE, class_="filters")[
        htpy.label["Category ", htpy.select(name="category")[options]],
        htpy.label["on ", day_input(view.on)],
        htpy.button(type="submit")["Show"],
    ]


def render_data_page(view: DataView) -> str:
    return staff_page(
        "Market data",
        DATA_PAGE,
        ("table_filter.js",),
        htpy.h1["Market data in force"],
        htpy.p(class_="muted")[
            "Edit docs/markets/ and run `python -m mor.markets.load --write`"
            " to change it."
        ],
        htpy.h2["Loads"],
        _loads_table(view.loads),
        htpy.h2["What the newest load changed"],
        _changes(view),
        htpy.h2[f"Countries on {format_day(view.on)}"],
        _filter_form(view),
        htpy.p(class_="muted")[
            "Researched countries only. Open means a quote may be made; the"
            " rates are those in force, most specific first."
        ],
        _filters("countries", "open", "open only"),
        htpy.div(class_="scroll")[
            htpy.table(id="countries", class_="grid")[
                htpy.tr[
                    htpy.th["Code"],
                    htpy.th["Country"],
                    htpy.th["Answer"],
                    htpy.th["Rates in force"],
                ],
                _country_rows(view),
            ]
        ],
    )

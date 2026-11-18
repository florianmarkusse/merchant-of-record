"""The staff quote page's HTML (docs/plan-quote.md §5b): the order form,
then each shipping option as a card with its total, a summary and every
part, and why the duties and taxes are what they are. Typed functions
from the view to markup; the handler is `staff_markets.quote_page`.

`ts/staff_quote.ts` re-quotes as the form changes, by fetching this same
page and swapping `#result`; without it the form's button does the same."""

from dataclasses import dataclass
from datetime import date
from fractions import Fraction

import htpy

from mor.dates import format_day
from mor.markets.records import Collector, Country, Standing
from mor.markets.rules import (
    Closed,
    NotListed,
    NotOpen,
    RatesMissing,
    RulesNotConfirmed,
)
from mor.money.amount import Money
from mor.money.currency import Currency, exponent
from mor.money.rounding import RoundingMode, round_fraction
from mor.quote.build import (
    CountryClosed,
    InputsMissing,
    LineRateProblem,
    LineRateRefused,
    NoCurrency,
    NoFeeRule,
    NoServiceForLane,
    Part,
    PartKind,
    Quote,
    QuoteResult,
    ShippingOption,
)
from mor.quote.shipping import NotOffered, NotOfferedReason
from mor.tax.line_rates import (
    RateNeeded,
    RateNotConfirmed,
    VendorNeedsPerson,
    VendorUnknownCode,
)
from mor.web.staff_layout import day_input, money_text, staff_page


PAGE = "/staff/markets"


@dataclass(frozen=True)
class Ask:
    """The vendor still to be asked about the order's product, and whether
    this environment has an account for it."""

    vendor: str
    has_account: bool


@dataclass(frozen=True)
class QuoteView:
    countries: list[Country]  # researched ones, for the country list
    origins: list[Country]  # every country in the file, for "Made in"
    categories: list[tuple[str, str]]  # (category, label)
    fields: dict[str, str]  # the form as entered
    errors: dict[str, str]  # per field: what is wrong with it
    on: date
    loaded: bool  # whether any market data is loaded
    quote: QuoteResult | None
    why: list[str] | None  # the tax explanation, once a country is given
    ask: Ask | None
    asked: bool  # the vendor was just asked from this page
    back: str  # this page's URL, to come back to after a POST
    # Units of each reference currency per unit of the quote's currency,
    # from the load's exchange rates: amounts shown beside the quote's own,
    # for reading only.
    references: tuple[tuple[Currency, Fraction], ...]


# --- words for people --------------------------------------------------------

PART_WORDS = {
    PartKind.GOODS: "Merchant's price",
    PartKind.PSP_FEE: "Payment fee (estimate)",
    PartKind.FX: "FX margin",
    PartKind.MOR_MARGIN: "Our margin",
    PartKind.FREIGHT: "Freight",
    PartKind.SURCHARGE: "Surcharge",
    PartKind.FUEL: "Fuel",
    PartKind.DUTY_PAID_FEE: "Carrier's fee for paying duties",
    PartKind.DUTY: "Duty",
    PartKind.ADDITIONAL_DUTY: "Additional duty",
    PartKind.CUSTOMS_FEE: "Customs fee",
    PartKind.IMPORT_TAX: "VAT / GST",
    PartKind.OTHER_TAX: "Other tax",
}

ROUNDING_WORDS = {
    RoundingMode.CEILING: "up",
    RoundingMode.FLOOR: "down",
    RoundingMode.HALF_EVEN: "half to even",
    RoundingMode.HALF_AWAY_FROM_ZERO: "half up",
    RoundingMode.TOWARD_ZERO: "toward zero",
    RoundingMode.AWAY_FROM_ZERO: "away from zero",
}

COLLECTOR_WORDS = {
    Collector.BORDER: "at the border",
    Collector.SELLER: "by us",
}

NOT_OFFERED_WORDS: dict[NotOfferedReason, str] = {
    "service_not_confirmed": "service not confirmed",
    "card_not_in_force": "rate card not in force",
    "card_not_confirmed": "rate card not confirmed",
    "no_zone": "no zone for the country",
    "zone_not_confirmed": "zone not confirmed",
    "over_weight": "too heavy",
    "price_range": "price is a range",
    "category_restricted": "category restricted",
    "surcharge_not_confirmed": "a surcharge is not confirmed",
    "fuel_unknown": "fuel rate unknown",
    "not_duty_paid": "not duty-paid",
    "fx_missing": "no exchange rate",
}

SUMMARY_ROWS: tuple[tuple[str, frozenset[PartKind]], ...] = (
    ("Merchant's price", frozenset({PartKind.GOODS})),
    ("Our fees", frozenset({PartKind.PSP_FEE, PartKind.FX, PartKind.MOR_MARGIN})),
    (
        "Shipping",
        frozenset({PartKind.FREIGHT, PartKind.SURCHARGE, PartKind.FUEL}),
    ),
    ("Carrier's fee for paying duties", frozenset({PartKind.DUTY_PAID_FEE})),
    (
        "Duties and taxes",
        frozenset(
            {
                PartKind.DUTY,
                PartKind.ADDITIONAL_DUTY,
                PartKind.CUSTOMS_FEE,
                PartKind.IMPORT_TAX,
                PartKind.OTHER_TAX,
            }
        ),
    ),
)


def closed_text(closed: Closed) -> str:
    match closed:
        case NotListed():
            return "not in our countries file"
        case NotOpen(status=status, note=note):
            return f"{status.value}: {note}" if note else status.value
        case RulesNotConfirmed(line_nos=line_nos):
            lines = ", ".join(str(line_no) for line_no in line_nos)
            return f"its rules are not confirmed (landed_rates.csv lines {lines})"
        case RatesMissing(category=category):
            return f"no import tax recorded for {category}"


# The fields that can be marked, in form order, with their labels.
FIELD_LABELS = (
    ("country", "Ship to"),
    ("goods", "Merchant's price"),
    ("hs", "HS code"),
    ("weight", "Weight"),
    ("length", "Box"),
)


# --- the form ----------------------------------------------------------------------


def _field(
    view: QuoteView,
    name: str,
    label: str,
    control: htpy.Node,
    wide: bool = False,
    hint: str | None = None,
) -> htpy.Element:
    classes = "field"
    if wide:
        classes += " wide"
    # The error slot is always there, so the live update can fill or empty
    # it (`data-live`); the stylesheet marks a field whose slot has text.
    return htpy.label(class_=classes)[
        htpy.span(class_="label")[label],
        control,
        htpy.span(class_="hint")[hint] if hint is not None else None,
        htpy.span(id=f"error-{name}", class_="error", data_live="")[
            view.errors.get(name, "")
        ],
    ]


def _input(
    view: QuoteView, name: str, placeholder: str, mode: str = "text"
) -> htpy.VoidElement:
    return htpy.input(
        name=name,
        value=view.fields.get(name, ""),
        # "e.g.", so an example is not read as a value already entered.
        placeholder=f"e.g. {placeholder}",
        inputmode=mode,
        autocomplete="off",
    )


def _form(view: QuoteView) -> htpy.Element:
    countries: list[htpy.Node] = [htpy.option(value="")["Choose a country"]]
    chosen = view.fields.get("country", "")
    for country in view.countries:
        countries.append(
            htpy.option(value=country.iso2, selected=country.iso2 == chosen)[
                f"{country.name} ({country.iso2}) · {country.status.value}"
            ]
        )
    categories: list[htpy.Node] = []
    chosen_category = view.fields.get("category", "apparel")
    for category, label in view.categories:
        categories.append(
            htpy.option(value=category, selected=category == chosen_category)[label]
        )
    origins: list[htpy.Node] = []
    chosen_origin = view.fields.get("origin", "VN").strip().upper() or "VN"
    for country in view.origins:
        origins.append(
            htpy.option(value=country.iso2, selected=country.iso2 == chosen_origin)[
                f"{country.name} ({country.iso2})"
            ]
        )
    currencies: list[htpy.Node] = []
    chosen_currency = view.fields.get("currency", "USD")
    for currency in Currency:
        currencies.append(
            htpy.option(
                value=currency.value, selected=currency.value == chosen_currency
            )[currency.value]
        )
    return htpy.form(id="order", class_="order panel", method="get", action=PAGE)[
        htpy.fieldset[
            htpy.legend["Order"],
            htpy.div(class_="fields")[
                _field(
                    view,
                    "country",
                    "Ship to",
                    htpy.select(name="country")[countries],
                    True,
                ),
                _field(
                    view,
                    "category",
                    "Category",
                    htpy.select(name="category")[categories],
                ),
                _field(
                    view,
                    "goods",
                    "Merchant's price",
                    htpy.span(class_="pair")[
                        _input(view, "goods", "50.00", "decimal"),
                        htpy.select(name="currency", aria_label="currency")[currencies],
                    ],
                    True,
                ),
                _field(view, "hs", "HS code", _input(view, "hs", "610910", "numeric")),
                _field(
                    view,
                    "origin",
                    "Made in (country of origin)",
                    htpy.select(name="origin")[origins],
                    hint="Decides trade-agreement rates.",
                ),
                _field(
                    view,
                    "on",
                    "Rules as of",
                    day_input(view.on),
                    True,
                    hint="The market data in force that day; a later day shows"
                    " a change entered ahead.",
                ),
            ],
        ],
        htpy.fieldset[
            htpy.legend["Parcel"],
            htpy.div(class_="fields")[
                _field(
                    view,
                    "weight",
                    "Weight (g)",
                    _input(view, "weight", "600", "numeric"),
                ),
                _field(
                    view,
                    "length",
                    "Box (mm): length x width x height",
                    htpy.span(class_="box")[
                        _input(view, "length", "300", "numeric"),
                        "x",
                        htpy.input(
                            name="width",
                            value=view.fields.get("width", ""),
                            placeholder="e.g. 250",
                            inputmode="numeric",
                            aria_label="width (mm)",
                        ),
                        "x",
                        htpy.input(
                            name="height",
                            value=view.fields.get("height", ""),
                            placeholder="e.g. 50",
                            inputmode="numeric",
                            aria_label="height (mm)",
                        ),
                    ],
                    True,
                ),
            ],
        ],
        htpy.div(class_="actions")[
            # Pressed, the page also marks what is still missing (`go`).
            htpy.button(type="submit", name="go", value="1")["Quote"],
            htpy.span(id="live-status", class_="live-status", aria_live="polite"),
        ],
    ]


# --- the result --------------------------------------------------------------------


def _ask_form(view: QuoteView) -> htpy.Node:
    if view.ask is None:
        return None
    if not view.ask.has_account:
        return htpy.p[
            f"There is no {view.ask.vendor} account in this environment, so it"
            " cannot be asked."
        ]
    return htpy.form(method="post", action=f"{PAGE}/duty_lookups")[
        htpy.input(type="hidden", name="country", value=view.fields.get("country", "")),
        htpy.input(type="hidden", name="origin", value=view.fields.get("origin", "")),
        htpy.input(type="hidden", name="hs", value=view.fields.get("hs", "")),
        htpy.input(type="hidden", name="back", value=view.back),
        htpy.button(type="submit")[f"Ask {view.ask.vendor}"],
    ]


def _notice(kind: str, *content: htpy.Node) -> htpy.Element:
    return htpy.div(class_=f"notice {kind}")[content]


def _refusal(view: QuoteView, quote: QuoteResult) -> htpy.Node:
    match quote:
        case Quote():
            return None
        case InputsMissing(asks=asks, waiting=waiting):
            if waiting and not asks:
                return _notice(
                    "info",
                    htpy.strong["Waiting for the vendor."],
                    " It was asked about this product; the worker records its"
                    " answer, then this page quotes.",
                )
            return _notice(
                "info",
                htpy.strong["The vendor has not been asked about this product yet."],
                " Its duty and tax rates decide here.",
                _ask_form(view),
            )
        case CountryClosed(category=category, closed=closed):
            return _notice(
                "bad",
                htpy.strong["Not sold there"],
                f" for {category}: {closed_text(closed)}.",
            )
        case NoCurrency(currency=currency):
            return _notice("bad", f"No currency we can charge in ({currency}).")
        case NoFeeRule(component=component):
            return _notice("bad", f"No fee rule for {component.value} in fees.csv.")
        case LineRateRefused(charge=charge, problem=problem):
            return _notice(
                "bad",
                htpy.strong[f"No usable {charge.value} rate: "],
                _rate_problem_text(problem),
                " See why below.",
            )
        case NoServiceForLane(not_offered=not_offered):
            return _notice(
                "bad",
                htpy.strong["No shipping option can carry it."],
                _not_offered_list(not_offered),
            )
        case _:
            return _notice(
                "bad",
                f"No confirmed exchange rate from {quote.base.value} to"
                f" {quote.quote.value}.",
            )


def _rate_problem_text(problem: LineRateProblem) -> str:
    match problem:
        case RateNeeded(reason="no_hs_code"):
            return "enter the HS code."
        case RateNeeded():
            return "we have no rate for this HS code."
        case RateNotConfirmed(line_no=line_no):
            return f"our rate is not confirmed (hs_rates.csv line {line_no})."
        case VendorUnknownCode():
            return "the vendor does not know this HS code."
        case VendorNeedsPerson(detail=detail):
            return f"the vendor's answer needs a person ({detail})."


def _not_offered_list(items: tuple[NotOffered, ...]) -> htpy.Element:
    lines: list[htpy.Node] = []
    for item in items:
        lines.append(
            htpy.li[
                htpy.code[item.rate_card],
                f": {NOT_OFFERED_WORDS[item.reason]} ",
                htpy.span(class_="muted")[f"({item.detail})"],
            ]
        )
    return htpy.ul[lines]


def _converted(money: Money, currency: Currency, rate: Fraction) -> Money:
    """`money` in a reference currency, rounded half to even: for reading."""
    major = Fraction(money.amount_minor, 10 ** exponent(money.currency))
    minor = major * rate * 10 ** exponent(currency)
    return Money(round_fraction(minor, RoundingMode.HALF_EVEN), currency)


def approx_text(view: QuoteView, money: Money) -> str:
    """ "≈ 221.47 USD · 198.36 EUR", or "" with no reference currency."""
    said: list[str] = []
    for currency, rate in view.references:
        said.append(money_text(_converted(money, currency, rate)))
    if not said:
        return ""
    return "≈ " + " · ".join(said)


def _first_reference(view: QuoteView, money: Money) -> str:
    """The amount in the first reference currency, for a table column."""
    for currency, rate in view.references:
        return money_text(_converted(money, currency, rate))
    return ""


def _reference_heading(view: QuoteView) -> str:
    for currency, _rate in view.references:
        return f"≈ {currency.value}"
    return ""


def _sum(
    parts: tuple[Part, ...], kinds: frozenset[PartKind], currency: Currency
) -> Money:
    total = 0
    for part in parts:
        if part.kind in kinds:
            total += part.amount.amount_minor
    return Money(total, currency)


def _parts_table(view: QuoteView, parts: tuple[Part, ...]) -> htpy.Element:
    rows: list[htpy.Node] = []
    for part in parts:
        collected = (
            COLLECTOR_WORDS[part.collector] if part.collector is not None else ""
        )
        rows.append(
            htpy.tr[
                htpy.td(title=part.kind.value)[PART_WORDS[part.kind]],
                htpy.td[part.label],
                htpy.td(class_="num")[money_text(part.amount)],
                htpy.td(class_="num muted")[_first_reference(view, part.amount)],
                htpy.td(title=part.mode.value)[ROUNDING_WORDS[part.mode]],
                htpy.td[collected],
                htpy.td(class_="source")[part.source],
            ]
        )
    return htpy.div(class_="scroll")[
        htpy.table(class_="grid")[
            htpy.tr[
                htpy.th["Part"],
                htpy.th["How"],
                htpy.th(class_="num")["Amount"],
                htpy.th(class_="num")[_reference_heading(view)],
                htpy.th["Rounded"],
                htpy.th["Collected"],
                htpy.th["From"],
            ],
            rows,
        ]
    ]


def _option_card(
    view: QuoteView, quote: Quote, option: ShippingOption, best: bool
) -> htpy.Element:
    everything = quote.goods + option.parts
    tags: list[htpy.Node] = [
        htpy.span(class_="tag")[option.tier.value.replace("_", " ")]
    ]
    if best:
        tags.append(htpy.span(class_="tag good")["cheapest"])
    if option.transit_days is not None:
        low, high = option.transit_days
        tags.append(htpy.span(class_="tag")[f"{low}–{high} days"])
    else:
        tags.append(htpy.span(class_="tag warn")["transit unknown"])
    if option.fuel_stale_since is not None:
        tags.append(
            htpy.span(class_="tag warn")[
                f"fuel rate ended {format_day(option.fuel_stale_since)}"
            ]
        )
    if option.standing == Standing.PLACEHOLDER:
        tags.append(htpy.span(class_="tag warn")["placeholder route"])
    summary: list[htpy.Node] = []
    for label, kinds in SUMMARY_ROWS:
        amount = _sum(everything, kinds, quote.currency)
        if amount.amount_minor == 0 and PartKind.DUTY_PAID_FEE in kinds:
            continue
        summary.append(
            htpy.tr[
                htpy.td[label],
                htpy.td[money_text(amount)],
                htpy.td(class_="muted")[_first_reference(view, amount)],
            ]
        )
    return htpy.article(class_="option best" if best else "option")[
        htpy.h3[option.service_name],
        htpy.div(class_="tags")[tags],
        htpy.div(class_="total")[money_text(option.total)],
        htpy.div(class_="approx muted")[approx_text(view, option.total)],
        htpy.table(class_="summary")[summary],
        htpy.a(href=f"#parts-{option.rate_card}")["Every part"],
    ]


def _option_parts(
    view: QuoteView, quote: Quote, option: ShippingOption
) -> htpy.Element:
    """An option's every part, full width under the cards."""
    return htpy.details(class_="parts panel", id=f"parts-{option.rate_card}")[
        htpy.summary[
            f"Every part: {option.service_name} ",
            htpy.span(class_="muted")[
                f"({option.rate_card}, {money_text(option.total)})"
            ],
        ],
        _parts_table(view, quote.goods + option.parts),
    ]


def _quote_block(view: QuoteView, quote: Quote) -> htpy.Node:
    count = len(quote.options)
    plural = "s" if count != 1 else ""
    heading = f"{count} shipping option{plural}, paid in {quote.currency.value}"
    blocks: list[htpy.Node] = [
        htpy.div(class_="result-head")[
            htpy.h2[heading],
            htpy.button(
                type="button", class_="quiet", data_toggle_parts="", hidden=True
            )["Show every part"],
        ]
    ]
    if view.references:
        blocks.append(
            htpy.p(class_="muted")[
                f"The buyer pays in {quote.currency.value}. Amounts after ≈ are"
                " converted at the market data's exchange rates, for reading only."
            ]
        )
    if quote.placeholders:
        blocks.append(
            _notice(
                "warn",
                htpy.strong["Placeholder numbers in this quote: "],
                ", ".join(quote.placeholders),
                ". Prod refuses to quote on a placeholder.",
            )
        )
    cards: list[htpy.Node] = []
    for index, option in enumerate(quote.options):
        cards.append(_option_card(view, quote, option, index == 0 and count > 1))
    blocks.append(htpy.div(class_="options")[cards])
    for option in quote.options:
        blocks.append(_option_parts(view, quote, option))
    if quote.not_offered:
        blocks.append(
            htpy.details[
                htpy.summary[
                    f"{len(quote.not_offered)} rate card"
                    f"{'s' if len(quote.not_offered) != 1 else ''} not offered"
                ],
                _not_offered_list(quote.not_offered),
            ]
        )
    return blocks


def _result(view: QuoteView) -> htpy.Element:
    blocks: list[htpy.Node] = []
    if not view.loaded:
        blocks.append(
            _notice(
                "warn",
                "No market data loaded yet: run `python -m mor.markets.load --write`.",
            )
        )
    elif view.errors:
        names: list[str] = []
        for name, label in FIELD_LABELS:
            if name in view.errors:
                names.append(label)
        blocks.append(
            _notice("bad", f"Fix the fields marked above: {', '.join(names)}.")
        )
    elif view.why is None:
        blocks.append(
            htpy.p(class_="muted")[
                "Choose a country and enter a price; add a parcel weight to see"
                " the shipping options and the full price."
            ]
        )
    if view.asked:
        blocks.append(
            _notice("info", "Asked. The worker sends the question; reload in a moment.")
        )
    if view.quote is not None:
        if isinstance(view.quote, Quote):
            blocks.append(_quote_block(view, view.quote))
        else:
            blocks.append(_refusal(view, view.quote))
    elif view.why is not None:
        blocks.append(
            htpy.p(class_="muted")["Add a parcel weight to see the shipping options."]
        )
    if view.why is not None:
        items: list[htpy.Node] = []
        for line in view.why:
            items.append(htpy.li[line])
        open_it = not isinstance(view.quote, Quote)
        ask = _ask_form(view) if view.quote is None else None
        blocks.append(
            htpy.details(class_="panel", open=open_it)[
                htpy.summary["Why these duties and taxes"],
                htpy.ul(class_="why")[items],
                ask,
            ]
        )
    return htpy.section(id="result")[blocks]


def render_quote_page(view: QuoteView) -> str:
    return staff_page(
        "Quote",
        PAGE,
        ("staff_quote.js",),
        htpy.h1["Quote an order"],
        htpy.p(class_="muted")[
            "What a buyer would pay, from the market data in force: every"
            " shipping option with each part of its price."
        ],
        _form(view),
        _result(view),
    )

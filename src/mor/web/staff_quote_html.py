"""The staff quote page's HTML (docs/plan-quote.md §5b): the order form,
then every shipping option on one line each, how the chosen one's price
comes to be (each part, what it is a share of, where it came from), and
why the duties and taxes are what they are. Typed functions
from the view to markup; the handler is `staff_markets.quote_page`.

`ts/staff_quote.ts` re-quotes as the form changes, by fetching this same
page and swapping `#result`; without it the form's button does the same,
and an option's name is a plain link to the page showing it."""

from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from urllib.parse import urlencode

import htpy

from mor.couriers.records import PriceQuestion
from mor.dates import format_day, format_moment
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
from mor.tax.explain import CHARGE_WORDS, Explained
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


@dataclass(frozen=True, kw_only=True)
class QuoteView:
    countries: list[Country]  # researched ones, for the country list
    origins: list[Country]  # every country in the file, for "Made in"
    categories: list[tuple[str, str]]  # (category, label)
    fields: dict[str, str]  # the form as entered
    errors: dict[str, str]  # per field: what is wrong with it
    on: date
    loaded: bool  # whether any market data is loaded
    quote: QuoteResult | None
    why: Explained | None  # the tax decision, once a country and price are given
    ask: Ask | None
    asked: bool  # the vendor or a carrier was just asked from this page
    back: str  # this page's URL, to come back to after a POST
    # Units of each reference currency per unit of the quote's currency,
    # from the load's exchange rates: amounts shown beside the quote's own,
    # for reading only.
    references: tuple[tuple[Currency, Fraction], ...]
    # Carriers a live route needs a price from but this environment has no
    # account for: nothing can be asked.
    carriers_without_account: frozenset[str] = frozenset()


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
    NotOfferedReason.SERVICE_NOT_CONFIRMED: "service not confirmed",
    NotOfferedReason.CARD_NOT_IN_FORCE: "rate card not in force",
    NotOfferedReason.CARD_NOT_CONFIRMED: "rate card not confirmed",
    NotOfferedReason.NO_ZONE: "no zone for the country",
    NotOfferedReason.ZONE_NOT_CONFIRMED: "zone not confirmed",
    NotOfferedReason.OVER_WEIGHT: "too heavy",
    NotOfferedReason.PRICE_RANGE: "price is a range",
    NotOfferedReason.CATEGORY_RESTRICTED: "category restricted",
    NotOfferedReason.SURCHARGE_NOT_CONFIRMED: "a surcharge is not confirmed",
    NotOfferedReason.FUEL_UNKNOWN: "fuel rate unknown",
    NotOfferedReason.NOT_DUTY_PAID: "not duty-paid",
    NotOfferedReason.FX_MISSING: "no exchange rate",
    NotOfferedReason.CARRIER_NOT_SERVED: "the carrier does not serve it",
    NotOfferedReason.CARRIER_NEEDS_PERSON: "the carrier's answer needs a person",
    NotOfferedReason.PLACEHOLDER: "a placeholder, not offered in prod",
}

# The groups a price is built from, in order: label, colour class, kinds.
# The carrier's fee for paying duties is the carrier's, so it is shipping.
GROUPS: tuple[tuple[str, str, frozenset[PartKind]], ...] = (
    ("Merchant's price", "s-goods", frozenset({PartKind.GOODS})),
    (
        "Our fees",
        "s-fees",
        frozenset({PartKind.PSP_FEE, PartKind.FX, PartKind.MOR_MARGIN}),
    ),
    (
        "Shipping",
        "s-ship",
        frozenset(
            {
                PartKind.FREIGHT,
                PartKind.SURCHARGE,
                PartKind.FUEL,
                PartKind.DUTY_PAID_FEE,
            }
        ),
    ),
    (
        "Duties and taxes",
        "s-tax",
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
        # The option shown below; a name in the options table sets it.
        htpy.input(type="hidden", name="option", value=view.fields.get("option", "")),
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


def _price_form(view: QuoteView, question: PriceQuestion) -> htpy.Element:
    parcel = question.parcel
    return htpy.form(method="post", action=f"{PAGE}/shipping_prices")[
        htpy.input(type="hidden", name="country", value=question.destination),
        htpy.input(type="hidden", name="service", value=question.service),
        htpy.input(type="hidden", name="weight", value=str(parcel.weight_g)),
        htpy.input(type="hidden", name="length", value=str(parcel.length_mm)),
        htpy.input(type="hidden", name="width", value=str(parcel.width_mm)),
        htpy.input(type="hidden", name="height", value=str(parcel.height_mm)),
        htpy.input(type="hidden", name="back", value=view.back),
        htpy.button(type="submit")[f"Ask {question.carrier} for {question.service}"],
    ]


def _missing_notices(view: QuoteView, missing: InputsMissing) -> list[htpy.Node]:
    """What the quote waits for: the duty vendor, the carriers, or both."""
    notices: list[htpy.Node] = []
    if missing.asks:
        notices.append(
            _notice(
                "info",
                htpy.strong["The vendor has not been asked about this product yet."],
                " Its duty and tax rates decide here.",
                _ask_form(view),
            )
        )
    elif missing.waiting:
        notices.append(
            _notice(
                "info",
                htpy.strong["Waiting for the vendor."],
                " It was asked about this product; the worker records its"
                " answer, then this page quotes.",
            )
        )
    if missing.price_asks:
        forms: list[htpy.Node] = []
        for question in missing.price_asks:
            if question.carrier in view.carriers_without_account:
                forms.append(
                    htpy.p[
                        f"There is no {question.carrier} account in this"
                        " environment, so it cannot be asked."
                    ]
                )
            else:
                forms.append(_price_form(view, question))
        notices.append(
            _notice(
                "info",
                htpy.strong["No usable carrier price for this parcel."],
                " A live route is priced by the carrier: it is asked for this"
                " parcel, and its price is used until the account's period"
                " ends.",
                forms,
            )
        )
    elif missing.price_waiting:
        notices.append(
            _notice(
                "info",
                htpy.strong["Waiting for the carrier."],
                " It was asked for this parcel's price; the worker records its"
                " answer, then this page quotes.",
            )
        )
    return notices


def _notice(kind: str, *content: htpy.Node) -> htpy.Element:
    return htpy.div(class_=f"notice {kind}")[content]


def _refusal(view: QuoteView, quote: QuoteResult) -> htpy.Node:
    match quote:
        case Quote():
            return None
        case InputsMissing():
            return _missing_notices(view, quote)
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
                htpy.table(class_="grid")[_not_offered_rows(not_offered, 1)],
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


def _not_offered_rows(items: tuple[NotOffered, ...], span: int) -> list[htpy.Node]:
    """A table row per route that cannot carry the order: its card or live
    service, then why across `span` columns."""
    rows: list[htpy.Node] = []
    for item in items:
        rows.append(
            htpy.tr(class_="not-offered")[
                htpy.td[htpy.code[item.key]],
                htpy.td(colspan=str(span))[
                    f"Not offered: {NOT_OFFERED_WORDS[item.reason]} ",
                    htpy.span(class_="muted")[f"({item.detail})"],
                ],
            ]
        )
    return rows


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


def _sum(parts: list[Part], currency: Currency) -> Money:
    total = 0
    for part in parts:
        total += part.amount.amount_minor
    return Money(total, currency)


def chosen_option(quote: Quote, key: str) -> ShippingOption:
    """The option asked for by its key, else the cheapest."""
    for option in quote.options:
        if option.key == key:
            return option
    return quote.options[0]


def _option_href(view: QuoteView, key: str) -> str:
    """This page with the same order, showing another option."""
    params: dict[str, str] = {}
    for name, value in view.fields.items():
        if name != "option" and value:
            params[name] = value
    params["on"] = view.on.isoformat()
    params["option"] = key
    return f"{PAGE}?{urlencode(params)}"


def _option_tags(option: ShippingOption) -> list[htpy.Node]:
    tags: list[htpy.Node] = []
    if option.fuel_stale_since is not None:
        tags.append(
            htpy.span(class_="tag warn")[
                f"fuel rate ended {format_day(option.fuel_stale_since)}"
            ]
        )
    if option.standing == Standing.PLACEHOLDER:
        tags.append(htpy.span(class_="tag warn")["placeholder route"])
    if option.live is not None:
        tags.append(
            htpy.span(class_="tag")[
                f"carrier's price, used until {format_moment(option.live.usable_until)}"
            ]
        )
        if option.live.own is not None:
            carrier = option.live.carrier_total
            ours = option.live.own.total
            tags.append(
                htpy.span(class_="tag")[
                    f"carrier {money_text(carrier)}, our card {money_text(ours)}"
                ]
            )
    return tags


def _days_text(option: ShippingOption) -> str:
    if option.transit_days is None:
        return "unknown"
    low, high = option.transit_days
    return f"{low}–{high} days"


def _options_table(
    view: QuoteView, quote: Quote, chosen: ShippingOption
) -> htpy.Element:
    """Every option on one line each, cheapest first; the chosen one is
    explained below, and each name shows another."""
    rows: list[htpy.Node] = []
    for index, option in enumerate(quote.options):
        name: list[htpy.Node] = [
            htpy.a(
                href=_option_href(view, option.key),
                data_option=option.key,
                aria_current="true" if option is chosen else None,
            )[option.service_name]
        ]
        if index == 0 and len(quote.options) > 1:
            name.append(htpy.span(class_="tag good")["cheapest"])
        rows.append(
            htpy.tr(class_="chosen" if option is chosen else None)[
                htpy.td[name],
                htpy.td[option.tier.value.replace("_", " ")],
                htpy.td[_days_text(option)],
                htpy.td[_option_tags(option)],
                htpy.td(class_="num")[htpy.strong[money_text(option.total)]],
                htpy.td(class_="num muted")[approx_text(view, option.total)],
            ]
        )
    return htpy.div(class_="scroll")[
        htpy.table(class_="grid options-table")[
            htpy.tr[
                htpy.th["Service"],
                htpy.th["Type"],
                htpy.th["Delivery"],
                htpy.th["Notes"],
                htpy.th(class_="num")["Buyer pays"],
                htpy.th(class_="num")[""],
            ],
            rows,
            # Cards that cannot carry it, greyed, with why.
            _not_offered_rows(quote.not_offered, 5),
        ]
    ]


def _grouped(quote: Quote, option: ShippingOption) -> list[tuple[str, str, list[Part]]]:
    """The chosen option's parts in the order the price is built, by group:
    (label, css class, parts); empty groups left out."""
    found: list[tuple[str, str, list[Part]]] = []
    for label, css, kinds in GROUPS:
        parts: list[Part] = []
        for part in quote.goods + option.parts:
            if part.kind in kinds:
                parts.append(part)
        if parts:
            found.append((label, css, parts))
    return found


def _share_bar(
    groups: list[tuple[str, str, list[Part]]], quote: Quote, total: Money
) -> htpy.Element:
    """One bar, each group's share of what the buyer pays."""
    pieces: list[htpy.Node] = []
    legend: list[htpy.Node] = []
    for label, css, parts in groups:
        amount = _sum(parts, quote.currency)
        if total.amount_minor <= 0 or amount.amount_minor <= 0:
            continue
        share = Fraction(amount.amount_minor * 100, total.amount_minor)
        percent = round_fraction(share, RoundingMode.HALF_EVEN)
        width = f"{float(share):.2f}%"
        pieces.append(
            htpy.span(class_=f"piece {css}", style=f"width: {width}", title=label)
        )
        legend.append(htpy.li[htpy.span(class_=f"swatch {css}"), f"{label} {percent}%"])
    return htpy.div(class_="shares")[
        htpy.div(class_="bar", aria_hidden="true")[pieces],
        htpy.ul(class_="legend")[legend],
    ]


def _part_note(part: Part) -> str:
    """Where a part's number came from and how it was rounded."""
    said = [f"from {part.source}", f"rounded {ROUNDING_WORDS[part.mode]}"]
    if part.collector is not None:
        said.append(f"paid {COLLECTOR_WORDS[part.collector]}")
    return " · ".join(said)


def _how(part: Part, lines: int) -> list[htpy.Node]:
    """How a part was reached, and where from; its order line named only
    when the order has more than one."""
    how = part.label
    if part.line is not None and lines > 1:
        how = f"line {part.line + 1}: {how}"
    return [
        htpy.span(class_="how")[how],
        htpy.span(class_="note")[_part_note(part)],
    ]


def _buildup(view: QuoteView, quote: Quote, option: ShippingOption) -> htpy.Element:
    """How the chosen option's price comes to be: each group with its
    subtotal, each part with what it is a share of and where it came from,
    then what the buyer pays."""
    groups = _grouped(quote, option)
    lines = 0
    for part in quote.goods:
        if part.kind == PartKind.GOODS:
            lines += 1
    body: list[htpy.Node] = []
    for number, (label, css, parts) in enumerate(groups):
        subtotal = _sum(parts, quote.currency)
        sign = "" if number == 0 else "+ "
        heading: list[htpy.Node] = [
            htpy.span(class_=f"swatch {css}"),
            htpy.span(class_="what")[f"{sign}{label}"],
        ]
        if len(parts) == 1:
            # One part: its group's line says how it was reached.
            heading.extend(_how(parts[0], lines))
        rows: list[htpy.Node] = [
            htpy.tr(class_="group")[
                htpy.th(scope="rowgroup")[heading],
                htpy.td(class_="num")[money_text(subtotal)],
                htpy.td(class_="num muted")[_first_reference(view, subtotal)],
            ]
        ]
        for part in parts:
            if len(parts) == 1:
                break  # said on the group's line
            rows.append(
                htpy.tr(class_="part")[
                    htpy.td[
                        htpy.span(class_="what")[PART_WORDS[part.kind]],
                        _how(part, lines),
                    ],
                    htpy.td(class_="num")[money_text(part.amount)],
                    htpy.td(class_="num muted")[_first_reference(view, part.amount)],
                ]
            )
        body.append(htpy.tbody[rows])
    if not groups or groups[-1][1] != "s-tax":
        # Said, not left out: none due is an answer too.
        none = Money(0, quote.currency)
        body.append(
            htpy.tbody[
                htpy.tr(class_="group")[
                    htpy.th(scope="rowgroup")[
                        htpy.span(class_="swatch s-tax"),
                        htpy.span(class_="what")["+ Duties and taxes"],
                        htpy.span(class_="how")["none due on this order"],
                        htpy.span(class_="note")["why: see Duties and taxes below"],
                    ],
                    htpy.td(class_="num")[money_text(none)],
                    htpy.td(class_="num muted")[_first_reference(view, none)],
                ]
            ]
        )
    return htpy.section(class_="panel buildup")[
        htpy.h2[f"How {option.service_name} comes to {money_text(option.total)}"],
        _share_bar(groups, quote, option.total),
        htpy.div(class_="scroll")[
            htpy.table(class_="build")[
                htpy.thead[
                    htpy.tr[
                        htpy.th["Part"],
                        htpy.th(class_="num")[quote.currency.value],
                        htpy.th(class_="num")[_reference_heading(view)],
                    ]
                ],
                body,
                htpy.tfoot[
                    htpy.tr(class_="total")[
                        htpy.th["= Buyer pays"],
                        htpy.td(class_="num")[money_text(option.total)],
                        htpy.td(class_="num muted")[
                            _first_reference(view, option.total)
                        ],
                    ]
                ],
            ]
        ],
    ]


def _quote_block(view: QuoteView, quote: Quote) -> htpy.Node:
    count = len(quote.options)
    plural = "s" if count != 1 else ""
    blocks: list[htpy.Node] = [
        htpy.h2(class_="result-head")[
            f"{count} shipping option{plural}, paid in {quote.currency.value}"
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
    chosen = chosen_option(quote, view.fields.get("option", ""))
    blocks.append(_options_table(view, quote, chosen))
    blocks.append(_buildup(view, quote, chosen))
    return blocks


def _duties_panel(why: Explained, ask: htpy.Node) -> htpy.Element:
    """Who collects each duty and tax and at what rate: a summary, one row
    per charge, then the data rows that decided it, folded."""
    summary: list[htpy.Node] = []
    for sentence in why.summary:
        summary.append(htpy.p[sentence])
    rows: list[htpy.Node] = []
    for charge in why.charges:
        rate: list[htpy.Node] = [charge.rate or "none"]
        if charge.problem is not None:
            rate.append(htpy.span(class_="note bad")[charge.problem])
        for missed in charge.missed:
            rate.append(htpy.span(class_="note")[missed])
        own: htpy.Node = ""
        if charge.own is not None:
            if charge.own.differs:
                own = htpy.span(class_="tag warn")[charge.own.text]
            else:
                own = charge.own.text
        rows.append(
            htpy.tr[
                htpy.td[htpy.strong[CHARGE_WORDS[charge.charge]]],
                htpy.td[charge.collected],
                htpy.td[rate],
                htpy.td[charge.says or ""],
                htpy.td[own],
            ]
        )
    table: htpy.Node = None
    if rows:
        table = htpy.div(class_="scroll")[
            htpy.table(class_="grid duties")[
                htpy.tr[
                    htpy.th["Charge"],
                    htpy.th["Collected"],
                    htpy.th["Rate"],
                    htpy.th["Who says so"],
                    htpy.th["Our own data"],
                ],
                rows,
            ]
        ]
    sources: htpy.Node = None
    if why.rows:
        lines: list[htpy.Node] = []
        for row in why.rows:
            lines.append(
                htpy.tr[
                    htpy.td[row.where],
                    htpy.td[row.says],
                    htpy.td[row.research or ""],
                ]
            )
        sources = [
            htpy.h3["The data rows used"],
            htpy.div(class_="scroll")[
                htpy.table(class_="grid sources")[
                    htpy.tr[
                        htpy.th["Where"],
                        htpy.th["What it says"],
                        htpy.th["Research"],
                    ],
                    lines,
                ]
            ],
        ]
    return htpy.section(class_="panel duties-panel")[
        htpy.h2["Duties and taxes: who collects them, and at what rate"],
        htpy.div(class_="duties-summary")[summary],
        table,
        ask,
        sources,
    ]


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
        ask = _ask_form(view) if view.quote is None else None
        blocks.append(_duties_panel(view.why, ask))
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

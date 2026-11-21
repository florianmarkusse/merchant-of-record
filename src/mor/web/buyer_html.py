"""The buyer's checkout page (`/c/{session}`, docs/plan-quote.md §5, 4d-2;
docs/plan-payments.md §4): typed htpy, English first, mobile first, the
merchant's name on top and ours only in the one line the law asks for.
While a quote is worked out or a payment confirmed, `ts/waiting.ts` asks
whether the state changed and reloads once it has; without the script the
page refreshes itself (the <meta> refresh in its <noscript>).

What the buyer sees of a price is the four lines decided in
plan-architecture §8: items (our fee inside), shipping, duties and
taxes, and the total.
"""

from dataclasses import dataclass
from datetime import datetime

import htpy

from mor.checkout import addresses as fields
from mor.checkout.addresses import Address, AddressError, AddressErrors
from mor.checkout.quotes import MadeQuote, RefusedQuote, StoredOption
from mor.checkout.records import CheckoutSession, SessionState
from mor.dates import format_moment
from mor.entity import ENTITY
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.payments.attempts import Attempt
from mor.payments.transitions import AttemptState
from mor.psp.port import PaymentMethod
from mor.quote.build import Part
from mor.quote.groups import group_of
from mor.quote.refusals import RefusalCode
from mor.web.qr import qr_svg
from mor.web.staff_layout import money_text, static_url


# Wording needs legal input per country (plan-merchant-surfaces §5): a
# placeholder until then, and our name with it (src/mor/entity.py).
OUR_NAME = ENTITY.legal_name
REFRESH_SECONDS = 2

REFUSAL_WORDS: dict[RefusalCode, str] = {
    "country_not_supported": "We can't deliver this order to that country.",
    "country_closed": "We can't deliver this order to that country yet.",
    "classification_needed": (
        "This order can't be priced for that country yet. The shop has been told."
    ),
    "duty_rate_unavailable": (
        "We can't work out the duties for that country right now. Please try"
        " again later."
    ),
    "no_service_for_lane": "No delivery service carries this order there.",
    "pricing_unavailable": (
        "We can't price delivery to that country right now. Please try again later."
    ),
}

ERROR_WORDS: dict[AddressError, str] = {
    "required": "Please fill this in.",
    "too_long": "This is too long.",
    "email": "This doesn't look like an email address.",
    "phone": "Please give a phone number with digits only.",
    "postcode": "This isn't a postcode for that country.",
    "country": "Please pick a country we deliver to.",
}

LABELS = (
    (fields.NAME, "Full name", "name"),
    (fields.EMAIL, "Email", "email"),
    (fields.LINE1, "Address", "address-line1"),
    (fields.LINE2, "Address line 2 (optional)", "address-line2"),
    (fields.CITY, "City or district", "address-level2"),
    (fields.REGION, "Province or state", "address-level1"),
    (fields.POSTCODE, "Postcode", "postal-code"),
)


@dataclass(frozen=True)
class BuyerView:
    session: CheckoutSession
    public_id: str
    merchant_name: str
    countries: tuple[tuple[str, str], ...]  # (iso2, name), sorted by name
    values: dict[str, str]  # the address form, as given or stored
    errors: AddressErrors
    notice: str | None  # a message above the form (too many addresses)
    quote: MadeQuote | None
    refused: RefusedQuote | None
    option_key: str  # the option shown; the cheapest if none or unknown
    now: datetime
    attempt: Attempt | None  # the newest payment attempt
    methods: tuple[PaymentMethod, ...]  # what routing offers; none: no route
    takes_payments: bool  # the merchant's KYB is approved
    status: str  # what the waiting script compares (`buyer.status_text`)


METHOD_WORDS: dict[PaymentMethod, str] = {
    PaymentMethod.CARD: "Card",
    PaymentMethod.QR: "QR code, with your banking app",
}

ENDED_WORDS: dict[AttemptState, str] = {
    AttemptState.DECLINED: "Your payment was declined. You can try again.",
    AttemptState.EXPIRED: "Your payment ran out of time. You can try again.",
    AttemptState.CANCELLED: "You can choose another way to pay.",
}


def waiting(view: BuyerView) -> bool:
    """The page shows a state that changes by itself."""
    state = view.session.state
    if state == SessionState.QUOTING:
        return True
    if state == SessionState.PAYING:
        return view.attempt is None or view.attempt.state != AttemptState.NEEDS_PERSON
    return False


def _page(view: BuyerView, *content: htpy.Node) -> str:
    refresh: htpy.Node = None
    script: htpy.Node = None
    if waiting(view):
        refresh = htpy.noscript[
            htpy.meta(http_equiv="refresh", content=str(REFRESH_SECONDS))
        ]
        script = htpy.script(src=static_url("js/waiting.js"), type="module")
    page = htpy.html(lang="en")[
        htpy.head[
            htpy.meta(charset="utf-8"),
            htpy.meta(name="viewport", content="width=device-width, initial-scale=1"),
            htpy.meta(name="robots", content="noindex"),
            # The link is a bearer token: never sent to another site. Not
            # `no-referrer`, which makes the browser send `Origin: null` on
            # the page's own POST and fail the same-origin check (found on
            # the sandbox, 19-Nov-2026).
            htpy.meta(name="referrer", content="same-origin"),
            htpy.link(rel="icon", href="data:,"),
            refresh,
            htpy.title[f"Checkout · {view.merchant_name}"],
            htpy.link(rel="stylesheet", href=static_url("css/buyer.css")),
            script,
        ],
        htpy.body(
            data_status_url=f"/c/{view.public_id}/status",
            data_status=view.status,
        )[
            htpy.header[htpy.h1[view.merchant_name]],
            htpy.main[content],
            htpy.footer[
                f"Sold and invoiced by {OUR_NAME} on behalf of {view.merchant_name}."
            ],
        ],
    ]
    return str(page)


def _cart(session: CheckoutSession) -> htpy.Node:
    items: list[htpy.Node] = []
    for line in session.lines:
        items.append(htpy.li[f"{line.quantity} × {line.name}"])
    return htpy.section(class_="cart")[htpy.h2["Your order"], htpy.ul[items]]


def _field(view: BuyerView, name: str, label: str, autocomplete: str) -> htpy.Node:
    error = view.errors.get(name)
    message: htpy.Node = None
    if error is not None:
        message = htpy.span(class_="error", id=f"{name}-error")[ERROR_WORDS[error]]
    kind = "email" if name == fields.EMAIL else "text"
    return htpy.label[
        htpy.span[label],
        htpy.input(
            type=kind,
            name=name,
            value=view.values.get(name, ""),
            autocomplete=autocomplete,
            maxlength=str(fields.MAX_FIELD),
            aria_invalid="true" if error is not None else None,
            aria_describedby=f"{name}-error" if error is not None else None,
        ),
        message,
    ]


def _address_form(view: BuyerView, action: str) -> htpy.Node:
    chosen = view.values.get(fields.COUNTRY, "")
    options: list[htpy.Node] = [htpy.option(value="")["Choose a country"]]
    for iso2, name in view.countries:
        options.append(htpy.option(value=iso2, selected=iso2 == chosen)[name])
    country_error = view.errors.get(fields.COUNTRY)
    phone_error = view.errors.get(fields.PHONE)
    rows: list[htpy.Node] = [
        htpy.label[
            htpy.span["Country"],
            htpy.select(name=fields.COUNTRY, autocomplete="country")[options],
            None
            if country_error is None
            else htpy.span(class_="error")[ERROR_WORDS[country_error]],
        ]
    ]
    for name, label, autocomplete in LABELS:
        rows.append(_field(view, name, label, autocomplete))
    rows.append(
        htpy.fieldset(class_="phone")[
            htpy.legend["Phone, for the courier"],
            htpy.input(
                type="text",
                name=fields.DIAL_CODE,
                value=view.values.get(fields.DIAL_CODE, ""),
                placeholder="auto",  # blank: the chosen country's own code
                autocomplete="tel-country-code",
                aria_label="Country code, blank for the country's own",
                size="5",
            ),
            htpy.input(
                type="tel",
                name=fields.PHONE,
                value=view.values.get(fields.PHONE, ""),
                autocomplete="tel-national",
                aria_label="Phone number",
            ),
            None
            if phone_error is None
            else htpy.span(class_="error")[ERROR_WORDS[phone_error]],
        ]
    )
    notice: htpy.Node = None
    if view.notice is not None:
        notice = htpy.p(class_="notice")[view.notice]
    return htpy.form(method="post", action=action, class_="address")[
        notice,
        rows,
        htpy.button(type="submit")["See the price"],
    ]


def _sum(parts: tuple[Part, ...], groups: tuple[str, ...]) -> int:
    total = 0
    for part in parts:
        if group_of(part.kind) in groups:
            total += part.amount.amount_minor
    return total


def _chosen(quote: MadeQuote, key: str) -> StoredOption:
    for option in quote.options:
        if option.key == key:
            return option
    return quote.options[0]


def _days(option: StoredOption) -> str:
    if option.transit is None:
        return ""
    low, high = option.transit
    if low == high:
        return f"{low} days"
    return f"{low}–{high} days"


def _quote(view: BuyerView, quote: MadeQuote) -> htpy.Node:
    currency: Currency = quote.currency
    chosen = _chosen(quote, view.option_key)
    rows: list[htpy.Node] = []
    for option in quote.options:
        name: htpy.Node = option.service_name
        if option.key != chosen.key:
            name = htpy.a(href=f"?option={option.key}")[option.service_name]
        rows.append(
            htpy.tr(aria_current="true" if option.key == chosen.key else None)[
                htpy.td[name],
                htpy.td[_days(option)],
                htpy.td(class_="amount")[
                    money_text(Money(option.total_minor, currency))
                ],
            ]
        )
    items = _sum(quote.goods, ("goods", "fees"))
    shipping = _sum(chosen.parts, ("shipping",))
    duties = _sum(chosen.parts, ("duties_and_taxes",))
    lines = (
        ("Items", items),
        ("Shipping", shipping),
        ("Duties and taxes", duties),
    )
    breakdown: list[htpy.Node] = []
    for label, amount in lines:
        breakdown.append(
            htpy.tr[
                htpy.th[label],
                htpy.td(class_="amount")[money_text(Money(amount, currency))],
            ]
        )
    breakdown.append(
        htpy.tr(class_="total")[
            htpy.th["Total"],
            htpy.td(class_="amount")[money_text(Money(chosen.total_minor, currency))],
        ]
    )
    held = view.now < quote.valid_until
    until: htpy.Node
    if held:
        until = htpy.p[
            "This price is held until ",
            htpy.time(datetime=quote.valid_until.isoformat())[
                format_moment(quote.valid_until)
            ],
            ". Nothing more is due on delivery.",
        ]
    else:
        until = htpy.form(method="post", action=f"/c/{view.public_id}/refresh")[
            htpy.p["This price is no longer held."],
            htpy.button(type="submit")["Get the current price"],
        ]
    return htpy.section(class_="quote")[
        htpy.h2["Delivery"],
        htpy.table(class_="options")[htpy.tbody[rows]],
        htpy.h2["Your price"],
        htpy.table(class_="breakdown")[htpy.tbody[breakdown]],
        until,
        _pay_form(view, chosen, currency) if held else None,
    ]


def _pay_form(view: BuyerView, chosen: StoredOption, currency: Currency) -> htpy.Node:
    if not view.takes_payments:
        return htpy.p(class_="notice")["This shop can't take payments right now."]
    if not view.methods:
        return htpy.p(class_="notice")[
            "Payment isn't available for this country yet. The shop has been told."
        ]
    choices: list[htpy.Node] = []
    for number, method in enumerate(view.methods):
        choices.append(
            htpy.label(class_="method")[
                htpy.input(
                    type="radio",
                    name="method",
                    value=method.value,
                    checked=number == 0,
                ),
                " ",
                METHOD_WORDS[method],
            ]
        )
    ended: htpy.Node = None
    if view.attempt is not None and view.attempt.state in ENDED_WORDS:
        ended = htpy.p(class_="refused", role="alert")[ENDED_WORDS[view.attempt.state]]
    return htpy.form(method="post", action=f"/c/{view.public_id}/pay", class_="pay")[
        ended,
        htpy.fieldset[htpy.legend["Pay with"], choices],
        htpy.input(type="hidden", name="option", value=chosen.key),
        htpy.button(type="submit")[
            "Pay ", money_text(Money(chosen.total_minor, currency))
        ],
    ]


def _another_way(view: BuyerView) -> htpy.Node:
    return htpy.form(method="post", action=f"/c/{view.public_id}/pay-another-way")[
        htpy.button(type="submit", class_="secondary")["Pay another way"]
    ]


def _paying(view: BuyerView, attempt: Attempt | None) -> htpy.Node:
    total = htpy.p[
        "Total: ",
        htpy.strong[money_text(attempt.amount)] if attempt is not None else None,
    ]
    if attempt is None or attempt.state in (
        AttemptState.REQUESTED,
        AttemptState.AUTHORISED,
        AttemptState.CANCELLING,
    ):
        return htpy.section(class_="paying")[
            total,
            htpy.p(class_="waiting", role="status")["Confirming your payment…"],
        ]
    if attempt.state == AttemptState.NEEDS_PERSON:
        return htpy.section(class_="paying")[
            total,
            htpy.p(role="status")[
                "We're checking your payment with the payment provider. You won't"
                " be charged twice, and you can close this page."
            ],
        ]
    action = attempt.action
    if attempt.state != AttemptState.ACTION_REQUIRED or action is None:
        return htpy.section(class_="paying")[
            total,
            htpy.p(class_="waiting", role="status")["Confirming your payment…"],
        ]
    expiry: htpy.Node = None
    if action.expires_at is not None:
        expiry = htpy.p[
            "This code works until ",
            htpy.time(datetime=action.expires_at.isoformat())[
                format_moment(action.expires_at)
            ],
            ".",
        ]
    if action.kind == "qr":
        return htpy.section(class_="paying")[
            total,
            htpy.p["Scan this code with your banking app to pay."],
            qr_svg(action.url, "QR code to pay"),
            expiry,
            htpy.p(class_="waiting", role="status")[
                "This page updates by itself once you've paid."
            ],
            _another_way(view),
        ]
    return htpy.section(class_="paying")[
        total,
        htpy.p[htpy.a(href=action.url, class_="button")["Continue to payment"]],
        htpy.p(class_="waiting", role="status")[
            "Once you've paid, this page updates by itself."
        ],
        _another_way(view),
    ]


def _paid(view: BuyerView) -> htpy.Node:
    total: htpy.Node = None
    if view.attempt is not None:
        total = htpy.p["Total paid: ", htpy.strong[money_text(view.attempt.amount)]]
    return htpy.section(class_="paid")[
        htpy.h2["Thank you, your order is paid"],
        total,
        htpy.p[
            f"{view.merchant_name} will send it to you. Your invoice comes from"
            f" {OUR_NAME}."
        ],
        htpy.p[htpy.a(href=view.session.return_url)[f"Back to {view.merchant_name}"]],
    ]


def render_buyer_page(view: BuyerView) -> str:
    action = f"/c/{view.public_id}/address"
    state = view.session.state
    if state == SessionState.COMPLETED:
        return _page(view, _cart(view.session), _paid(view))
    if state == SessionState.PAYING:
        return _page(view, _cart(view.session), _paying(view, view.attempt))
    if state == SessionState.QUOTING:
        return _page(
            view,
            _cart(view.session),
            htpy.p(class_="waiting", role="status")[
                "Working out shipping, duties and taxes for your address…"
            ],
        )
    if state == SessionState.QUOTED and view.quote is not None:
        return _page(
            view,
            _cart(view.session),
            _quote(view, view.quote),
            htpy.details[
                htpy.summary["Change the address"], _address_form(view, action)
            ],
        )
    refused: htpy.Node = None
    if state == SessionState.REFUSED and view.refused is not None:
        refused = htpy.p(class_="refused", role="alert")[
            REFUSAL_WORDS[view.refused.code], " You can try another address."
        ]
    return _page(
        view,
        _cart(view.session),
        refused,
        htpy.h2["Where should we send it?"],
        _address_form(view, action),
    )


def address_values(address: Address) -> dict[str, str]:
    """A stored address back into the form, to change it."""
    return {
        fields.NAME: address.name,
        fields.EMAIL: address.email,
        fields.DIAL_CODE: address.phone_dial_code,
        fields.PHONE: address.phone_number,
        fields.LINE1: address.line1,
        fields.LINE2: address.line2 or "",
        fields.CITY: address.city,
        fields.REGION: address.region or "",
        fields.POSTCODE: address.postcode or "",
        fields.COUNTRY: address.country,
    }

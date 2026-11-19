"""The staff Sessions pages (docs/plan-quote.md §5, 4d-2): the checkout
sessions and every quote made for them, with each part's exact value,
rounding and source. The buyer's address shows as its country only: the
rest is personal data, and nothing here needs it."""

from datetime import datetime

import htpy

from mor.checkout.quotes import MadeQuote, RefusedQuote, StoredPart, StoredQuote
from mor.checkout.records import CheckoutSession
from mor.checkout.sessions import SessionSummary
from mor.dates import format_moment
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.public_ids import QUOTE_PREFIX, SESSION_PREFIX, public_id
from mor.web.api_estimates import collected_by, group_of
from mor.web.staff_layout import money_text, staff_page


SESSIONS_PAGE = "/staff/markets/sessions"


def render_sessions_page(rows: list[SessionSummary], now: datetime) -> str:
    body: list[htpy.Node] = []
    for row in rows:
        session_id = public_id(SESSION_PREFIX, row.id)
        body.append(
            htpy.tr[
                htpy.td[htpy.a(href=f"{SESSIONS_PAGE}/{session_id}")[session_id]],
                htpy.td[row.merchant_name],
                htpy.td[row.state],
                htpy.td[row.country or ""],
                htpy.td[str(row.open_waits) if row.open_waits else ""],
                htpy.td[format_moment(row.created_at)],
            ]
        )
    table: htpy.Node = htpy.p(class_="muted")["No sessions yet."]
    if body:
        table = htpy.table(class_="data")[
            htpy.thead[
                htpy.tr[
                    htpy.th["Session"],
                    htpy.th["Merchant"],
                    htpy.th["State"],
                    htpy.th["Country"],
                    htpy.th["Waiting on"],
                    htpy.th["Created"],
                ]
            ],
            htpy.tbody[body],
        ]
    return staff_page(
        "Sessions",
        SESSIONS_PAGE,
        ("table_filter.js",),
        htpy.h1["Checkout sessions"],
        htpy.p(class_="muted")[
            f"The newest first, as of {format_moment(now)}. A session is"
            " waiting on lookups while its quote's inputs are being fetched."
        ],
        table,
    )


def _parts_rows(parts: tuple[StoredPart, ...], currency: Currency) -> list[htpy.Node]:
    rows: list[htpy.Node] = []
    for part in parts:
        line = "" if part.line is None else str(part.line + 1)
        rows.append(
            htpy.tr[
                htpy.td[group_of(part.kind)],
                htpy.td[part.kind.value],
                htpy.td[line],
                htpy.td(class_="num")[money_text(Money(part.amount_minor, currency))],
                htpy.td[part.rounding.value],
                htpy.td[collected_by(part.collector) or ""],
                htpy.td[part.label],
                htpy.td[part.source],
            ]
        )
    return rows


def _made(quote: MadeQuote) -> htpy.Node:
    currency = quote.currency
    options: list[htpy.Node] = []
    for option in quote.options:
        options.append(
            htpy.h4[
                f"{option.service_name} ({option.key}), total ",
                money_text(Money(option.total_minor, currency)),
            ]
        )
        options.append(
            htpy.table(class_="data")[
                htpy.thead[
                    htpy.tr[
                        htpy.th["Group"],
                        htpy.th["Kind"],
                        htpy.th["Line"],
                        htpy.th["Amount"],
                        htpy.th["Rounding"],
                        htpy.th["Collected by"],
                        htpy.th["How"],
                        htpy.th["Source"],
                    ]
                ],
                htpy.tbody[_parts_rows(quote.goods + option.parts, currency)],
            ]
        )
    notes: list[htpy.Node] = []
    for key, reason in quote.not_offered:
        notes.append(htpy.li[f"{key}: not offered ({reason})"])
    for placeholder in quote.placeholders:
        notes.append(htpy.li[f"placeholder: {placeholder}"])
    return htpy.div[
        htpy.p[
            f"Made {format_moment(quote.made_at)}, held until"
            f" {format_moment(quote.valid_until)}; load {quote.load_id}; inputs:"
            f" {len(quote.inputs.duty_lookups)} duty lookups,"
            f" {len(quote.inputs.price_lookups)} carrier prices."
        ],
        options,
        htpy.ul[notes] if notes else None,
    ]


def _refused(quote: RefusedQuote) -> htpy.Node:
    return htpy.p[
        f"Refused {format_moment(quote.made_at)}: {quote.code}, {quote.message}"
    ]


def render_session_page(
    session: CheckoutSession,
    merchant_name: str,
    countries: dict[str, str],
    quotes: list[StoredQuote],
) -> str:
    """`countries`: each address id (hex) to its country, for the quotes."""
    lines: list[htpy.Node] = []
    for line in session.lines:
        lines.append(
            htpy.li[
                f"{line.quantity} × {line.name} ({line.category}, HS"
                f" {line.customs.hs_code or 'none'}) at ",
                money_text(line.unit_price),
            ]
        )
    sections: list[htpy.Node] = []
    for quote in quotes:
        heading = (
            f"{public_id(QUOTE_PREFIX, quote.id)} for"
            f" {countries.get(quote.address_id.hex, '?')}"
        )
        match quote:
            case MadeQuote():
                sections.append(htpy.section[htpy.h3[heading], _made(quote)])
            case RefusedQuote():
                sections.append(htpy.section[htpy.h3[heading], _refused(quote)])
    session_id = public_id(SESSION_PREFIX, session.id)
    return staff_page(
        f"Session {session_id}",
        SESSIONS_PAGE,
        (),
        htpy.h1[f"Session {session_id}"],
        htpy.p[
            f"{merchant_name}; {session.state.value}; created"
            f" {format_moment(session.created_at)}, expires"
            f" {format_moment(session.expires_at)}."
        ],
        htpy.ul[lines],
        htpy.h2["Quotes, newest first"],
        sections or htpy.p(class_="muted")["None yet."],
    )

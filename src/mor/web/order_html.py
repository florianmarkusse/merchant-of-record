"""The buyer's order page (`/o/{token}`, docs/plan-payments.md §6.1;
plan-merchant-surfaces §5): what was bought, where it goes, the total, the
invoice as a page and a PDF, what happens next, and whom to ask. Until the
invoice is issued, moments after payment, the page waits for it like the
checkout page waits (`ts/waiting.ts`, or a <meta> refresh without
JavaScript)."""

from dataclasses import dataclass

import htpy

from mor.checkout.addresses import Address
from mor.checkout.quotes import StoredOption
from mor.checkout.records import SoldLine
from mor.entity import ENTITY
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.web.staff_layout import money_text, static_url


REFRESH_SECONDS = 2


@dataclass(frozen=True, kw_only=True)
class OrderView:
    token: str
    merchant_name: str
    merchant_support_email: str | None
    return_url: str
    lines: tuple[SoldLine, ...]
    option: StoredOption
    currency: Currency
    address: Address
    invoice_number: str | None  # None until issued
    status: str  # what the waiting script compares


def order_status(invoice_number: str | None) -> str:
    return "preparing" if invoice_number is None else "invoiced"


def _days(option: StoredOption) -> str:
    if option.transit is None:
        return ""
    low, high = option.transit
    if low == high:
        return f", {low} days"
    return f", {low}–{high} days"


def _address(address: Address) -> htpy.Node:
    shown: list[htpy.Node] = []
    for text in (
        address.name,
        address.line1,
        address.line2,
        address.city,
        address.region,
        address.postcode,
        address.country,
    ):
        if text:
            shown.append(htpy.span[text])
    return htpy.address[shown]


def _invoice(view: OrderView) -> htpy.Node:
    if view.invoice_number is None:
        return htpy.section(class_="invoice")[
            htpy.h2["Your invoice"],
            htpy.p(class_="waiting", role="status")[
                "Your invoice is being prepared. This page updates by itself."
            ],
        ]
    base = f"/o/{view.token}/invoice"
    return htpy.section(class_="invoice")[
        htpy.h2["Your invoice"],
        htpy.p[
            htpy.a(href=base)[f"View invoice {view.invoice_number}"],
            " · ",
            htpy.a(href=f"{base}.pdf")["Download it as a PDF"],
        ],
    ]


def _help(view: OrderView) -> htpy.Node:
    seller_item: htpy.Node = htpy.li[
        f"About your order or its delivery: {view.merchant_name}."
    ]
    if view.merchant_support_email is not None:
        email = view.merchant_support_email
        seller_item = htpy.li[
            f"About your order or its delivery: {view.merchant_name}, ",
            htpy.a(href=f"mailto:{email}")[email],
            ".",
        ]
    return htpy.section(class_="help")[
        htpy.h2["Questions?"],
        htpy.ul[
            seller_item,
            htpy.li[
                f"About your payment or invoice: {ENTITY.legal_name}, ",
                ENTITY.support_email,
                ".",
            ],
        ],
    ]


def render_order_page(view: OrderView) -> str:
    waiting = view.invoice_number is None
    refresh: htpy.Node = None
    script: htpy.Node = None
    if waiting:
        refresh = htpy.noscript[
            htpy.meta(http_equiv="refresh", content=str(REFRESH_SECONDS))
        ]
        script = htpy.script(src=static_url("js/waiting.js"), type="module")
    items: list[htpy.Node] = []
    for line in view.lines:
        items.append(htpy.li[f"{line.quantity} × {line.name}"])
    option = view.option
    page = htpy.html(lang="en")[
        htpy.head[
            htpy.meta(charset="utf-8"),
            htpy.meta(name="viewport", content="width=device-width, initial-scale=1"),
            htpy.meta(name="robots", content="noindex"),
            htpy.link(rel="icon", href="data:,"),
            refresh,
            htpy.title[f"Your order · {view.merchant_name}"],
            htpy.link(rel="stylesheet", href=static_url("css/buyer.css")),
            script,
        ],
        htpy.body(data_status_url=f"/o/{view.token}/status", data_status=view.status)[
            htpy.header[htpy.h1[view.merchant_name]],
            htpy.main[
                htpy.section(class_="paid")[
                    htpy.h2["Thank you, your order is paid"],
                    htpy.p[
                        "Total paid: ",
                        htpy.strong[
                            money_text(Money(option.total_minor, view.currency))
                        ],
                    ],
                ],
                htpy.section(class_="cart")[htpy.h2["Your order"], htpy.ul[items]],
                htpy.section(class_="delivery")[
                    htpy.h2["Delivery"],
                    htpy.p[f"{option.service_name}{_days(option)}, to:"],
                    _address(view.address),
                ],
                _invoice(view),
                htpy.section(class_="next")[
                    htpy.h2["What happens next"],
                    htpy.p[
                        f"{view.merchant_name} prepares and sends your order."
                        " Duties and taxes are paid: nothing more is due on"
                        " delivery."
                    ],
                ],
                _help(view),
                htpy.p[
                    htpy.a(href=view.return_url, rel="noreferrer")[
                        f"Back to {view.merchant_name}"
                    ]
                ],
            ],
            htpy.footer[
                f"Sold and invoiced by {ENTITY.legal_name} on behalf of"
                f" {view.merchant_name}."
            ],
        ],
    ]
    return str(page)

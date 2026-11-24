"""The invoice, drawn from its rows by one htpy function: as a page
(`/o/{token}/invoice`) and, through WeasyPrint, as a PDF
(docs/plan-payments.md §6). Never re-derived from the quote.

What a tax invoice must show per country, and in which words, waits for
an adviser (§9): the fields are here, the wording is a placeholder.
"""

import htpy

from mor.dates import format_day
from mor.invoices.records import Invoice, InvoiceLine, InvoiceSection
from mor.money.amount import Money
from mor.web.staff_layout import STATIC_DIR, money_text, static_url


INVOICE_CSS = STATIC_DIR / "css" / "invoice.css"

SECTION_HEADINGS: dict[InvoiceSection, str | None] = {
    InvoiceSection.GOODS: None,
    InvoiceSection.SHIPPING: None,
    InvoiceSection.TAX: "Tax",
    InvoiceSection.BORDER: "Duties and taxes paid at the border on your behalf",
}


def _row(tax_invoice: Invoice, line: InvoiceLine) -> htpy.Node:
    description: list[htpy.Node] = [line.description]
    if line.rule_text is not None:
        description.append(htpy.span(class_="rule")[line.rule_text])
    return htpy.tr[
        htpy.td[description],
        htpy.td(class_="num")["" if line.quantity is None else str(line.quantity)],
        htpy.td(class_="num")[
            money_text(Money(line.amount_minor, tax_invoice.currency))
        ],
    ]


def _address(lines: list[str | None]) -> htpy.Node:
    shown: list[htpy.Node] = []
    for text in lines:
        if text:
            shown.append(htpy.span[text])
    return htpy.address[shown]


def invoice_body(tax_invoice: Invoice) -> htpy.Node:
    rows: list[htpy.Node] = []
    for section in InvoiceSection:
        heading = SECTION_HEADINGS[section]
        in_section: list[htpy.Node] = []
        for line in tax_invoice.lines:
            if line.section == section:
                in_section.append(_row(tax_invoice, line))
        if not in_section:
            continue
        if heading is not None:
            rows.append(htpy.tr(class_="section")[htpy.th(colspan="3")[heading]])
        rows.extend(in_section)
    registration: htpy.Node = None
    if tax_invoice.seller_tax_label is not None:
        registration = htpy.p[
            f"{tax_invoice.seller_tax_label}: {tax_invoice.seller_tax_id}"
        ]
    buyer = tax_invoice.buyer
    return htpy.article(class_="invoice")[
        htpy.h1["Invoice ", tax_invoice.number_text],
        htpy.dl(class_="facts")[
            htpy.dt["Invoice number"],
            htpy.dd[tax_invoice.number_text],
            htpy.dt["Date of issue"],
            htpy.dd[format_day(tax_invoice.issued_at.date())],
            htpy.dt["Paid"],
            htpy.dd[format_day(tax_invoice.paid_at.date())],
        ],
        htpy.section(class_="parties")[
            htpy.div[
                htpy.h2["Seller"],
                _address([tax_invoice.seller_name, tax_invoice.seller_address]),
                registration,
                # "Co., Ltd." ends the sentence itself.
                htpy.p[
                    f"Sold on behalf of {tax_invoice.merchant_legal_name.rstrip('.')}."
                ],
            ],
            htpy.div[
                htpy.h2["Buyer"],
                _address(
                    [
                        buyer.name,
                        buyer.line1,
                        buyer.line2,
                        buyer.city,
                        buyer.region,
                        buyer.postcode,
                        buyer.country,
                    ]
                ),
            ],
        ],
        htpy.table(class_="lines")[
            htpy.thead[
                htpy.tr[
                    htpy.th["Description"],
                    htpy.th(class_="num")["Qty"],
                    htpy.th(class_="num")[f"Amount ({tax_invoice.currency.value})"],
                ]
            ],
            htpy.tbody[rows],
            htpy.tfoot[
                htpy.tr(class_="total")[
                    htpy.th(colspan="2")["Total paid"],
                    htpy.td(class_="num")[
                        money_text(Money(tax_invoice.total_minor, tax_invoice.currency))
                    ],
                ]
            ],
        ],
        htpy.p(class_="note")[
            "Item prices include our fees. Nothing more is due on delivery."
        ],
    ]


def invoice_document(tax_invoice: Invoice, for_pdf: bool) -> str:
    """The whole document. The page links the stylesheet; the PDF is given
    it by WeasyPrint, which may fetch nothing."""
    stylesheet: htpy.Node = None
    if not for_pdf:
        stylesheet = htpy.link(rel="stylesheet", href=static_url("css/invoice.css"))
    page = htpy.html(lang="en")[
        htpy.head[
            htpy.meta(charset="utf-8"),
            htpy.meta(name="viewport", content="width=device-width, initial-scale=1"),
            htpy.meta(name="robots", content="noindex"),
            htpy.link(rel="icon", href="data:,"),
            htpy.title[f"Invoice {tax_invoice.number_text}"],
            stylesheet,
        ],
        htpy.body[invoice_body(tax_invoice)],
    ]
    return str(page)

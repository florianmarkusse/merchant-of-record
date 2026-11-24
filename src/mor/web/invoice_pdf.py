"""The invoice as a PDF, through WeasyPrint (docs/plan-payments.md §6.1),
from the same HTML as the page. Rendering blocks for a while, so callers
run it in a thread.

WeasyPrint gets a URL fetcher that allows no protocol at all: nothing in
an invoice (a merchant's line names, a buyer's address) can make the
server fetch a URL or read a file. The stylesheet is handed over as text.
"""

from weasyprint import CSS, HTML
from weasyprint.urls import URLFetcher

from mor.invoices.records import Invoice
from mor.web.invoice_html import INVOICE_CSS, invoice_document


def invoice_pdf(tax_invoice: Invoice) -> bytes:
    fetcher = URLFetcher(allowed_protocols=())
    stylesheet = CSS(string=INVOICE_CSS.read_text(), url_fetcher=fetcher)
    document = HTML(string=invoice_document(tax_invoice, True), url_fetcher=fetcher)
    return document.write_pdf(stylesheets=[stylesheet])

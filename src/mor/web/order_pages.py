"""The buyer's order page and invoice handlers (`/o/{token}`,
docs/plan-payments.md §6.1). The token is the link's bearer secret: 32
random bytes, no cookies, no expiry for now (revisited with the notify
slice). All GET, so there is no form to guard.

The token is in the URL, so every answer here says `Referrer-Policy:
no-referrer` (no link out may hand it to another site), and pages are
neither indexed nor cached. The address is personal data: never logged.
"""

import asyncio

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from mor.checkout.quotes import read_made_quote
from mor.checkout.sessions import read_address, session_by_id
from mor.config import Settings
from mor.db.connect import connect
from mor.invoices.store import invoice_for_order
from mor.merchants.store import read_merchant
from mor.orders.store import order_by_token
from mor.web.invoice_html import invoice_document
from mor.web.invoice_pdf import invoice_pdf
from mor.web.order_html import OrderView, order_status, render_order_page


PRIVATE = {
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex",
    "Cache-Control": "no-store",
}


def _not_found() -> Response:
    return Response("This order link is not valid.", status_code=404, headers=PRIVATE)


async def order_page(request: Request, settings: Settings) -> Response:
    token = request.path_params["token"]
    async with await connect(settings.database_url) as conn:
        order = await order_by_token(conn, token)
        if order is None:
            return _not_found()
        session = await session_by_id(conn, order.session_id)
        assert session is not None
        seller = await read_merchant(conn, order.merchant_id)
        assert seller is not None
        quote = await read_made_quote(conn, order.quote_id)
        address = await read_address(conn, order.address_id)
        assert address is not None
        tax_invoice = await invoice_for_order(conn, order.id)
    number = None if tax_invoice is None else tax_invoice.number_text
    view = OrderView(
        token=order.buyer_token,
        merchant_name=seller.display_name,
        merchant_support_email=seller.support_email,
        return_url=session.return_url,
        lines=session.lines,
        option=quote.options[order.option_no],
        currency=quote.currency,
        address=address,
        invoice_number=number,
        status=order_status(number),
    )
    return HTMLResponse(render_order_page(view), headers=PRIVATE)


async def order_status_route(request: Request, settings: Settings) -> Response:
    """For the waiting script: whether the invoice is issued yet."""
    async with await connect(settings.database_url) as conn:
        order = await order_by_token(conn, request.path_params["token"])
        if order is None:
            return _not_found()
        tax_invoice = await invoice_for_order(conn, order.id)
    number = None if tax_invoice is None else tax_invoice.number_text
    return JSONResponse({"status": order_status(number)}, headers=PRIVATE)


async def invoice_page(request: Request, settings: Settings) -> Response:
    async with await connect(settings.database_url) as conn:
        order = await order_by_token(conn, request.path_params["token"])
        if order is None:
            return _not_found()
        tax_invoice = await invoice_for_order(conn, order.id)
    if tax_invoice is None:
        return _not_found()
    return HTMLResponse(invoice_document(tax_invoice, False), headers=PRIVATE)


async def invoice_pdf_route(request: Request, settings: Settings) -> Response:
    async with await connect(settings.database_url) as conn:
        order = await order_by_token(conn, request.path_params["token"])
        if order is None:
            return _not_found()
        tax_invoice = await invoice_for_order(conn, order.id)
    if tax_invoice is None:
        return _not_found()
    pdf = await asyncio.to_thread(invoice_pdf, tax_invoice)
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            **PRIVATE,
            "Content-Disposition": f'inline; filename="{tax_invoice.number_text}.pdf"',
        },
    )

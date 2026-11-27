"""The route table: every route of the web app, in one place.

Outside prod the fakes' routes (under `/_fake/`) and the test setup's page
are part of the same app (plan-architecture.md §6, plan-test-setup.md §4),
so local development is one web server.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import httpx2
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import Lifespan

import mor.psp.registry
from mor.config import Environment, Settings
from mor.web import (
    api,
    buyer,
    csp,
    order_pages,
    sandbox,
    staff_bank,
    staff_classifications,
    staff_markets,
    staff_settlement,
    staff_shipments,
    webhooks,
)
from mor.web.staff_layout import STATIC_DIR


class FakesInProd(Exception):
    """The fakes were handed to a prod app: a wiring bug."""


@dataclass(frozen=True)
class Fakes:
    """The fake vendors' app, and the client that reaches them over HTTP
    (the sandbox page's vendor buttons call their API with it)."""

    app: Starlette
    http: httpx2.AsyncClient


def make_app(
    settings: Settings,
    fakes: Fakes | None,
    clock: Callable[[], datetime],
    lifespan: Lifespan[Starlette] | None = None,
) -> Starlette:
    """`clock` gives the current time, aware UTC: `datetime.now(UTC)` when
    serving, a controlled one in tests, so time is an argument here too."""
    url = settings.database_url

    async def echo_webhook(request: Request) -> Response:
        return await webhooks.echo_webhook(request, url, clock())

    async def psp_webhook(request: Request) -> Response:
        return await webhooks.psp_webhook(request, url, clock())

    async def carrier_webhook(request: Request) -> Response:
        return await webhooks.carrier_webhook(request, url, clock())

    async def api_create_checkout_session(request: Request) -> Response:
        return await api.create_checkout_session(request, settings, clock())

    async def api_get_checkout_session(request: Request) -> Response:
        return await api.get_checkout_session(request, settings)

    async def api_create_estimate(request: Request) -> Response:
        return await api.create_estimate(request, settings, clock())

    async def api_get_order(request: Request) -> Response:
        return await api.get_order(request, settings)

    async def api_get_order_label(request: Request) -> Response:
        return await api.get_order_label(request, settings)

    async def api_get_merchant(request: Request) -> Response:
        return await api.get_merchant(request, settings)

    async def api_merchant_settings(request: Request) -> Response:
        return await api.post_merchant_settings(request, settings, clock())

    async def buyer_page(request: Request) -> Response:
        return await buyer.buyer_page(request, settings, clock())

    async def buyer_address(request: Request) -> Response:
        return await buyer.post_address(request, settings, clock())

    async def buyer_refresh(request: Request) -> Response:
        return await buyer.post_refresh(request, settings, clock())

    async def buyer_pay(request: Request) -> Response:
        return await buyer.post_pay(request, settings, clock())

    async def buyer_another_way(request: Request) -> Response:
        return await buyer.post_another_way(request, settings, clock())

    async def buyer_status(request: Request) -> Response:
        return await buyer.get_status(request, settings)

    async def order_page(request: Request) -> Response:
        return await order_pages.order_page(request, settings)

    async def order_status(request: Request) -> Response:
        return await order_pages.order_status_route(request, settings)

    async def invoice_page(request: Request) -> Response:
        return await order_pages.invoice_page(request, settings)

    async def invoice_pdf(request: Request) -> Response:
        return await order_pages.invoice_pdf_route(request, settings)

    async def staff_quote_page(request: Request) -> Response:
        return await staff_markets.quote_page(request, url, clock())

    async def staff_answers_page(request: Request) -> Response:
        return await staff_markets.answers_page(url, clock())

    async def staff_data_page(request: Request) -> Response:
        return await staff_markets.data_page(request, url, clock())

    async def staff_sessions_page(request: Request) -> Response:
        return await staff_markets.sessions_page(url, clock())

    async def staff_session_page(request: Request) -> Response:
        return await staff_markets.session_page(request, url)

    async def staff_ask_vendor(request: Request) -> Response:
        return await staff_markets.ask_vendor(request, url, clock())

    async def staff_ask_carrier(request: Request) -> Response:
        return await staff_markets.ask_carrier(request, url, clock())

    async def staff_settlement_accounts(request: Request) -> Response:
        return await staff_settlement.accounts_page(url)

    async def staff_settlement_account(request: Request) -> Response:
        return await staff_settlement.account_page(request, url, clock())

    async def staff_settlement_resolve(request: Request) -> Response:
        return await staff_settlement.resolve(request, url, clock())

    async def staff_settlement_payout_note(request: Request) -> Response:
        return await staff_settlement.note_on_payout(request, url, clock())

    async def staff_bank_accounts(request: Request) -> Response:
        return await staff_bank.accounts_page(url)

    async def staff_bank_account(request: Request) -> Response:
        return await staff_bank.account_page(request, url)

    async def staff_bank_link(request: Request) -> Response:
        return await staff_bank.link_entry(request, url, clock())

    async def staff_bank_note(request: Request) -> Response:
        return await staff_bank.note_on_entry(request, url, clock())

    async def staff_classifications_page(request: Request) -> Response:
        return await staff_classifications.page(request, url)

    async def staff_classify(request: Request) -> Response:
        return await staff_classifications.set_code(request, url, clock())

    async def staff_shipments_page(request: Request) -> Response:
        return await staff_shipments.page(request, url)

    async def staff_book_again(request: Request) -> Response:
        return await staff_shipments.book(request, url, clock())

    routes: list[BaseRoute] = [
        Route("/webhooks/echo/{account_id}", echo_webhook, methods=["POST"]),
        Route("/webhooks/psp/{account_id}", psp_webhook, methods=["POST"]),
        Route("/webhooks/carrier/{account_id}", carrier_webhook, methods=["POST"]),
        # The merchant API, in every environment: each has its own keys.
        Route("/v1/checkout_sessions", api_create_checkout_session, methods=["POST"]),
        Route(
            "/v1/checkout_sessions/{session_id}",
            api_get_checkout_session,
            methods=["GET"],
        ),
        Route("/v1/estimates", api_create_estimate, methods=["POST"]),
        Route("/v1/orders/{order_id}", api_get_order, methods=["GET"]),
        Route("/v1/orders/{order_id}/label", api_get_order_label, methods=["GET"]),
        Route("/v1/merchant", api_get_merchant, methods=["GET"]),
        Route("/v1/merchant/settings", api_merchant_settings, methods=["POST"]),
        # The buyer's checkout page: its link is its own bearer token.
        Route("/c/{session_id}", buyer_page, methods=["GET"]),
        Route("/c/{session_id}/address", buyer_address, methods=["POST"]),
        Route("/c/{session_id}/refresh", buyer_refresh, methods=["POST"]),
        Route("/c/{session_id}/pay", buyer_pay, methods=["POST"]),
        Route("/c/{session_id}/pay-another-way", buyer_another_way, methods=["POST"]),
        Route("/c/{session_id}/status", buyer_status, methods=["GET"]),
        # The buyer's order page and invoice: its token is the bearer secret.
        Route("/o/{token}", order_page, methods=["GET"]),
        Route("/o/{token}/status", order_status, methods=["GET"]),
        Route("/o/{token}/invoice", invoice_page, methods=["GET"]),
        Route("/o/{token}/invoice.pdf", invoice_pdf, methods=["GET"]),
        # Stylesheets and compiled scripts (static/js/ comes from ts/).
        Mount(
            "/static",
            StaticFiles(directory=STATIC_DIR, check_dir=False),
            name="static",
        ),
    ]
    # Staff pages: outside prod only until staff sign-in exists
    # (plan-quote.md §5b); then mounted in prod behind it.
    if settings.environment != Environment.PROD:
        routes.extend(
            [
                Route("/staff/markets", staff_quote_page, methods=["GET"]),
                Route("/staff/markets/answers", staff_answers_page, methods=["GET"]),
                Route("/staff/markets/data", staff_data_page, methods=["GET"]),
                Route("/staff/markets/sessions", staff_sessions_page, methods=["GET"]),
                Route(
                    "/staff/markets/sessions/{session_id}",
                    staff_session_page,
                    methods=["GET"],
                ),
                Route(
                    "/staff/markets/duty_lookups", staff_ask_vendor, methods=["POST"]
                ),
                Route(
                    "/staff/markets/shipping_prices",
                    staff_ask_carrier,
                    methods=["POST"],
                ),
                Route("/staff/settlement", staff_settlement_accounts, methods=["GET"]),
                Route(
                    "/staff/settlement/{account_id}",
                    staff_settlement_account,
                    methods=["GET"],
                ),
                Route(
                    "/staff/settlement/{account_id}/exceptions/{exception_id}",
                    staff_settlement_resolve,
                    methods=["POST"],
                ),
                Route(
                    "/staff/settlement/{account_id}/payouts/{payout_id}/note",
                    staff_settlement_payout_note,
                    methods=["POST"],
                ),
                Route(
                    "/staff/classifications",
                    staff_classifications_page,
                    methods=["GET"],
                ),
                Route("/staff/classifications", staff_classify, methods=["POST"]),
                Route("/staff/shipments", staff_shipments_page, methods=["GET"]),
                Route(
                    "/staff/shipments/{shipment_id}/book",
                    staff_book_again,
                    methods=["POST"],
                ),
                Route("/staff/bank", staff_bank_accounts, methods=["GET"]),
                Route("/staff/bank/{account_id}", staff_bank_account, methods=["GET"]),
                Route(
                    "/staff/bank/{account_id}/entries/{entry_id}/link",
                    staff_bank_link,
                    methods=["POST"],
                ),
                Route(
                    "/staff/bank/{account_id}/entries/{entry_id}/note",
                    staff_bank_note,
                    methods=["POST"],
                ),
            ]
        )
    if fakes is not None:
        if settings.environment == Environment.PROD:
            raise FakesInProd("the fakes never run in prod")
        http = fakes.http

        async def sandbox_page(request: Request) -> Response:
            return await sandbox.show(request, url)

        async def sandbox_job(request: Request) -> Response:
            return await sandbox.create_job(request, url, clock)

        async def sandbox_manual(request: Request) -> Response:
            return await sandbox.record_by_hand(request, url, clock)

        async def sandbox_release(request: Request) -> Response:
            return await sandbox.release(request, url, http)

        async def sandbox_redeliver(request: Request) -> Response:
            return await sandbox.redeliver(request, url, http)

        async def sandbox_cut(request: Request) -> Response:
            return await sandbox.cut(request, url, http)

        async def sandbox_fault(request: Request) -> Response:
            return await sandbox.fault(request, settings.self_url, http)

        async def sandbox_settlement(request: Request) -> Response:
            return await sandbox.cut_settlement(request, url, http)

        async def sandbox_bank_book(request: Request) -> Response:
            return await sandbox.book_at_bank(request, url, http)

        routes.extend(
            [
                Route("/_fake/", sandbox_page, methods=["GET"]),
                Route("/_fake/sandbox/jobs", sandbox_job, methods=["POST"]),
                Route("/_fake/sandbox/manual", sandbox_manual, methods=["POST"]),
                Route("/_fake/sandbox/release", sandbox_release, methods=["POST"]),
                Route("/_fake/sandbox/redeliver", sandbox_redeliver, methods=["POST"]),
                Route("/_fake/sandbox/cut", sandbox_cut, methods=["POST"]),
                Route("/_fake/sandbox/fault", sandbox_fault, methods=["POST"]),
                Route(
                    "/_fake/sandbox/settlement", sandbox_settlement, methods=["POST"]
                ),
                Route("/_fake/sandbox/bank-book", sandbox_bank_book, methods=["POST"]),
            ]
        )
        routes.extend(fakes.app.routes)
    header = csp.policy(mor.psp.registry.browser_origins(settings))
    return Starlette(
        routes=routes,
        middleware=[Middleware(csp.with_policy, header=header)],
        lifespan=lifespan,
    )

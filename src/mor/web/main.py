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
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles

from mor.config import Environment, Settings
from mor.web import sandbox, staff_markets, webhooks
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
    settings: Settings, fakes: Fakes | None, clock: Callable[[], datetime]
) -> Starlette:
    """`clock` gives the current time, aware UTC: `datetime.now(UTC)` when
    serving, a controlled one in tests, so time is an argument here too."""
    url = settings.database_url

    async def echo_webhook(request: Request) -> Response:
        return await webhooks.echo_webhook(request, url, clock())

    async def staff_quote_page(request: Request) -> Response:
        return await staff_markets.quote_page(request, url, clock())

    async def staff_answers_page(request: Request) -> Response:
        return await staff_markets.answers_page(url)

    async def staff_data_page(request: Request) -> Response:
        return await staff_markets.data_page(request, url, clock())

    async def staff_ask_vendor(request: Request) -> Response:
        return await staff_markets.ask_vendor(request, url, clock())

    routes: list[BaseRoute] = [
        Route("/webhooks/echo/{account_id}", echo_webhook, methods=["POST"]),
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
                Route(
                    "/staff/markets/duty_lookups", staff_ask_vendor, methods=["POST"]
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

        routes.extend(
            [
                Route("/_fake/", sandbox_page, methods=["GET"]),
                Route("/_fake/sandbox/jobs", sandbox_job, methods=["POST"]),
                Route("/_fake/sandbox/manual", sandbox_manual, methods=["POST"]),
                Route("/_fake/sandbox/release", sandbox_release, methods=["POST"]),
                Route("/_fake/sandbox/redeliver", sandbox_redeliver, methods=["POST"]),
                Route("/_fake/sandbox/cut", sandbox_cut, methods=["POST"]),
                Route("/_fake/sandbox/fault", sandbox_fault, methods=["POST"]),
            ]
        )
        routes.extend(fakes.app.routes)
    return Starlette(routes=routes)

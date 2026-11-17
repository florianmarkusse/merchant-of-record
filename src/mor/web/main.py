"""The route table: every route of the web app, in one place.

Outside prod the fakes' routes (under `/_fake/`) are part of the same app
(plan-architecture.md §6), so local development is one web server.
"""

from collections.abc import Callable
from datetime import datetime

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from mor.config import Environment, Settings
from mor.web import webhooks


class FakesInProd(Exception):
    """The fakes were handed to a prod app: a wiring bug."""


def make_app(
    settings: Settings, fakes: Starlette | None, clock: Callable[[], datetime]
) -> Starlette:
    """`clock` gives the current time, aware UTC: `datetime.now(UTC)` when
    serving, a controlled one in tests, so time is an argument here too."""

    async def echo_webhook(request: Request) -> Response:
        return await webhooks.echo_webhook(request, settings.database_url, clock())

    routes: list[BaseRoute] = [
        Route("/webhooks/echo/{account_id}", echo_webhook, methods=["POST"]),
    ]
    if fakes is not None:
        if settings.environment == Environment.PROD:
            raise FakesInProd("the fakes never run in prod")
        routes.extend(fakes.routes)
    return Starlette(routes=routes)

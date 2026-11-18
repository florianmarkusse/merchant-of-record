"""Every fake vendor in one app, mounted under `/_fake/` outside prod
(plan-architecture.md §6)."""

import httpx2
from starlette.applications import Starlette
from starlette.routing import BaseRoute

import mor.fakes.duty
import mor.fakes.echo


def make_app(database_url: str, outgoing: httpx2.AsyncClient) -> Starlette:
    """`outgoing` carries the fakes' webhooks: a real client in dev, our app
    in-process in tests."""
    routes: list[BaseRoute] = []
    routes.extend(mor.fakes.echo.make_app(database_url, outgoing).routes)
    routes.extend(mor.fakes.duty.make_app(database_url).routes)
    return Starlette(routes=routes)

"""Every fake vendor in one app, mounted under `/_fake/` outside prod
(plan-architecture.md §6)."""

from collections.abc import Callable
from datetime import datetime

import httpx2
from starlette.applications import Starlette
from starlette.routing import BaseRoute

import mor.fakes.courier
import mor.fakes.duty
import mor.fakes.echo
import mor.fakes.psp


def make_app(
    database_url: str, outgoing: httpx2.AsyncClient, clock: Callable[[], datetime]
) -> Starlette:
    """`outgoing` carries the fakes' webhooks: a real client in dev, our app
    in-process in tests. `clock` is the vendors' own time (a QR's expiry);
    tests share theirs with it."""
    routes: list[BaseRoute] = []
    routes.extend(mor.fakes.echo.make_app(database_url, outgoing).routes)
    routes.extend(mor.fakes.duty.make_app(database_url).routes)
    routes.extend(mor.fakes.courier.make_app(database_url).routes)
    routes.extend(mor.fakes.psp.make_app(database_url, outgoing, clock).routes)
    return Starlette(routes=routes)

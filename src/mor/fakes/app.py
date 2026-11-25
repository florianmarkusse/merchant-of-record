"""Every fake vendor in one app, mounted under `/_fake/` outside prod
(plan-architecture.md §6)."""

from collections.abc import Callable
from datetime import datetime

import httpx2
from starlette.applications import Starlette
from starlette.routing import BaseRoute

import mor.fakes.bank
import mor.fakes.carrier
import mor.fakes.duty
import mor.fakes.echo
import mor.fakes.psp
from mor.fakes.hang import Hang


def make_app(
    database_url: str,
    outgoing: httpx2.AsyncClient,
    clock: Callable[[], datetime],
    hang: Hang,
) -> Starlette:
    """`outgoing` carries the fakes' webhooks: a real client in dev, our app
    in-process in tests. `clock` is the vendors' own time (a QR's expiry, a
    call's log);
    tests share theirs with it. `hang` is how a vendor never answers
    (`mor.fakes.hang`)."""
    routes: list[BaseRoute] = []
    routes.extend(mor.fakes.echo.make_app(database_url, outgoing, hang).routes)
    routes.extend(mor.fakes.duty.make_app(database_url, clock, hang).routes)
    routes.extend(mor.fakes.carrier.make_app(database_url, clock, hang).routes)
    routes.extend(mor.fakes.psp.make_app(database_url, outgoing, clock, hang).routes)
    routes.extend(mor.fakes.bank.make_app(database_url, clock, hang).routes)
    return Starlette(routes=routes)

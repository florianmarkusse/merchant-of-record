"""Serve the web app (docs/plan-test-setup.md §2).

    ENVIRONMENT=test DATABASE_URL=postgresql:///mor_sandbox \\
        uv run python -m mor.web.serve

Listens on SELF_URL's host and port. Outside prod the fakes are part of
the app, and their webhooks come back to SELF_URL over real HTTP, as a
vendor's would. Outside prod uvicorn reloads it when a file under src/
changes (`--reload`, which needs the app as a factory it imports by name);
a reload does not migrate, load markets or seed.
"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx2
import uvicorn
from starlette.applications import Starlette

import mor.fakes.app
from mor.config import Environment, load_settings
from mor.web.main import Fakes, make_app


# Longer than any handler of ours takes; a fake's webhook that gets no
# answer in time counts as failed and is redelivered.
WEBHOOK_TIMEOUT_SECONDS = 10
SOURCE = Path(__file__).resolve().parent.parent.parent  # src/


def _now() -> datetime:
    return datetime.now(UTC)


def create_app() -> Starlette:
    """uvicorn's app factory: settings from the environment, and the
    outgoing client closed when the app shuts down."""
    settings = load_settings(os.environ)
    outgoing = httpx2.AsyncClient(timeout=WEBHOOK_TIMEOUT_SECONDS)
    fakes = None
    if settings.environment != Environment.PROD:
        fakes = Fakes(mor.fakes.app.make_app(settings.database_url, outgoing), outgoing)

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None]:
        yield
        await outgoing.aclose()

    return Starlette(routes=make_app(settings, fakes, _now).routes, lifespan=lifespan)


if __name__ == "__main__":
    settings = load_settings(os.environ)
    address = urlsplit(settings.self_url)
    assert address.hostname is not None and address.port is not None, (
        f"SELF_URL needs a host and a port, got {settings.self_url!r}"
    )
    uvicorn.run(
        "mor.web.serve:create_app",
        factory=True,
        host=address.hostname,
        port=address.port,
        reload=settings.environment != Environment.PROD,
        reload_dirs=[str(SOURCE)],
    )

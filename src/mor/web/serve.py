"""Serve the web app (docs/plan-test-setup.md §2).

    ENVIRONMENT=test DATABASE_URL=postgresql:///mor_sandbox \\
        uv run python -m mor.web.serve

Listens on SELF_URL's host and port. Outside prod the fakes are part of
the app, and their webhooks come back to SELF_URL over real HTTP, as a
vendor's would.
"""

import asyncio
import os
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx2
import uvicorn

import mor.fakes.echo
from mor.config import Environment, Settings, load_settings
from mor.web.main import Fakes, make_app


# Longer than any handler of ours takes; a fake's webhook that gets no
# answer in time counts as failed and is redelivered.
WEBHOOK_TIMEOUT_SECONDS = 10


def _now() -> datetime:
    return datetime.now(UTC)


async def serve(settings: Settings) -> None:
    address = urlsplit(settings.self_url)
    assert address.hostname is not None and address.port is not None, (
        f"SELF_URL needs a host and a port, got {settings.self_url!r}"
    )
    async with httpx2.AsyncClient(timeout=WEBHOOK_TIMEOUT_SECONDS) as outgoing:
        fakes = None
        if settings.environment != Environment.PROD:
            fakes = Fakes(
                mor.fakes.echo.make_app(settings.database_url, outgoing), outgoing
            )
        app = make_app(settings, fakes, _now)
        config = uvicorn.Config(app, host=address.hostname, port=address.port)
        await uvicorn.Server(config).serve()


if __name__ == "__main__":
    asyncio.run(serve(load_settings(os.environ)))

"""Seed the test setup with synthetic accounts (docs/plan-test-setup.md §3).

    ENVIRONMENT=test DATABASE_URL=postgresql:///mor_sandbox \\
        uv run python -m mor.seed

One echo account per provider and event mode, each its own vendor-side
account at the fake under SELF_URL. The server must be running: push
accounts subscribe their webhooks at the fake over HTTP. Refused in prod.
Running it again creates nothing and subscribes again, which the fake
treats as an update.

Transitional with the echo flow: the seed grows a merchant per mode
combination as the real ports arrive (plan-architecture.md §6).
"""

import asyncio
import os
import secrets
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx2

from mor.audit import Actor, ActorKind
from mor.config import Environment, Settings, load_settings
from mor.db.connect import Connection, connect
from mor.echo.flow import create_echo_account
from mor.echo.port import EchoProvider, EventMode
from mor.echo.reports import start_report_schedule


SEEDER = Actor(ActorKind.SYSTEM, "seed")
TIMEOUT = timedelta(seconds=5)
POLL_INTERVAL = timedelta(minutes=1)
# One seed at a time: a second waits for the first to commit, then finds
# its accounts. Released with the transaction.
SEED_LOCK = "seed:echo_accounts"


@dataclass(frozen=True)
class SeedInProd:
    """The seed was started against prod: an operator mistake."""


@dataclass(frozen=True)
class FakeUnreachable:
    """The fake did not take a subscription; most likely the server is not
    running at SELF_URL."""

    detail: str


@dataclass(frozen=True)
class SeededAccount:
    account_id: uuid.UUID
    base_url: str
    event_mode: EventMode
    webhook_secret: str
    created: bool


def _flavour(provider: EchoProvider) -> str:
    match provider:
        case EchoProvider.FAKE_KEYED:
            return "keyed"
        case EchoProvider.FAKE_CANCELLABLE:
            return "cancellable"
        case EchoProvider.FAKE_PLAIN:
            return "plain"


def base_url(self_url: str, provider: EchoProvider, mode: EventMode) -> str:
    return f"{self_url}/_fake/echo/{_flavour(provider)}/sandbox-{mode.value}"


async def _seed_account(
    conn: Connection, url: str, provider: EchoProvider, mode: EventMode, now: datetime
) -> SeededAccount:
    cur = await conn.execute(
        "SELECT id, webhook_secret FROM echo_account WHERE base_url = %s", (url,)
    )
    row = await cur.fetchone()
    if row is not None:
        return SeededAccount(row[0], url, mode, row[1], created=False)
    secret = secrets.token_hex(16)
    account_id = await create_echo_account(
        conn,
        provider,
        url,
        TIMEOUT,
        event_mode=mode,
        webhook_secret=secret,
        poll_interval=POLL_INTERVAL,
    )
    if mode == EventMode.FILE:
        await start_report_schedule(conn, account_id, SEEDER, now)
    return SeededAccount(account_id, url, mode, secret, created=True)


async def seed_accounts(
    conn: Connection, settings: Settings, now: datetime
) -> list[SeededAccount] | SeedInProd:
    """Our side of the seed, in the caller's transaction. Never commits."""
    if settings.environment == Environment.PROD:
        return SeedInProd()
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (SEED_LOCK,)
    )
    seeded: list[SeededAccount] = []
    for provider in EchoProvider:
        for mode in EventMode:
            url = base_url(settings.self_url, provider, mode)
            seeded.append(await _seed_account(conn, url, provider, mode, now))
    return seeded


async def subscribe_webhooks(
    http: httpx2.AsyncClient, settings: Settings, seeded: list[SeededAccount]
) -> FakeUnreachable | None:
    """The vendor side: register each push account's webhook at the fake,
    as one would on a vendor's dashboard."""
    for account in seeded:
        match account.event_mode:
            case EventMode.PUSH:
                payload = "full"
            case EventMode.PUSH_THIN:
                payload = "thin"
            case EventMode.POLL | EventMode.FILE | EventMode.NONE:
                continue
        try:
            response = await http.post(
                f"{account.base_url}/webhooks",
                json={
                    "url": f"{settings.self_url}/webhooks/echo/{account.account_id}",
                    "secret": account.webhook_secret,
                    "payload": payload,
                },
            )
        except httpx2.TransportError as error:
            return FakeUnreachable(repr(error))
        if response.status_code != 204:
            return FakeUnreachable(f"status {response.status_code}")
    return None


async def _main() -> int:
    settings = load_settings(os.environ)
    async with await connect(settings.database_url) as conn:
        seeded = await seed_accounts(conn, settings, datetime.now(UTC))
        if isinstance(seeded, SeedInProd):
            print("refused: the seed never runs in prod")
            return 1
        await conn.commit()
    async with httpx2.AsyncClient() as http:
        unreachable = await subscribe_webhooks(http, settings, seeded)
    if unreachable is not None:
        print(f"accounts seeded, webhooks not subscribed: {unreachable.detail}")
        print(f"is the server running at {settings.self_url}? Then seed again.")
        return 1
    created = 0
    for account in seeded:
        if account.created:
            created += 1
    print(f"{len(seeded)} echo accounts, {created} new; webhooks subscribed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))

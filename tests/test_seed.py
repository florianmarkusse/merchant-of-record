"""The test setup's seed (docs/plan-test-setup.md §3), through our app and
the fake in-process: what it writes, that a second run changes nothing,
and that a seeded push account really receives the vendor's events."""

import asyncio
from datetime import UTC, datetime

from mor.audit import Actor, ActorKind
from mor.config import Environment, Settings
from mor.couriers.prices import courier_account
from mor.couriers.records import PriceExpiry
from mor.db.connect import connect
from mor.db.fetch import one_value
from mor.echo.flow import create_echo_job
from mor.echo.port import EchoProvider, EventMode
from mor.markets.store import load_market
from mor.merchants.records import KeyEnv, NewKey
from mor.merchants.store import authenticate
from mor.messaging.worker import Failed, drain_once
from mor.seed import (
    DEFAULT_PSP,
    SeededAccount,
    SeedInProd,
    base_url,
    seed_accounts,
    seed_couriers,
    seed_merchant,
    seed_psp_accounts,
    set_up_fake_psp,
    subscribe_webhooks,
)
from tests.helpers import ENV, Wired, count_rows, wire
from tests.quote.fixtures import live_market


NOW = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
# In-process, the host is ignored: the fakes and our app share one app.
SELF_URL = "http://mor"


async def _seed(url: str, wired: Wired) -> list[SeededAccount]:
    settings = Settings(ENV, url, SELF_URL)
    async with await connect(url) as conn:
        seeded = await seed_accounts(conn, settings, NOW)
        assert not isinstance(seeded, SeedInProd)
        await conn.commit()
    assert await subscribe_webhooks(wired.control, settings, seeded) is None
    return seeded


def test_the_seed_makes_one_account_per_provider_and_mode_once(
    database_url: str,
) -> None:
    async def body() -> None:
        wired = wire(database_url, lambda: NOW)
        async with wired.http, wired.control:
            first = await _seed(database_url, wired)
            again = await _seed(database_url, wired)
        modes = len(EventMode)
        assert len(first) == len(EchoProvider) * modes
        assert all(account.created for account in first)
        assert not any(account.created for account in again)
        accounts = "SELECT count(*) FROM echo_account"
        assert await count_rows(database_url, accounts) == len(EchoProvider) * modes
        looks = "SELECT count(*) FROM outbox WHERE message_type = 'echo_report_due'"
        assert await count_rows(database_url, looks) == len(EchoProvider)
        subscriptions = "SELECT count(*) FROM fake_vendor.echo_webhook"
        assert await count_rows(database_url, subscriptions) == len(EchoProvider) * 2

    asyncio.run(body())


def test_the_seed_is_refused_in_prod(database_url: str) -> None:
    async def body() -> None:
        settings = Settings(Environment.PROD, database_url, SELF_URL)
        async with await connect(database_url) as conn:
            assert await seed_accounts(conn, settings, NOW) == SeedInProd()

    asyncio.run(body())
    assert (
        asyncio.run(count_rows(database_url, "SELECT count(*) FROM echo_account")) == 0
    )


def test_a_seeded_push_account_receives_the_vendors_events(database_url: str) -> None:
    async def body() -> None:
        wired = wire(database_url, lambda: NOW)
        async with (
            wired.http,
            wired.control,
            await connect(database_url, autocommit=True) as worker,
        ):
            seeded = await _seed(database_url, wired)
            url = base_url(SELF_URL, EchoProvider.FAKE_KEYED, EventMode.PUSH)
            account = [a for a in seeded if a.base_url == url][0]
            async with await connect(database_url) as conn:
                job_id = await create_echo_job(
                    conn, account.account_id, "hi", Actor(ActorKind.SYSTEM, "t"), NOW
                )
                await conn.commit()
            for _ in range(5):
                for result in await drain_once(worker, wired.http, ENV, NOW):
                    assert not isinstance(result, Failed), result
            async with await connect(database_url) as conn:
                vendor_echo_id = await one_value(
                    conn,
                    "SELECT vendor_echo_id FROM echo_job WHERE id = %s",
                    (job_id,),
                    str,
                )
            response = await wired.control.post(
                f"{url}/echoes/{vendor_echo_id}/events/release", json={}
            )
            assert response.json()["push"] == "delivered"
            for _ in range(5):
                await drain_once(worker, wired.http, ENV, NOW)
            async with await connect(database_url) as conn:
                cur = await conn.execute(
                    "SELECT vendor_status::text FROM echo_job WHERE id = %s",
                    (job_id,),
                )
                assert await cur.fetchone() == ("confirmed",)

    asyncio.run(body())


def test_each_carrier_on_a_live_route_gets_one_account_at_the_fake(
    database_url: str,
) -> None:
    async def body() -> None:
        settings = Settings(ENV, database_url, SELF_URL)
        async with await connect(database_url) as conn:
            await load_market(
                conn, live_market(), "live", Actor(ActorKind.SYSTEM, "t"), NOW
            )
            first = await seed_couriers(conn, settings)
            again = await seed_couriers(conn, settings)
            account = await courier_account(conn, "carrier")
            await conn.commit()
        assert first == ["carrier"] and again == []
        assert account is not None
        assert account.base_url == "http://mor/_fake/courier/sandbox-carrier"
        assert account.price_expiry == PriceExpiry.CARRIER_FIRST

    asyncio.run(body())


def test_the_sandbox_merchant_gets_one_key_shown_once(database_url: str) -> None:
    async def body() -> None:
        settings = Settings(ENV, database_url, SELF_URL)
        async with await connect(database_url) as conn:
            first = await seed_merchant(conn, settings, NOW)
            again = await seed_merchant(conn, settings, NOW)
            assert isinstance(first, NewKey) and again is None
            found = await authenticate(conn, f"Bearer {first.text}", KeyEnv.TEST)
            refused = await seed_merchant(
                conn, Settings(Environment.PROD, database_url, SELF_URL), NOW
            )
            await conn.commit()
        assert found is not None and found.merchant.display_name == "Sandbox Shop"
        assert isinstance(refused, SeedInProd)
        assert await count_rows(database_url, "SELECT count(*) FROM merchant") == 1

    asyncio.run(body())


def test_psp_accounts_are_seeded_once_per_wiring_with_one_default_route(
    database_url: str,
) -> None:
    async def body() -> None:
        wired = wire(database_url, lambda: NOW)
        settings = Settings(ENV, database_url, SELF_URL)
        try:
            names: set[str] = set()
            for _ in range(2):
                async with await connect(database_url) as conn:
                    accounts = await seed_psp_accounts(conn, settings, NOW)
                    assert not isinstance(accounts, SeedInProd)
                    await conn.commit()
                assert await set_up_fake_psp(wired.control, settings, accounts) is None
                names = {account.name for account in accounts}
            assert len(names) == 12
            async with await connect(database_url) as conn:
                cur = await conn.execute(
                    "SELECT a.name FROM psp_route r"
                    " JOIN psp_account a ON a.id = r.psp_account_id"
                )
                assert await cur.fetchall() == [(DEFAULT_PSP,)]
            cur_count = await wired.control.get(
                f"{SELF_URL}/_fake/psp/{DEFAULT_PSP}/payments?reference=none"
            )
            assert cur_count.json() == {"payments": []}
        finally:
            await wired.http.aclose()
            await wired.control.aclose()

    asyncio.run(body())


def test_the_psp_seed_is_refused_in_prod(database_url: str) -> None:
    async def body() -> None:
        async with await connect(database_url) as conn:
            result = await seed_psp_accounts(
                conn, Settings(Environment.PROD, database_url, SELF_URL), NOW
            )
        assert isinstance(result, SeedInProd)

    asyncio.run(body())

"""The test setup's seed (docs/plan-test-setup.md §3), through our app and
the fake in-process: what it writes, that a second run changes nothing,
and that a seeded push account really receives the vendor's events."""

import asyncio
from datetime import UTC, datetime

from mor.audit import Actor, ActorKind
from mor.carriers.accounts import carrier_account
from mor.carriers.records import PriceExpiry
from mor.config import Environment, Settings
from mor.db.connect import connect
from mor.db.fetch import one_value
from mor.echo.flow import create_echo_job
from mor.echo.port import EchoProvider, EventMode
from mor.ids import CarrierCode
from mor.markets.store import load_market
from mor.merchants.records import KeyEnv, NewKey
from mor.merchants.store import authenticate
from mor.messaging.worker import Failed, drain_once
from mor.money.currency import Currency
from mor.seed import (
    DEFAULT_PSP,
    SeededEchoAccount,
    SeedInProd,
    base_url,
    seed_accounts,
    seed_bank_accounts,
    seed_carriers,
    seed_merchant,
    seed_psp_accounts,
    set_up_fake_bank,
    set_up_fake_psp,
    subscribe_webhooks,
)
from tests.helpers import ENV, Wired, count_rows, wire
from tests.quote.fixtures import live_market


NOW = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
# In-process, the host is ignored: the fakes and our app share one app.
SELF_URL = "http://mor"


async def _seed(url: str, wired: Wired) -> list[SeededEchoAccount]:
    settings = Settings(
        environment=ENV,
        database_url=url,
        self_url=SELF_URL,
        fake_psp_origin="http://psp.mor",
    )
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
        settings = Settings(
            environment=Environment.PROD,
            database_url=database_url,
            self_url=SELF_URL,
            fake_psp_origin="http://psp.mor",
        )
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


def test_each_routed_carrier_gets_one_account_at_the_fake(
    database_url: str,
) -> None:
    async def body() -> None:
        settings = Settings(
            environment=ENV,
            database_url=database_url,
            self_url=SELF_URL,
            fake_psp_origin="http://psp.mor",
        )
        async with await connect(database_url) as conn:
            await load_market(
                conn, live_market(), "live", Actor(ActorKind.SYSTEM, "t"), NOW
            )
            first = await seed_carriers(conn, settings)
            again = await seed_carriers(conn, settings)
            account = await carrier_account(conn, CarrierCode("carrier"))
            await conn.commit()
        assert not isinstance(first, SeedInProd)
        assert not isinstance(again, SeedInProd)
        assert [seeded.created for seeded in first] == [True]
        assert [seeded.created for seeded in again] == [False]
        assert account is not None
        assert account.base_url == "http://mor/_fake/carrier/keyed/sandbox-carrier"
        assert account.price_expiry == PriceExpiry.CARRIER_FIRST

    asyncio.run(body())


def test_the_sandbox_merchant_gets_one_key_shown_once(database_url: str) -> None:
    async def body() -> None:
        settings = Settings(
            environment=ENV,
            database_url=database_url,
            self_url=SELF_URL,
            fake_psp_origin="http://psp.mor",
        )
        async with await connect(database_url) as conn:
            first = await seed_merchant(conn, settings, NOW)
            again = await seed_merchant(conn, settings, NOW)
            assert isinstance(first, NewKey) and again is None
            found = await authenticate(conn, f"Bearer {first.text}", KeyEnv.TEST)
            refused = await seed_merchant(
                conn,
                Settings(
                    environment=Environment.PROD,
                    database_url=database_url,
                    self_url=SELF_URL,
                    fake_psp_origin="http://psp.mor",
                ),
                NOW,
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
        settings = Settings(
            environment=ENV,
            database_url=database_url,
            self_url=SELF_URL,
            fake_psp_origin="http://psp.mor",
        )
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


def test_a_bank_account_per_currency_is_seeded_once_with_every_psp_paying_in(
    database_url: str,
) -> None:
    async def body() -> None:
        wired = wire(database_url, lambda: NOW)
        settings = Settings(
            environment=ENV,
            database_url=database_url,
            self_url=SELF_URL,
            fake_psp_origin="http://psp.mor",
        )
        try:
            psp_count = 0
            bank_count = 0
            for _ in range(2):
                async with await connect(database_url) as conn:
                    psps = await seed_psp_accounts(conn, settings, NOW)
                    assert not isinstance(psps, SeedInProd)
                    banks = await seed_bank_accounts(conn, settings, psps, NOW)
                    assert not isinstance(banks, SeedInProd)
                    await conn.commit()
                assert await set_up_fake_psp(wired.control, settings, psps) is None
                assert await set_up_fake_bank(wired.control, banks, psps) is None
                psp_count = len(psps)
                bank_count = len(banks)
            assert bank_count == len(Currency)
            assert await count_rows(
                database_url, "SELECT count(*) FROM bank_account"
            ) == len(Currency)
            assert (
                await count_rows(
                    database_url, "SELECT count(*) FROM psp_payout_destination"
                )
                == len(Currency) * psp_count
            )
            assert (
                await count_rows(
                    database_url,
                    "SELECT count(*) FROM fake_vendor.bank_fake_destination",
                )
                == len(Currency) * psp_count
            )
            assert await count_rows(
                database_url, "SELECT count(*) FROM bank_cursor"
            ) == len(Currency)
        finally:
            await wired.http.aclose()
            await wired.control.aclose()

    asyncio.run(body())


def test_the_psp_seed_is_refused_in_prod(database_url: str) -> None:
    async def body() -> None:
        async with await connect(database_url) as conn:
            result = await seed_psp_accounts(
                conn,
                Settings(
                    environment=Environment.PROD,
                    database_url=database_url,
                    self_url=SELF_URL,
                    fake_psp_origin="http://psp.mor",
                ),
                NOW,
            )
        assert isinstance(result, SeedInProd)

    asyncio.run(body())

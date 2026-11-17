"""The test setup's page (docs/plan-test-setup.md §4), through our app and
the fake in-process: the page shows the seeded accounts, each button does
its work and comes back to the page, and a cross-site POST is refused."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx2

from mor.config import Settings
from mor.db.connect import connect
from mor.echo.port import EchoProvider, EventMode
from mor.messaging.worker import Failed, drain_once
from mor.seed import SeedInProd, base_url, seed_accounts, subscribe_webhooks
from tests.helpers import ENV, Wired, wire


NOW = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
SELF_URL = "http://mor"
SAME_ORIGIN = {"Origin": SELF_URL}


async def _with_seed(url: str, body: Callable[[Wired], Awaitable[None]]) -> None:
    wired = wire(url, lambda: NOW)
    async with wired.http, wired.control:
        settings = Settings(ENV, url, SELF_URL)
        async with await connect(url) as conn:
            seeded = await seed_accounts(conn, settings, NOW)
            assert not isinstance(seeded, SeedInProd)
            await conn.commit()
        assert await subscribe_webhooks(wired.control, settings, seeded) is None
        await body(wired)


async def _account_id(url: str, provider: EchoProvider, mode: EventMode) -> uuid.UUID:
    async with await connect(url) as conn:
        cur = await conn.execute(
            "SELECT id FROM echo_account WHERE base_url = %s",
            (base_url(SELF_URL, provider, mode),),
        )
        row = await cur.fetchone()
    assert row is not None and isinstance(row[0], uuid.UUID)
    return row[0]


async def _post(wired: Wired, path: str, form: dict[str, str]) -> httpx2.Response:
    return await wired.control.post(f"{SELF_URL}{path}", data=form, headers=SAME_ORIGIN)


async def _drain(url: str, wired: Wired) -> None:
    async with await connect(url, autocommit=True) as worker:
        for _ in range(10):
            for result in await drain_once(worker, wired.http, ENV, NOW):
                assert not isinstance(result, Failed), result


async def _job(url: str) -> tuple[uuid.UUID, str]:
    async with await connect(url) as conn:
        cur = await conn.execute("SELECT id, vendor_status::text FROM echo_job")
        rows = await cur.fetchall()
    assert len(rows) == 1
    return rows[0][0], rows[0][1]


def test_the_page_lists_every_seeded_account(database_url: str) -> None:
    async def body(wired: Wired) -> None:
        response = await wired.control.get(f"{SELF_URL}/_fake/")
        assert response.status_code == 200
        for provider in EchoProvider:
            for mode in EventMode:
                assert f"{provider.value}, {mode.value}" in response.text

    asyncio.run(_with_seed(database_url, body))


def test_a_job_created_and_released_on_the_page_reaches_its_status(
    database_url: str,
) -> None:
    async def body(wired: Wired) -> None:
        account_id = await _account_id(
            database_url, EchoProvider.FAKE_KEYED, EventMode.PUSH
        )
        created = await _post(
            wired, "/_fake/sandbox/jobs", {"account_id": str(account_id)}
        )
        assert created.status_code == 303
        await _drain(database_url, wired)
        job_id, _ = await _job(database_url)

        released = await _post(
            wired, "/_fake/sandbox/release", {"job_id": str(job_id), "push": "once"}
        )
        assert released.status_code == 303
        await _drain(database_url, wired)
        assert await _job(database_url) == (job_id, "confirmed")

        page = await wired.control.get(f"{SELF_URL}{released.headers['location']}")
        assert "vendor answered 200" in page.text
        assert "confirmed (vendor)" in page.text

    asyncio.run(_with_seed(database_url, body))


def test_an_event_recorded_by_hand_on_the_page_moves_the_status(
    database_url: str,
) -> None:
    async def body(wired: Wired) -> None:
        account_id = await _account_id(
            database_url, EchoProvider.FAKE_PLAIN, EventMode.NONE
        )
        await _post(wired, "/_fake/sandbox/jobs", {"account_id": str(account_id)})
        job_id, _ = await _job(database_url)
        recorded = await _post(
            wired, "/_fake/sandbox/manual", {"job_id": str(job_id), "kind": "archived"}
        )
        assert recorded.status_code == 303
        await _drain(database_url, wired)
        assert await _job(database_url) == (job_id, "archived")

    asyncio.run(_with_seed(database_url, body))


def test_a_report_cut_on_the_page_exists_at_the_vendor(database_url: str) -> None:
    async def body(wired: Wired) -> None:
        account_id = await _account_id(
            database_url, EchoProvider.FAKE_KEYED, EventMode.FILE
        )
        cut = await _post(wired, "/_fake/sandbox/cut", {"account_id": str(account_id)})
        assert cut.status_code == 303
        page = await wired.control.get(f"{SELF_URL}{cut.headers['location']}")
        assert "vendor answered 200" in page.text
        listed = await wired.control.get(
            f"{base_url(SELF_URL, EchoProvider.FAKE_KEYED, EventMode.FILE)}/reports"
        )
        assert len(listed.json()["reports"]) == 1

    asyncio.run(_with_seed(database_url, body))


def test_a_cross_site_post_is_refused_and_changes_nothing(database_url: str) -> None:
    async def body(wired: Wired) -> None:
        account_id = await _account_id(
            database_url, EchoProvider.FAKE_KEYED, EventMode.PUSH
        )
        form = {"account_id": str(account_id)}
        for headers in ({"Origin": "http://evil.example"}, {}):
            response = await wired.control.post(
                f"{SELF_URL}/_fake/sandbox/jobs", data=form, headers=headers
            )
            assert response.status_code == 403
        async with await connect(database_url) as conn:
            cur = await conn.execute("SELECT count(*) FROM echo_job")
            assert await cur.fetchone() == (0,)

    asyncio.run(_with_seed(database_url, body))


def test_a_malformed_form_is_answered_400(database_url: str) -> None:
    async def body(wired: Wired) -> None:
        response = await _post(wired, "/_fake/sandbox/jobs", {"account_id": "nope"})
        assert response.status_code == 400

    asyncio.run(_with_seed(database_url, body))

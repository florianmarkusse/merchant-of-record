"""Inbound events through our app, the fake vendor and the worker, all
in-process: push webhooks, the `none` mode, and events recorded by hand
(docs/plan-messaging.md §12, step 1)."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

import mor.fakes.echo
from mor.audit import Actor, ActorKind
from mor.config import Environment, Settings
from mor.db.connect import Connection, connect
from mor.echo.flow import create_echo_account, create_echo_job
from mor.echo.inbound import JobNotFound, record_manual_event, store_inbox
from mor.echo.port import EchoProvider, EventKind, EventMode
from mor.echo.reports import start_report_schedule
from mor.fakes.hang import hang_a_minute
from mor.messaging.worker import Deferred, Failed, RunResult, drain_once
from mor.web.main import Fakes, FakesInProd, make_app
from tests.helpers import ENV, NO_HTTP, Wired, wire


NOW = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
ACTOR = Actor(ActorKind.SYSTEM, "test")
PERSON = Actor(ActorKind.STAFF, "staff-1")
SECRET = "test-secret"


@dataclass(kw_only=True)
class Setup:
    url: str
    wired: Wired
    worker: Connection
    now: datetime  # the one clock of app and worker; tests move it


async def _with_setup(url: str, body: Callable[[Setup], Awaitable[None]]) -> None:
    setup_box: list[Setup] = []
    wired = wire(url, lambda: setup_box[0].now)
    async with wired.http, wired.control, await connect(url, autocommit=True) as worker:
        setup_box.append(Setup(url=url, wired=wired, worker=worker, now=NOW))
        await body(setup_box[0])


async def _account(
    setup: Setup,
    mode: EventMode,
    name: str,
    poll_interval: timedelta = timedelta(minutes=5),
) -> tuple[uuid.UUID, str]:
    base_url = f"http://fake/_fake/echo/keyed/{name}"
    async with await connect(setup.url) as conn:
        account_id = await create_echo_account(
            conn,
            EchoProvider.FAKE_KEYED,
            base_url,
            timedelta(seconds=5),
            event_mode=mode,
            webhook_secret=SECRET,
            poll_interval=poll_interval,
        )
        await conn.commit()
    if mode in (EventMode.PUSH, EventMode.PUSH_THIN):
        response = await setup.wired.control.post(
            f"{base_url}/webhooks",
            json={
                "url": f"http://mor/webhooks/echo/{account_id}",
                "secret": SECRET,
                "payload": "full" if mode == EventMode.PUSH else "thin",
            },
        )
        assert response.status_code == 204
    return account_id, base_url


async def _drain(setup: Setup) -> list[RunResult]:
    every: list[RunResult] = []
    for _ in range(20):
        results = await drain_once(setup.worker, setup.wired.http, ENV, setup.now)
        if not results:
            break
        every.extend(results)
    return every


async def _echoed_job(setup: Setup, account_id: uuid.UUID) -> tuple[uuid.UUID, str]:
    async with await connect(setup.url) as conn:
        job_id = await create_echo_job(conn, account_id, "hello", ACTOR, NOW)
        await conn.commit()
    for result in await _drain(setup):
        assert not isinstance(result, Failed), result
    async with await connect(setup.url) as conn:
        cur = await conn.execute(
            "SELECT vendor_echo_id FROM echo_job WHERE id = %s", (job_id,)
        )
        row = await cur.fetchone()
    assert row is not None and isinstance(row[0], str)
    return job_id, row[0]


async def _release(
    setup: Setup,
    base_url: str,
    echo_id: str,
    kind: str | None = None,
    push: str = "once",
) -> str:
    body: dict[str, str] = {"push": push}
    if kind is not None:
        body["kind"] = kind
    response = await setup.wired.control.post(
        f"{base_url}/echoes/{echo_id}/events/release", json=body
    )
    assert response.status_code == 200, response.text
    push_state = response.json()["push"]
    assert isinstance(push_state, str)
    return push_state


async def _rows(setup: Setup, query: bytes, job_id: uuid.UUID) -> list[str]:
    async with await connect(setup.url) as conn:
        cur = await conn.execute(query, (job_id,))
        rows = await cur.fetchall()
    values: list[str] = []
    for (value,) in rows:
        values.append(str(value))
    return values


async def _status(setup: Setup, job_id: uuid.UUID) -> str:
    query = b"SELECT vendor_status::text FROM echo_job WHERE id = %s"
    return (await _rows(setup, query, job_id))[0]


async def _milestone_facts(setup: Setup, job_id: uuid.UUID) -> list[str]:
    query = (
        b"SELECT message_type::text FROM outbox WHERE aggregate_id = %s"
        b" AND message_type IN ('echo_job_confirmed', 'echo_job_archived')"
        b" ORDER BY seq"
    )
    return await _rows(setup, query, job_id)


async def _inbox_count(setup: Setup) -> int:
    async with await connect(setup.url) as conn:
        cur = await conn.execute("SELECT count(*) FROM echo_inbox")
        row = await cur.fetchone()
    assert row is not None and isinstance(row[0], int)
    return row[0]


def test_pushed_events_become_milestones_once(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        account_id, base_url = await _account(setup, EventMode.PUSH, "push-1")
        job_id, echo_id = await _echoed_job(setup, account_id)

        assert await _release(setup, base_url, echo_id) == "delivered"
        await _drain(setup)
        assert await _status(setup, job_id) == "confirmed"
        assert await _release(setup, base_url, echo_id) == "delivered"
        await _drain(setup)
        assert await _status(setup, job_id) == "archived"
        assert await _milestone_facts(setup, job_id) == [
            "echo_job_confirmed",
            "echo_job_archived",
        ]

    asyncio.run(_with_setup(database_url, body))


def test_a_webhook_sent_twice_is_stored_and_applied_once(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        account_id, base_url = await _account(setup, EventMode.PUSH, "push-2")
        job_id, echo_id = await _echoed_job(setup, account_id)

        await _release(setup, base_url, echo_id, push="twice")
        await _drain(setup)
        assert await _inbox_count(setup) == 1
        events = b"SELECT vendor_event_id FROM echo_job_event WHERE echo_job_id = %s"
        assert len(await _rows(setup, events, job_id)) == 1
        assert await _milestone_facts(setup, job_id) == ["echo_job_confirmed"]

    asyncio.run(_with_setup(database_url, body))


def test_a_badly_signed_webhook_stores_nothing_until_redelivered(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        account_id, base_url = await _account(setup, EventMode.PUSH, "push-3")
        job_id, echo_id = await _echoed_job(setup, account_id)

        assert await _release(setup, base_url, echo_id, push="bad_signature") == (
            "failed"
        )
        assert await _inbox_count(setup) == 0
        response = await setup.wired.control.post(f"{base_url}/events/redeliver")
        assert response.json() == {"redelivered": 1}
        await _drain(setup)
        assert await _status(setup, job_id) == "confirmed"

    asyncio.run(_with_setup(database_url, body))


def test_events_out_of_order_never_move_the_status_back(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        account_id, base_url = await _account(setup, EventMode.PUSH, "push-4")
        job_id, echo_id = await _echoed_job(setup, account_id)

        await _release(setup, base_url, echo_id, kind="archived")
        await _drain(setup)
        await _release(setup, base_url, echo_id, kind="confirmed")
        await _drain(setup)
        assert await _status(setup, job_id) == "archived"
        assert await _milestone_facts(setup, job_id) == ["echo_job_archived"]

    asyncio.run(_with_setup(database_url, body))


def test_a_none_account_moves_only_by_hand(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        account_id, base_url = await _account(setup, EventMode.NONE, "none-1")
        job_id, echo_id = await _echoed_job(setup, account_id)

        # The vendor's event goes nowhere: nobody listens and nobody polls.
        assert await _release(setup, base_url, echo_id) == "not_subscribed"
        await _drain(setup)
        assert await _status(setup, job_id) == "none"

        async with await connect(setup.url) as conn:
            recorded = await record_manual_event(
                conn, job_id, EventKind.CONFIRMED, NOW, PERSON, NOW
            )
            await conn.commit()
        assert isinstance(recorded, uuid.UUID)
        await _drain(setup)
        assert await _status(setup, job_id) == "confirmed"
        actor = b"SELECT actor_id FROM outbox WHERE id = %s"
        assert await _rows(setup, actor, recorded) == ["staff-1"]

    asyncio.run(_with_setup(database_url, body))


def test_a_manual_event_beside_a_vendor_one_changes_nothing_twice(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        account_id, base_url = await _account(setup, EventMode.PUSH, "push-5")
        job_id, echo_id = await _echoed_job(setup, account_id)

        await _release(setup, base_url, echo_id)
        await _drain(setup)
        async with await connect(setup.url) as conn:
            await record_manual_event(
                conn, job_id, EventKind.CONFIRMED, NOW, PERSON, NOW
            )
            missing = await record_manual_event(
                conn, uuid.uuid7(), EventKind.CONFIRMED, NOW, PERSON, NOW
            )
            await conn.commit()
        assert isinstance(missing, JobNotFound)
        await _drain(setup)
        assert await _status(setup, job_id) == "confirmed"
        assert await _milestone_facts(setup, job_id) == ["echo_job_confirmed"]

    asyncio.run(_with_setup(database_url, body))


def test_an_undecodable_body_stays_and_its_delivery_fails_visibly(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        account_id, _ = await _account(setup, EventMode.PUSH, "push-6")
        async with await connect(setup.url) as conn:
            await store_inbox(conn, account_id, EventMode.PUSH, b"not json", NOW)
            await conn.commit()
        results = await drain_once(setup.worker, setup.wired.http, ENV, NOW)
        assert len(results) == 1 and isinstance(results[0], Failed)
        assert "UndecodableInbox" in results[0].error
        assert await _inbox_count(setup) == 1

    asyncio.run(_with_setup(database_url, body))


def test_webhooks_for_unknown_or_non_push_accounts_are_refused(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        none_id, _ = await _account(setup, EventMode.NONE, "none-2")
        for account in [str(none_id), str(uuid.uuid7()), "not-a-uuid"]:
            response = await setup.wired.control.post(
                f"http://mor/webhooks/echo/{account}", content=b"{}"
            )
            assert response.status_code == 404
        push_id, _ = await _account(setup, EventMode.PUSH, "push-7")
        unsigned = await setup.wired.control.post(
            f"http://mor/webhooks/echo/{push_id}", content=b'{"events": []}'
        )
        assert unsigned.status_code == 401
        assert await _inbox_count(setup) == 0

    asyncio.run(_with_setup(database_url, body))


def test_the_fakes_are_never_mounted_in_prod() -> None:
    fakes = mor.fakes.echo.make_app("postgresql:///unused", NO_HTTP, hang_a_minute)
    with pytest.raises(FakesInProd):
        make_app(
            Settings(Environment.PROD, "postgresql:///unused", "http://mor"),
            Fakes(fakes, NO_HTTP),
            lambda: NOW,
        )


async def _scheduled_polls(setup: Setup, job_id: uuid.UUID) -> int:
    async with await connect(setup.url) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM outbox_delivery WHERE aggregate_id = %s"
            " AND consumer = 'echo_poll' AND handled_at IS NULL",
            (job_id,),
        )
        row = await cur.fetchone()
    assert row is not None and isinstance(row[0], int)
    return row[0]


def test_a_poll_account_polls_until_the_status_is_final(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        interval = timedelta(minutes=5)
        account_id, base_url = await _account(setup, EventMode.POLL, "poll-1", interval)
        job_id, echo_id = await _echoed_job(setup, account_id)
        # The first poll ran at once and found nothing; the next is scheduled.
        assert await _status(setup, job_id) == "none"
        assert await _scheduled_polls(setup, job_id) == 1

        assert await _release(setup, base_url, echo_id) == "not_subscribed"
        await _drain(setup)  # not due yet: time only starts the poll
        assert await _status(setup, job_id) == "none"
        setup.now += interval
        await _drain(setup)
        assert await _status(setup, job_id) == "confirmed"

        await _release(setup, base_url, echo_id)
        setup.now += interval
        await _drain(setup)
        assert await _status(setup, job_id) == "archived"
        # The poll that stored the archived page scheduled one more, which
        # finds the job final and stops.
        setup.now += interval
        await _drain(setup)
        assert await _scheduled_polls(setup, job_id) == 0
        assert await _milestone_facts(setup, job_id) == [
            "echo_job_confirmed",
            "echo_job_archived",
        ]

    asyncio.run(_with_setup(database_url, body))


def test_the_same_page_polled_again_is_stored_once(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        interval = timedelta(minutes=5)
        account_id, base_url = await _account(setup, EventMode.POLL, "poll-2", interval)
        job_id, echo_id = await _echoed_job(setup, account_id)
        await _release(setup, base_url, echo_id)
        for _ in range(3):
            setup.now += interval
            await _drain(setup)
        # An empty page, then the page with `confirmed`, unchanged twice.
        assert await _inbox_count(setup) == 2
        assert await _status(setup, job_id) == "confirmed"

    asyncio.run(_with_setup(database_url, body))


def test_a_failed_poll_is_tried_again_later(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        interval = timedelta(minutes=5)
        account_id, base_url = await _account(setup, EventMode.POLL, "poll-3", interval)
        job_id, echo_id = await _echoed_job(setup, account_id)
        await _release(setup, base_url, echo_id)
        response = await setup.wired.control.post(
            "http://fake/_fake/echo/faults",
            json={"flavour": "keyed", "fault": "fail_500", "operation": "events"},
        )
        assert response.status_code == 204
        setup.now += interval
        results = await drain_once(setup.worker, setup.wired.http, ENV, setup.now)
        assert len(results) == 1 and isinstance(results[0], Deferred)
        setup.now += timedelta(seconds=3)
        await _drain(setup)
        assert await _status(setup, job_id) == "confirmed"

    asyncio.run(_with_setup(database_url, body))


def test_a_thin_webhook_leads_to_one_fetch_and_no_schedule(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        account_id, base_url = await _account(setup, EventMode.PUSH_THIN, "thin-1")
        job_id, echo_id = await _echoed_job(setup, account_id)

        assert await _release(setup, base_url, echo_id) == "delivered"
        await _drain(setup)
        assert await _status(setup, job_id) == "confirmed"
        assert await _scheduled_polls(setup, job_id) == 0
        wanted = (
            b"SELECT message_type::text FROM outbox WHERE aggregate_id = %s"
            b" AND message_type = 'echo_events_wanted'"
        )
        assert len(await _rows(setup, wanted, job_id)) == 1

    asyncio.run(_with_setup(database_url, body))


def test_each_thin_webhook_of_one_echo_leads_to_its_own_fetch(
    database_url: str,
) -> None:
    """Once lost every event after the first: two thin notices for one
    echo were the same bytes, and the inbox took the second for a resend."""

    async def body(setup: Setup) -> None:
        account_id, base_url = await _account(setup, EventMode.PUSH_THIN, "thin-2")
        job_id, echo_id = await _echoed_job(setup, account_id)

        assert await _release(setup, base_url, echo_id, kind="confirmed") == "delivered"
        await _drain(setup)
        assert await _status(setup, job_id) == "confirmed"
        assert await _release(setup, base_url, echo_id, kind="archived") == "delivered"
        await _drain(setup)
        assert await _status(setup, job_id) == "archived"

    asyncio.run(_with_setup(database_url, body))


def test_a_backlog_of_reports_is_read_in_short_looks(database_url: str) -> None:
    """Once failed as a bug: one look read every waiting report, and 25 of
    them needed more steps than the worker allows one delivery."""

    async def body(setup: Setup) -> None:
        account_id, base_url = await _file_account(setup, "file-2")
        job_id, echo_id = await _echoed_job(setup, account_id)
        await _release(setup, base_url, echo_id)
        last = ""
        for _ in range(30):
            response = await setup.wired.control.post(f"{base_url}/reports/cut")
            last = response.json()["name"]

        setup.now += timedelta(minutes=5)
        for _ in range(40):
            results = await drain_once(setup.worker, setup.wired.http, ENV, setup.now)
            for result in results:
                assert not isinstance(result, Failed), result
        async with await connect(setup.url) as conn:
            cur = await conn.execute(
                "SELECT last_report FROM echo_report_cursor WHERE account_id = %s",
                (account_id,),
            )
            assert await cur.fetchone() == (last,)
        assert await _status(setup, job_id) == "confirmed"

    asyncio.run(_with_setup(database_url, body))


async def _file_account(setup: Setup, name: str) -> tuple[uuid.UUID, str]:
    account_id, base_url = await _account(setup, EventMode.FILE, name)
    async with await connect(setup.url) as conn:
        await start_report_schedule(conn, account_id, ACTOR, setup.now)
        await conn.commit()
    return account_id, base_url


async def _cut(setup: Setup, base_url: str) -> int:
    response = await setup.wired.control.post(f"{base_url}/reports/cut")
    assert response.status_code == 200
    events = response.json()["events"]
    assert isinstance(events, int)
    return events


def test_a_file_account_reads_each_report_once_in_order(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        account_id, base_url = await _file_account(setup, "file-1")
        job_id, echo_id = await _echoed_job(setup, account_id)
        assert await _status(setup, job_id) == "none"

        await _release(setup, base_url, echo_id)
        assert await _cut(setup, base_url) == 1
        await _release(setup, base_url, echo_id)
        assert await _cut(setup, base_url) == 1
        await _drain(setup)  # the next look is not due yet
        assert await _status(setup, job_id) == "none"

        setup.now += timedelta(minutes=5)
        await _drain(setup)
        assert await _status(setup, job_id) == "archived"
        assert await _milestone_facts(setup, job_id) == [
            "echo_job_confirmed",
            "echo_job_archived",
        ]
        assert await _inbox_count(setup) == 2
        # Nothing new: the next look stores nothing and schedules again.
        setup.now += timedelta(minutes=5)
        await _drain(setup)
        assert await _inbox_count(setup) == 2
        async with await connect(setup.url) as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM outbox_delivery WHERE handled_at IS NULL"
                " AND consumer = 'echo_report_fetch'"
            )
            row = await cur.fetchone()
        assert row == (1,)

    asyncio.run(_with_setup(database_url, body))

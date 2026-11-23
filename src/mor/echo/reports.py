"""Report files for `file` accounts (docs/plan-messaging.md §12).
Transitional with the echo flow.

    start_report_schedule ─▶ echo_report_due (scheduled, per account)
    echo_report_fetch (effect), one look, at most one report:
        list the reports after our cursor ─▶ the first becomes `pending`,
            `Again`
        fetch `pending` ─▶ stored raw in the inbox (the same path as a
            webhook), the cursor moves on, the next look is due now
        nothing new ─▶ the next look is due after the poll interval

One report per look keeps a look at two steps however many reports wait
(a backlog of 25 once ran past the worker's step limit), and a backlog is
read as looks that follow each other at once.

A list or a fetch only reads, so one repeated after a crash needs no guard:
the inbox key absorbs a report stored twice.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

import httpx2

from mor.audit import Actor, ActorKind
from mor.config import Environment
from mor.db.connect import Connection
from mor.echo import registry
from mor.echo.inbound import inbound_account, store_inbox
from mor.echo.port import (
    EchoAccount,
    EventMode,
    FetchResult,
    ListResult,
    Page,
    ReportList,
)
from mor.messaging.facts import EchoReportDueV1, Fact
from mor.messaging.outcome import Again, Applied, Done, NoCall, RetryLater, Stale
from mor.messaging.routing import WrongFact
from mor.messaging.store import append_fact
from mor.vendor_failures import OutcomeUnknown, Unavailable


SCHEDULER = Actor(ActorKind.SYSTEM, "echo_report_fetch")


@dataclass(frozen=True)
class ListReportsCall:
    account: EchoAccount
    account_id: uuid.UUID
    after: str


@dataclass(frozen=True)
class FetchReportCall:
    account: EchoAccount
    account_id: uuid.UUID
    name: str


type ReportCall = ListReportsCall | FetchReportCall


@dataclass(frozen=True)
class ListReportsAnswer:
    """The list call and what the vendor said to it, from `call` to `complete`."""

    call: ListReportsCall
    result: ListResult


@dataclass(frozen=True)
class FetchReportAnswer:
    """The fetch and what the vendor said to it, from `call` to `complete`."""

    call: FetchReportCall
    result: FetchResult


type ReportAnswer = ListReportsAnswer | FetchReportAnswer


@dataclass(frozen=True)
class _Cursor:
    last_report: str | None
    pending_report: str | None


async def start_report_schedule(
    conn: Connection, account_id: uuid.UUID, actor: Actor, now: datetime
) -> None:
    """For a `file` account: its first look for reports, now. Each look
    schedules the next. Never commits."""
    await append_fact(conn, EchoReportDueV1(account_id=account_id), actor, now)


def _account_id(fact: Fact) -> uuid.UUID:
    match fact:
        case EchoReportDueV1(account_id=account_id):
            return account_id
        case _:
            raise WrongFact(f"echo_report_fetch got {fact!r}")


async def _lock_cursor(conn: Connection, account_id: uuid.UUID) -> _Cursor:
    await conn.execute(
        "INSERT INTO echo_report_cursor (account_id) VALUES (%s)"
        " ON CONFLICT (account_id) DO NOTHING",
        (account_id,),
    )
    cur = await conn.execute(
        "SELECT last_report, pending_report FROM echo_report_cursor"
        " WHERE account_id = %s FOR UPDATE",
        (account_id,),
    )
    row = await cur.fetchone()
    assert row is not None
    return _Cursor(row[0], row[1])


async def prepare_reports(
    conn: Connection, fact: Fact, now: datetime
) -> NoCall | ReportCall:
    account_id = _account_id(fact)
    inbound = await inbound_account(conn, account_id)
    assert inbound is not None, f"no echo account {account_id}"
    if inbound.event_mode != EventMode.FILE:
        return NoCall(Stale())  # the account changed mode: stop looking
    cursor = await _lock_cursor(conn, account_id)
    if cursor.pending_report is not None:
        return FetchReportCall(inbound.account, account_id, cursor.pending_report)
    return ListReportsCall(inbound.account, account_id, cursor.last_report or "")


async def call_reports(
    http: httpx2.AsyncClient, environment: Environment, report_call: ReportCall
) -> ReportAnswer:
    match report_call:
        case ListReportsCall(account=account, after=after):
            listed = await registry.list_reports(http, environment, account, after)
            return ListReportsAnswer(report_call, listed)
        case FetchReportCall(account=account, name=name):
            fetched = await registry.fetch_report(http, environment, account, name)
            return FetchReportAnswer(report_call, fetched)


async def complete_reports(
    conn: Connection, fact: Fact, answer: ReportAnswer, now: datetime
) -> Done | RetryLater | Again:
    account_id = _account_id(fact)
    await _lock_cursor(conn, account_id)
    match answer:
        case ListReportsAnswer():
            return await _listed(conn, account_id, answer.result, now)
        case FetchReportAnswer():
            return await _fetched(conn, account_id, answer.call, answer.result, now)


async def _listed(
    conn: Connection, account_id: uuid.UUID, result: ListResult, now: datetime
) -> Done | RetryLater | Again:
    match result:
        case ReportList(names=names):
            if names:
                await conn.execute(
                    "UPDATE echo_report_cursor SET pending_report = %s"
                    " WHERE account_id = %s",
                    (names[0], account_id),
                )
                return Again()
            inbound = await inbound_account(conn, account_id)
            assert inbound is not None
            await append_fact(
                conn,
                EchoReportDueV1(account_id=account_id),
                SCHEDULER,
                now,
                deliver_at=now + inbound.poll_interval,
            )
            return Done(Applied())
        case Unavailable(detail=detail) | OutcomeUnknown(detail=detail):
            # Reads only: trying again later is always safe.
            return RetryLater(f"reports: {detail}")


async def _fetched(
    conn: Connection,
    account_id: uuid.UUID,
    fetch: FetchReportCall,
    result: FetchResult,
    now: datetime,
) -> Done | RetryLater:
    match result:
        case Page(body=body):
            await store_inbox(conn, account_id, EventMode.FILE, body, now)
            await conn.execute(
                "UPDATE echo_report_cursor"
                " SET last_report = %s, pending_report = NULL WHERE account_id = %s",
                (fetch.name, account_id),
            )
            # More may wait: look again at once, in a delivery of its own.
            await append_fact(
                conn, EchoReportDueV1(account_id=account_id), SCHEDULER, now
            )
            return Done(Applied())
        case Unavailable(detail=detail) | OutcomeUnknown(detail=detail):
            # Reads only: trying again later is always safe.
            return RetryLater(f"reports: {detail}")

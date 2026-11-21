"""Reading what a PSP settled (docs/plan-payments.md §7.1), per account, by
report file or by API, as the echo's report files are read
(docs/plan-messaging.md §12):

    start_settlement_reading ─▶ psp_settlement_due (scheduled, per account)
    psp_settlement_fetch (effect), one look, at most one report or page:
        nothing pending: list after `last_read` ─▶ the first is `pending`,
            `Again`
        `pending`: fetch it (an API batch a page at a time) ─▶ stored raw in
            the inbox, the cursor moves on, the next look is due now
        nothing new ─▶ the next look is due after the account's interval

A list or a fetch only reads, so one repeated after a crash needs no guard:
the inbox absorbs bytes stored twice, and every batch and line is stored
once by the PSP's own id.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

import httpx2

from mor.audit import Actor, ActorKind
from mor.config import Environment
from mor.db.connect import Connection
from mor.messaging.facts import Fact, PspSettlementDueV1
from mor.messaging.outcome import Again, Applied, Done, NoCall, RetryLater
from mor.messaging.store import append_fact
from mor.payments.inbound import store_inbox
from mor.psp import registry
from mor.psp.accounts import read_psp_account
from mor.psp.port import (
    InboundMode,
    PspAccount,
    SettlementList,
    SettlementListResult,
    SettlementMode,
    SettlementPage,
    SettlementPageResult,
)
from mor.vendor_failures import OutcomeUnknown, Unavailable, UndecodableBody


SCHEDULER = Actor(ActorKind.SYSTEM, "psp_settlement_fetch")


class WrongFact(Exception):
    """A consumer got a fact routing never sends it: a bug in routing."""


@dataclass(frozen=True)
class ListCall:
    account: PspAccount
    after: str


@dataclass(frozen=True)
class FetchCall:
    account: PspAccount
    name: str
    page: int


type SettlementCall = ListCall | FetchCall


@dataclass(frozen=True)
class ListAnswer:
    """A list call and what the PSP said to it, from `call` to `complete`."""

    call: ListCall
    result: SettlementListResult | UndecodableBody


@dataclass(frozen=True)
class FetchAnswer:
    """A fetch call and what the PSP said to it, from `call` to `complete`."""

    call: FetchCall
    result: SettlementPageResult | UndecodableBody


type SettlementAnswer = ListAnswer | FetchAnswer


@dataclass(frozen=True)
class _Cursor:
    last_read: str | None
    pending: str | None
    pending_page: int | None


async def start_settlement_reading(
    conn: Connection, account_id: uuid.UUID, actor: Actor, now: datetime
) -> bool:
    """An account's first look, now; each look schedules the next. The
    cursor's insert is the guard: an account already reading is left as
    it is, so its looks never run twice over. Never commits."""
    cur = await conn.execute(
        """
        INSERT INTO psp_settlement_cursor (account_id, started_at) VALUES (%s, %s)
        ON CONFLICT (account_id) DO NOTHING RETURNING account_id
        """,
        (account_id, now),
    )
    if await cur.fetchone() is None:
        return False
    await append_fact(conn, PspSettlementDueV1(psp_account_id=account_id), actor, now)
    return True


def _account_id(fact: Fact) -> uuid.UUID:
    match fact:
        case PspSettlementDueV1(psp_account_id=account_id):
            return account_id
        case _:
            raise WrongFact(f"psp_settlement_fetch got {fact!r}")


async def _lock_cursor(conn: Connection, account_id: uuid.UUID) -> _Cursor:
    cur = await conn.execute(
        "SELECT last_read, pending, pending_page FROM psp_settlement_cursor"
        " WHERE account_id = %s FOR UPDATE",
        (account_id,),
    )
    row = await cur.fetchone()
    assert row is not None, f"no settlement cursor for {account_id}"
    return _Cursor(row[0], row[1], row[2])


async def prepare_fetch(
    conn: Connection, fact: Fact, now: datetime
) -> NoCall | SettlementCall:
    account_id = _account_id(fact)
    account = await read_psp_account(conn, account_id)
    assert account is not None, f"no psp account {account_id}"
    cursor = await _lock_cursor(conn, account_id)
    if cursor.pending is not None:
        return FetchCall(account, cursor.pending, cursor.pending_page or 1)
    return ListCall(account, cursor.last_read or "")


async def call_fetch(
    http: httpx2.AsyncClient, environment: Environment, settlement_call: SettlementCall
) -> SettlementAnswer:
    match settlement_call:
        case ListCall(account=account, after=after):
            listed = await registry.list_settlements(http, environment, account, after)
            return ListAnswer(settlement_call, listed)
        case FetchCall(account=account, name=name, page=page):
            fetched = await registry.fetch_settlement(
                http, environment, account, name, page
            )
            return FetchAnswer(settlement_call, fetched)


def _inbound_mode(account: PspAccount) -> InboundMode:
    match account.settlement_mode:
        case SettlementMode.FILE:
            return InboundMode.SETTLEMENT_FILE
        case SettlementMode.API:
            return InboundMode.SETTLEMENT_PAGE


async def complete_fetch(
    conn: Connection, fact: Fact, answer: SettlementAnswer, now: datetime
) -> Done | RetryLater | Again:
    account_id = _account_id(fact)
    await _lock_cursor(conn, account_id)
    match answer:
        case ListAnswer():
            return await _listed(conn, account_id, answer.call, answer.result, now)
        case FetchAnswer():
            return await _fetched(conn, account_id, answer.call, answer.result, now)


def _retry(failure: Unavailable | OutcomeUnknown | UndecodableBody) -> RetryLater:
    match failure:
        case Unavailable(detail=detail) | OutcomeUnknown(detail=detail):
            # Reads only: trying again later is always safe.
            return RetryLater(f"settlement: {detail}")
        case UndecodableBody(detail=detail):
            # An answer the PSP does not document: retried, and visible as
            # a failing delivery until a person or a fixed client reads it.
            return RetryLater(f"settlement answer: {detail}")


async def _listed(
    conn: Connection,
    account_id: uuid.UUID,
    list_call: ListCall,
    result: SettlementListResult | UndecodableBody,
    now: datetime,
) -> Done | RetryLater | Again:
    match result:
        case SettlementList(names=names):
            if names:
                await conn.execute(
                    "UPDATE psp_settlement_cursor SET pending = %s, pending_page = 1"
                    " WHERE account_id = %s",
                    (names[0], account_id),
                )
                return Again()
            await append_fact(
                conn,
                PspSettlementDueV1(psp_account_id=account_id),
                SCHEDULER,
                now,
                deliver_at=now + list_call.account.settlement_interval,
            )
            return Done(Applied())
        case Unavailable() | OutcomeUnknown() | UndecodableBody():
            return _retry(result)


async def _fetched(
    conn: Connection,
    account_id: uuid.UUID,
    fetch_call: FetchCall,
    result: SettlementPageResult | UndecodableBody,
    now: datetime,
) -> Done | RetryLater:
    match result:
        case SettlementPage(body=body, next_page=next_page):
            await store_inbox(
                conn, account_id, _inbound_mode(fetch_call.account), body, now
            )
            if next_page is not None:
                await conn.execute(
                    "UPDATE psp_settlement_cursor SET pending_page = %s"
                    " WHERE account_id = %s",
                    (next_page, account_id),
                )
            else:
                await conn.execute(
                    """
                    UPDATE psp_settlement_cursor
                    SET last_read = %s, pending = NULL, pending_page = NULL
                    WHERE account_id = %s
                    """,
                    (fetch_call.name, account_id),
                )
            # More may wait: look again at once, in a delivery of its own.
            await append_fact(
                conn, PspSettlementDueV1(psp_account_id=account_id), SCHEDULER, now
            )
            return Done(Applied())
        case Unavailable() | OutcomeUnknown() | UndecodableBody():
            return _retry(result)

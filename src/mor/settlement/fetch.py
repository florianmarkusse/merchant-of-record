"""Reading what a PSP settled, per account, as the PSP sends it
(docs/plan-payments.md §7 and §4.5):

    start_settlement_reading ─▶ psp_settlement_due (scheduled, per account)
    psp_settlement_fetch (effect), one look:
        nothing pending: list after `last_read` ─▶ the first is `pending`,
            `Again`
        file: fetch the report ─▶ stored raw in the inbox, the cursor moves
            on, the next look is due now
        api, phase `payout`: fetch the payout object ─▶ stored raw; once the
            PSP lists all its lines (`completed`), phase `lines`, the next
            look due now; until then, after the account's interval
        api, phase `lines`: fetch a page after `pending_after` ─▶ stored raw
            with the payout it was asked for; the cursor moves to its last
            line, or on to the next payout after the last page
        nothing new ─▶ the next look is due after the account's interval

A list or a fetch only reads, so one repeated after a crash needs no guard:
the inbox absorbs bytes stored twice, and every payout and line is stored
once by the PSP's own id. Decoding is the inbox's, in any order: nothing
here waits for a page to be decoded.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

import httpx2
from psycopg.rows import class_row

from mor.audit import Actor, ActorKind
from mor.config import Environment
from mor.db.connect import Connection
from mor.db.fetch import one_row
from mor.messaging.facts import PspSettlementDueV1
from mor.messaging.outcome import Again, Applied, Done, NoCall, RetryLater
from mor.messaging.store import append_fact
from mor.payments.inbound import store_inbox
from mor.psp import registry
from mor.psp.accounts import read_psp_account
from mor.psp.port import (
    InboundMode,
    LinesPage,
    LinesResult,
    PayoutObject,
    PayoutResult,
    PspAccount,
    SettlementFile,
    SettlementFileResult,
    SettlementList,
    SettlementListResult,
    SettlementMode,
)
from mor.settlement.records import ReadPhase
from mor.vendor_failures import OutcomeUnknown, Unavailable, UndecodableBody


SCHEDULER = Actor(ActorKind.SYSTEM, "psp_settlement_fetch")


@dataclass(frozen=True)
class ListCall:
    account: PspAccount
    after: str


@dataclass(frozen=True)
class FileCall:
    account: PspAccount
    name: str


@dataclass(frozen=True)
class PayoutCall:
    account: PspAccount
    payout_id: str


@dataclass(frozen=True)
class LinesCall:
    account: PspAccount
    payout_id: str
    after: str | None


type SettlementCall = ListCall | FileCall | PayoutCall | LinesCall


@dataclass(frozen=True)
class ListAnswer:
    """A read and what the PSP said to it, from `read_settlement` to
    `complete_fetch`: the answer keeps its own read, typed, which the one
    `call` that `run_steps` also hands `complete_fetch` cannot be."""

    call: ListCall
    result: SettlementListResult | UndecodableBody


@dataclass(frozen=True)
class FileAnswer:
    call: FileCall
    result: SettlementFileResult | UndecodableBody


@dataclass(frozen=True)
class PayoutAnswer:
    call: PayoutCall
    result: PayoutResult | UndecodableBody


@dataclass(frozen=True)
class LinesAnswer:
    call: LinesCall
    result: LinesResult | UndecodableBody


type SettlementAnswer = ListAnswer | FileAnswer | PayoutAnswer | LinesAnswer


@dataclass(frozen=True, kw_only=True)
class _Cursor:
    last_read: str | None
    pending: str | None
    pending_phase: ReadPhase | None
    pending_after: str | None


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


async def _lock_cursor(conn: Connection, account_id: uuid.UUID) -> _Cursor:
    async with conn.cursor(row_factory=class_row(_Cursor)) as cur:
        await cur.execute(
            """
            SELECT last_read, pending, pending_phase, pending_after
            FROM psp_settlement_cursor WHERE account_id = %s FOR UPDATE
            """,
            (account_id,),
        )
        return await one_row(cur, f"settlement cursor for {account_id}")


# --- the consumer psp_settlement_fetch: prepare_fetch, read_settlement,
# complete_fetch, run by mor.messaging.delivery.run_steps ----------------------


async def prepare_fetch(
    conn: Connection, account_id: uuid.UUID, now: datetime
) -> NoCall | SettlementCall:
    account = await read_psp_account(conn, account_id)
    assert account is not None, f"no psp account {account_id}"
    cursor = await _lock_cursor(conn, account_id)
    if cursor.pending is None:
        return ListCall(account, cursor.last_read or "")
    match account.settlement_mode:
        case SettlementMode.FILE:
            return FileCall(account, cursor.pending)
        case SettlementMode.API:
            match cursor.pending_phase:
                # None never comes with a pending payout: `_listed` sets the
                # phase with it. Read as its start, the payout object.
                case ReadPhase.PAYOUT | None:
                    return PayoutCall(account, cursor.pending)
                case ReadPhase.LINES:
                    return LinesCall(account, cursor.pending, cursor.pending_after)


async def read_settlement(
    http: httpx2.AsyncClient, environment: Environment, settlement_call: SettlementCall
) -> SettlementAnswer:
    match settlement_call:
        case ListCall(account=account, after=after):
            listed = await registry.list_settlements(http, environment, account, after)
            return ListAnswer(settlement_call, listed)
        case FileCall(account=account, name=name):
            report = await registry.fetch_settlement_file(
                http, environment, account, name
            )
            return FileAnswer(settlement_call, report)
        case PayoutCall(account=account, payout_id=payout_id):
            payout = await registry.fetch_payout(http, environment, account, payout_id)
            return PayoutAnswer(settlement_call, payout)
        case LinesCall(account=account, payout_id=payout_id, after=after):
            page = await registry.fetch_payout_lines(
                http, environment, account, payout_id, after
            )
            return LinesAnswer(settlement_call, page)


async def complete_fetch(
    conn: Connection,
    account_id: uuid.UUID,
    call: SettlementCall,
    answer: SettlementAnswer,
    now: datetime,
) -> Done | RetryLater | Again:
    """`call` is the read as one union; each answer holds it typed."""
    await _lock_cursor(conn, account_id)
    match answer:
        case ListAnswer(call=call, result=result):
            return await _listed(conn, account_id, call, result, now)
        case FileAnswer(call=call, result=result):
            return await _file_fetched(conn, account_id, call, result, now)
        case PayoutAnswer(call=call, result=result):
            return await _payout_fetched(conn, account_id, call, result, now)
        case LinesAnswer(call=call, result=result):
            return await _lines_fetched(conn, account_id, call, result, now)


def _retry(failure: Unavailable | OutcomeUnknown | UndecodableBody) -> RetryLater:
    match failure:
        case Unavailable(detail=detail) | OutcomeUnknown(detail=detail):
            # Reads only: trying again later is always safe.
            return RetryLater(f"settlement: {detail}")
        case UndecodableBody(detail=detail):
            # An answer the PSP does not document: retried, and visible as
            # a failing delivery until a person or a fixed client reads it.
            return RetryLater(f"settlement answer: {detail}")


async def _look_now(conn: Connection, account_id: uuid.UUID, now: datetime) -> None:
    """There is more to read: the next look now, in a delivery of its own."""
    await append_fact(
        conn, PspSettlementDueV1(psp_account_id=account_id), SCHEDULER, now
    )


async def _look_later(conn: Connection, account: PspAccount, now: datetime) -> None:
    """Nothing more to read now: the next look after the account's
    interval."""
    await append_fact(
        conn,
        PspSettlementDueV1(psp_account_id=account.id),
        SCHEDULER,
        now,
        deliver_at=now + account.settlement_interval,
    )


async def _listed(
    conn: Connection,
    account_id: uuid.UUID,
    list_call: ListCall,
    result: SettlementListResult | UndecodableBody,
    now: datetime,
) -> Done | RetryLater | Again:
    match result:
        case SettlementList(names=names):
            if not names:
                await _look_later(conn, list_call.account, now)
                return Done(Applied())
            phase: ReadPhase | None = None
            match list_call.account.settlement_mode:
                case SettlementMode.FILE:
                    pass
                case SettlementMode.API:
                    phase = ReadPhase.PAYOUT
            await conn.execute(
                """
                UPDATE psp_settlement_cursor
                SET pending = %s, pending_phase = %s, pending_after = NULL
                WHERE account_id = %s
                """,
                (names[0], phase, account_id),
            )
            return Again()
        case Unavailable() | OutcomeUnknown() | UndecodableBody():
            return _retry(result)


async def _read_whole(conn: Connection, account_id: uuid.UUID, name: str) -> None:
    """The pending report or payout is read: on to the next."""
    await conn.execute(
        """
        UPDATE psp_settlement_cursor
        SET last_read = %s, pending = NULL, pending_phase = NULL,
            pending_after = NULL
        WHERE account_id = %s
        """,
        (name, account_id),
    )


async def _file_fetched(
    conn: Connection,
    account_id: uuid.UUID,
    file_call: FileCall,
    result: SettlementFileResult | UndecodableBody,
    now: datetime,
) -> Done | RetryLater:
    match result:
        case SettlementFile(body=body):
            await store_inbox(
                conn, account_id, InboundMode.SETTLEMENT_FILE, body, None, now
            )
            await _read_whole(conn, account_id, file_call.name)
            await _look_now(conn, account_id, now)
            return Done(Applied())
        case Unavailable() | OutcomeUnknown() | UndecodableBody():
            return _retry(result)


async def _payout_fetched(
    conn: Connection,
    account_id: uuid.UUID,
    payout_call: PayoutCall,
    result: PayoutResult | UndecodableBody,
    now: datetime,
) -> Done | RetryLater:
    match result:
        case PayoutObject(body=body, lines_ready=lines_ready):
            await store_inbox(
                conn,
                account_id,
                InboundMode.SETTLEMENT_PAYOUT,
                body,
                payout_call.payout_id,
                now,
            )
            if not lines_ready:
                # Its lines are not all listed yet: look again later.
                await _look_later(conn, payout_call.account, now)
                return Done(Applied())
            await conn.execute(
                """
                UPDATE psp_settlement_cursor
                SET pending_phase = %s, pending_after = NULL
                WHERE account_id = %s
                """,
                (ReadPhase.LINES, account_id),
            )
            await _look_now(conn, account_id, now)
            return Done(Applied())
        case Unavailable() | OutcomeUnknown() | UndecodableBody():
            return _retry(result)


async def _lines_fetched(
    conn: Connection,
    account_id: uuid.UUID,
    lines_call: LinesCall,
    result: LinesResult | UndecodableBody,
    now: datetime,
) -> Done | RetryLater:
    match result:
        case LinesPage(body=body, last_line_id=last_line_id, has_more=has_more):
            if has_more and last_line_id is None:
                return _retry(UndecodableBody("more lines promised after none"))
            await store_inbox(
                conn,
                account_id,
                InboundMode.SETTLEMENT_PAGE,
                body,
                lines_call.payout_id,
                now,
            )
            if has_more:
                await conn.execute(
                    "UPDATE psp_settlement_cursor SET pending_after = %s"
                    " WHERE account_id = %s",
                    (last_line_id, account_id),
                )
            else:
                await _read_whole(conn, account_id, lines_call.payout_id)
            await _look_now(conn, account_id, now)
            return Done(Applied())
        case Unavailable() | OutcomeUnknown() | UndecodableBody():
            return _retry(result)

"""Duty and VAT rates from vendors, asked once per combination
(docs/plan-quote.md §3, 4b-2).

    request_duty_rate ──duty_rate_needed──▶ duty_rate_ask (effect consumer)
        requested ──ask──▶ answered | unknown_code | needs_person
        (Unavailable, OutcomeUnknown: asked again after the worker's backoff)

The unique key on (vendor, destination, origin, HS code) is the "ask once"
guard. A lookup is a read, so asking again after a lost answer is safe
and needs no duplicate guard; each call is counted before it is made, so
every billed call shows. `prepare` and `complete` run in the worker's
transactions under the lookup's row lock and never commit; `call` runs
with no transaction open.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Literal

import httpx2
from psycopg.rows import class_row

from mor.audit import Actor, ActorKind
from mor.config import Environment
from mor.db.connect import Connection
from mor.db.fetch import one_value
from mor.duties import registry
from mor.duties.port import (
    AskResult,
    DutyProvider,
    DutyVendorAccount,
    RateQuestion,
    UnknownCode,
    VendorRates,
)
from mor.duties.records import LookupState, StoredAnswer, VendorLookup
from mor.markets.records import Charge, MarketSet
from mor.markets.store import latest_load, read_market
from mor.messaging.facts import (
    DutyRateDisagreedV1,
    DutyRateFoundV1,
    DutyRateNeededV1,
    DutyRateNeedsPersonV1,
    DutyRateUnknownCodeV1,
    Fact,
)
from mor.messaging.outcome import Applied, Done, NoCall, RetryLater, Stale
from mor.messaging.routing import WrongFact
from mor.messaging.store import append_fact
from mor.provider_accounts import ProviderAccountKind
from mor.tax.line_rates import Line, own_standard_row
from mor.vendor_failures import OutcomeUnknown, Unavailable, UndecodableBody


ACTOR = Actor(ActorKind.SYSTEM, "duty_rate_ask")


@dataclass(frozen=True)
class NoVendorAccount:
    """This environment has no account for the vendor the market data
    names: nothing can be asked."""

    vendor: str


@dataclass(frozen=True)
class AskCall:
    account: DutyVendorAccount
    lookup_id: uuid.UUID
    question: RateQuestion


@dataclass(frozen=True)
class AskAnswer:
    """The call and what the vendor said to it, from `call` to `complete`."""

    call: AskCall
    result: AskResult


# --- rows -----------------------------------------------------------------


@dataclass(frozen=True)
class _LookupRow:
    id: uuid.UUID
    vendor_name: str
    destination: str
    origin: str
    hs_code: str
    state: LookupState
    calls: int
    detail: str | None


@dataclass(frozen=True)
class _AccountRow:
    id: uuid.UUID
    vendor_name: str
    provider: DutyProvider
    kind: ProviderAccountKind
    base_url: str
    timeout_ms: int


@dataclass(frozen=True)
class _AnswerRow:
    charge: Charge
    formula: str
    rate_numerator: int | None
    rate_denominator: int | None
    note: str | None
    hs_code_used: str
    own_rate_numerator: int | None
    own_rate_denominator: int | None
    own_line_no: int | None


async def create_vendor_account(
    conn: Connection,
    vendor_name: str,
    provider: DutyProvider,
    kind: ProviderAccountKind,
    base_url: str,
    timeout: timedelta,
) -> uuid.UUID:
    account_id = uuid.uuid7()
    await conn.execute(
        """
        INSERT INTO duty_vendor_account
          (id, vendor_name, provider, kind, base_url, timeout_ms)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            account_id,
            vendor_name,
            provider,
            kind,
            base_url,
            int(timeout.total_seconds() * 1000),
        ),
    )
    return account_id


async def vendor_account(
    conn: Connection, vendor_name: str
) -> DutyVendorAccount | None:
    async with conn.cursor(row_factory=class_row(_AccountRow)) as cur:
        await cur.execute(
            "SELECT id, vendor_name, provider, kind, base_url, timeout_ms"
            " FROM duty_vendor_account WHERE vendor_name = %s",
            (vendor_name,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return DutyVendorAccount(
        id=row.id,
        vendor_name=row.vendor_name,
        provider=row.provider,
        kind=row.kind,
        base_url=row.base_url,
        timeout=timedelta(milliseconds=row.timeout_ms),
    )


async def request_duty_rate(
    conn: Connection,
    vendor_name: str,
    question: RateQuestion,
    actor: Actor,
    now: datetime,
) -> uuid.UUID | NoVendorAccount:
    """The lookup for this combination, asked for the first time if new.
    Insert first; a conflict means it was asked before, and that lookup is
    the answer. Never commits."""
    if await vendor_account(conn, vendor_name) is None:
        return NoVendorAccount(vendor_name)
    cur = await conn.execute(
        """
        INSERT INTO duty_lookup
          (id, vendor_name, destination, origin, hs_code, state, created_at,
           updated_at)
        VALUES (%s, %s, %s, %s, %s, 'requested', %s, %s)
        ON CONFLICT (vendor_name, destination, origin, hs_code) DO NOTHING
        RETURNING id
        """,
        (
            uuid.uuid7(),
            vendor_name,
            question.destination,
            question.origin,
            question.hs_code,
            now,
            now,
        ),
    )
    row = await cur.fetchone()
    if row is not None:
        lookup_id: uuid.UUID = row[0]
        await append_fact(conn, DutyRateNeededV1(duty_lookup_id=lookup_id), actor, now)
        return lookup_id
    # The insert conflicted, so the lookup exists.
    return await one_value(
        conn,
        """
        SELECT id FROM duty_lookup
        WHERE vendor_name = %s AND destination = %s AND origin = %s AND hs_code = %s
        """,
        (vendor_name, question.destination, question.origin, question.hs_code),
        uuid.UUID,
    )


async def read_lookup(
    conn: Connection, vendor_name: str, question: RateQuestion
) -> VendorLookup | None:
    """What we hold for a combination from a vendor, with its answers."""
    async with conn.cursor(row_factory=class_row(_LookupRow)) as cur:
        await cur.execute(
            """
            SELECT id, vendor_name, destination, origin, hs_code, state,
                   calls, detail
            FROM duty_lookup
            WHERE vendor_name = %s AND destination = %s AND origin = %s
              AND hs_code = %s
            """,
            (vendor_name, question.destination, question.origin, question.hs_code),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return await _with_answers(conn, row)


async def _with_answers(conn: Connection, row: _LookupRow) -> VendorLookup:
    answers: list[StoredAnswer] = []
    async with conn.cursor(row_factory=class_row(_AnswerRow)) as cur:
        await cur.execute(
            """
            SELECT charge, formula, rate_numerator, rate_denominator, note,
                   hs_code_used, own_rate_numerator, own_rate_denominator,
                   own_line_no
            FROM duty_answer WHERE lookup_id = %s ORDER BY charge
            """,
            (row.id,),
        )
        for answer in await cur.fetchall():
            answers.append(
                StoredAnswer(
                    charge=answer.charge,
                    formula=answer.formula,
                    rate=_fraction(answer.rate_numerator, answer.rate_denominator),
                    note=answer.note,
                    hs_code_used=answer.hs_code_used,
                    own_rate=_fraction(
                        answer.own_rate_numerator, answer.own_rate_denominator
                    ),
                    own_line_no=answer.own_line_no,
                )
            )
    return VendorLookup(
        id=row.id,
        vendor=row.vendor_name,
        state=row.state,
        calls=row.calls,
        detail=row.detail,
        answers=tuple(answers),
    )


def _fraction(numerator: int | None, denominator: int | None) -> Fraction | None:
    if numerator is None or denominator is None:
        return None
    return Fraction(numerator, denominator)


async def _lock_lookup(conn: Connection, lookup_id: uuid.UUID) -> _LookupRow:
    async with conn.cursor(row_factory=class_row(_LookupRow)) as cur:
        await cur.execute(
            """
            SELECT id, vendor_name, destination, origin, hs_code, state,
                   calls, detail
            FROM duty_lookup WHERE id = %s FOR UPDATE
            """,
            (lookup_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"duty lookup {lookup_id} does not exist")
    return row


def _lookup_id(fact: Fact) -> uuid.UUID:
    match fact:
        case DutyRateNeededV1(duty_lookup_id=lookup_id):
            return lookup_id
        case _:
            raise WrongFact(f"duty_rate_ask got {fact!r}")


# --- prepare, call, complete ----------------------------------------------


async def prepare(conn: Connection, fact: Fact, now: datetime) -> NoCall | AskCall:
    lookup = await _lock_lookup(conn, _lookup_id(fact))
    if lookup.state != LookupState.REQUESTED:
        return NoCall(Stale())
    account = await vendor_account(conn, lookup.vendor_name)
    assert account is not None, f"no account for {lookup.vendor_name}"
    # Counted before the call and committed with this step: a call that is
    # made is always counted, even if the worker dies right after.
    await conn.execute(
        "UPDATE duty_lookup SET calls = calls + 1, updated_at = %s WHERE id = %s",
        (now, lookup.id),
    )
    question = RateQuestion(lookup.destination, lookup.origin, lookup.hs_code)
    return AskCall(account, lookup.id, question)


async def call(
    http: httpx2.AsyncClient, environment: Environment, ask: AskCall
) -> AskAnswer:
    result = await registry.ask_rates(http, environment, ask.account, ask.question)
    return AskAnswer(ask, result)


async def complete(
    conn: Connection, fact: Fact, answer: AskAnswer, now: datetime
) -> Done | RetryLater:
    lookup = await _lock_lookup(conn, _lookup_id(fact))
    assert lookup.id == answer.call.lookup_id
    result = answer.result
    if lookup.state != LookupState.REQUESTED:
        return Done(Stale())
    match result:
        case VendorRates():
            await _store_answers(conn, lookup, result, now)
            return Done(Applied())
        case UnknownCode():
            await _set_state(conn, lookup.id, LookupState.UNKNOWN_CODE, None, now)
            await append_fact(
                conn, DutyRateUnknownCodeV1(duty_lookup_id=lookup.id), ACTOR, now
            )
            return Done(Applied())
        case UndecodableBody(detail=detail):
            await _set_state(conn, lookup.id, LookupState.NEEDS_PERSON, detail, now)
            await append_fact(
                conn,
                DutyRateNeedsPersonV1(duty_lookup_id=lookup.id, detail=detail),
                ACTOR,
                now,
            )
            return Done(Applied())
        case Unavailable(detail=detail):
            return RetryLater(f"unavailable: {detail}")
        case OutcomeUnknown(detail=detail):
            return RetryLater(f"outcome unknown: {detail}")


async def _set_state(
    conn: Connection,
    lookup_id: uuid.UUID,
    state: LookupState,
    detail: str | None,
    now: datetime,
) -> None:
    await conn.execute(
        "UPDATE duty_lookup SET state = %s, detail = %s, updated_at = %s WHERE id = %s",
        (state.value, detail, now, lookup_id),
    )


async def _market_in_force(conn: Connection) -> tuple[uuid.UUID, MarketSet] | None:
    load = await latest_load(conn)
    if load is None:
        return None
    return load.id, await read_market(conn, load.id)


async def _store_answers(
    conn: Connection, lookup: _LookupRow, rates: VendorRates, now: datetime
) -> None:
    """Each charge's answer next to our own standard row in the load in
    force, and a `duty_rate_disagreed` where both are rates and differ."""
    in_force = await _market_in_force(conn)
    line = Line(lookup.hs_code, lookup.origin, frozenset())
    for rate in rates.rates:
        load_id: uuid.UUID | None = None
        own_rate: Fraction | None = None
        own_line_no: int | None = None
        if in_force is not None:
            load_id, market = in_force
            own = own_standard_row(
                market, lookup.destination, rate.charge, line, now.date()
            )
            if own is not None:
                own_rate = own.rate
                own_line_no = own.line_no
        await conn.execute(
            """
            INSERT INTO duty_answer
              (lookup_id, charge, formula, rate_numerator, rate_denominator, note,
               hs_code_used, compared_load_id, own_rate_numerator,
               own_rate_denominator, own_line_no, answered_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                lookup.id,
                rate.charge.value,
                rate.formula,
                None if rate.rate is None else rate.rate.numerator,
                None if rate.rate is None else rate.rate.denominator,
                rate.note,
                rates.hs_code_used,
                load_id,
                None if own_rate is None else own_rate.numerator,
                None if own_rate is None else own_rate.denominator,
                own_line_no,
                now,
            ),
        )
        if (
            rate.rate is not None
            and own_rate is not None
            and own_line_no is not None
            and rate.rate != own_rate
        ):
            await append_fact(
                conn,
                DutyRateDisagreedV1(
                    duty_lookup_id=lookup.id,
                    charge=rate.charge.value,
                    vendor_rate=f"{rate.rate.numerator}/{rate.rate.denominator}",
                    own_rate=f"{own_rate.numerator}/{own_rate.denominator}",
                    own_line_no=own_line_no,
                ),
                ACTOR,
                now,
            )
    await _set_state(conn, lookup.id, LookupState.ANSWERED, None, now)
    await append_fact(conn, DutyRateFoundV1(duty_lookup_id=lookup.id), ACTOR, now)


# --- agreement --------------------------------------------------------------


@dataclass(frozen=True)
class Agreement:
    """Per destination and charge, how the vendor's answers compare with
    our own rows: the count a switch to `own_first` is judged on."""

    destination: str
    charge: Charge
    matched: int
    differed: int
    own_none: int  # we had no row
    not_a_rate: int  # the vendor's formula is not a plain percentage


async def agreement(conn: Connection) -> list[Agreement]:
    async with conn.cursor(row_factory=class_row(Agreement)) as cur:
        await cur.execute(
            """
            SELECT l.destination, a.charge,
              count(*) FILTER (WHERE a.rate_numerator IS NOT NULL
                AND a.own_rate_numerator IS NOT NULL
                AND a.rate_numerator * a.own_rate_denominator
                  = a.own_rate_numerator * a.rate_denominator) AS matched,
              count(*) FILTER (WHERE a.rate_numerator IS NOT NULL
                AND a.own_rate_numerator IS NOT NULL
                AND a.rate_numerator * a.own_rate_denominator
                  <> a.own_rate_numerator * a.rate_denominator) AS differed,
              count(*) FILTER (WHERE a.rate_numerator IS NOT NULL
                AND a.own_rate_numerator IS NULL) AS own_none,
              count(*) FILTER (WHERE a.rate_numerator IS NULL) AS not_a_rate
            FROM duty_answer a JOIN duty_lookup l ON l.id = a.lookup_id
            GROUP BY l.destination, a.charge
            ORDER BY l.destination, a.charge
            """
        )
        return await cur.fetchall()


type Comparison = Literal["differs", "not_a_rate", "own_none", "agrees"]


@dataclass(frozen=True)
class AnswerRow:
    """One vendor answer beside our own row in force when it came."""

    vendor: str
    destination: str
    origin: str
    hs_code: str
    charge: Charge
    formula: str
    note: str | None
    own_rate: Fraction | None
    own_line_no: int | None
    comparison: Comparison
    answered_at: datetime


@dataclass(frozen=True)
class _VendorAnswerRow:
    vendor_name: str
    destination: str
    origin: str
    hs_code: str
    charge: Charge
    formula: str
    note: str | None
    own_rate_numerator: int | None
    own_rate_denominator: int | None
    own_line_no: int | None
    answered_at: datetime
    comparison: str


async def vendor_answers(conn: Connection, limit: int) -> list[AnswerRow]:
    """The answers, differences first, then those that are not a plain
    rate, those we had no row for, and agreements; newest first in each."""
    async with conn.cursor(row_factory=class_row(_VendorAnswerRow)) as cur:
        await cur.execute(
            """
            SELECT l.vendor_name, l.destination, l.origin, l.hs_code, a.charge,
              a.formula, a.note, a.own_rate_numerator, a.own_rate_denominator,
              a.own_line_no, a.answered_at,
              CASE
                WHEN a.rate_numerator IS NULL THEN 'not_a_rate'
                WHEN a.own_rate_numerator IS NULL THEN 'own_none'
                WHEN a.rate_numerator * a.own_rate_denominator
                  = a.own_rate_numerator * a.rate_denominator THEN 'agrees'
                ELSE 'differs'
              END AS comparison
            FROM duty_answer a JOIN duty_lookup l ON l.id = a.lookup_id
            ORDER BY
              CASE
                WHEN a.rate_numerator IS NULL THEN 1
                WHEN a.own_rate_numerator IS NULL THEN 2
                WHEN a.rate_numerator * a.own_rate_denominator
                  = a.own_rate_numerator * a.rate_denominator THEN 3
                ELSE 0
              END,
              a.answered_at DESC, l.destination, a.charge
            LIMIT %s
            """,
            (limit,),
        )
        found = await cur.fetchall()
    rows: list[AnswerRow] = []
    for row in found:
        rows.append(
            AnswerRow(
                vendor=row.vendor_name,
                destination=row.destination,
                origin=row.origin,
                hs_code=row.hs_code,
                charge=row.charge,
                formula=row.formula,
                note=row.note,
                own_rate=_fraction(row.own_rate_numerator, row.own_rate_denominator),
                own_line_no=row.own_line_no,
                comparison=_comparison(row.comparison),
                answered_at=row.answered_at,
            )
        )
    return rows


def _comparison(text: str) -> Comparison:
    match text:
        case "differs" | "not_a_rate" | "own_none" | "agrees":
            return text
        case _:
            raise AssertionError(f"comparison {text!r} from our own query")

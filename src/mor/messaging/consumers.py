"""The dispatch tables: each consumer is plain functions, picked by an
exhaustive `match` on its name. A local consumer has `handle`; an effect
consumer has `prepare`, `call` and `complete` (plan-architecture.md §5).
Asking a consumer for a step of the other kind is a bug, so it raises."""

from datetime import datetime
from typing import assert_never

import httpx2

from mor.config import Environment
from mor.db.connect import Connection
from mor.echo import flow as echo_flow
from mor.echo import inbound as echo_inbound
from mor.echo import reports as echo_reports
from mor.echo import tracking as echo_tracking
from mor.echo.port import OutcomeUnknown, Page, ReportList, Unavailable
from mor.messaging.facts import Fact
from mor.messaging.outcome import ConsumerOutcome, NoCall, StepResult
from mor.messaging.routing import ConsumerName
from mor.probe import flow as probe_flow


type EffectCall = (
    echo_flow.EchoCall | echo_tracking.FetchEventsCall | echo_reports.ReportCall
)
type EffectResult = echo_flow.EchoResult | Page | ReportList


class WrongKind(Exception):
    """A step was asked of a consumer of the other kind."""


class WrongCall(Exception):
    """A consumer's `complete` got a call or result it never asks for."""


async def handle(
    conn: Connection, consumer: ConsumerName, fact: Fact, now: datetime
) -> ConsumerOutcome:
    """A local consumer, in the worker's transaction. Never commits."""
    match consumer:
        case ConsumerName.PROBE_ECHO:
            return await probe_flow.echo(conn, fact, now)
        case ConsumerName.PROBE_FINISH:
            return await probe_flow.finish(conn, fact, now)
        case ConsumerName.ECHO_INBOX_DECODE:
            return await echo_inbound.decode_inbox(conn, fact, now)
        case ConsumerName.ECHO_EVENT_APPLY:
            return await echo_inbound.apply_event(conn, fact, now)
        case ConsumerName.ECHO_TRACKING_START:
            return await echo_tracking.start_tracking(conn, fact, now)
        case (
            ConsumerName.ECHO_SEND
            | ConsumerName.ECHO_POLL
            | ConsumerName.ECHO_REPORT_FETCH
        ):
            raise WrongKind(f"{consumer} is an effect consumer")
        case _:
            assert_never(consumer)


async def prepare(
    conn: Connection, consumer: ConsumerName, fact: Fact, now: datetime
) -> NoCall | EffectCall:
    """An effect consumer's first step, in its own transaction."""
    match consumer:
        case ConsumerName.ECHO_SEND:
            return await echo_flow.prepare(conn, fact, now)
        case ConsumerName.ECHO_POLL:
            return await echo_tracking.prepare_poll(conn, fact, now)
        case ConsumerName.ECHO_REPORT_FETCH:
            return await echo_reports.prepare_reports(conn, fact, now)
        case (
            ConsumerName.PROBE_ECHO
            | ConsumerName.PROBE_FINISH
            | ConsumerName.ECHO_INBOX_DECODE
            | ConsumerName.ECHO_EVENT_APPLY
            | ConsumerName.ECHO_TRACKING_START
        ):
            raise WrongKind(f"{consumer} is a local consumer")
        case _:
            assert_never(consumer)


async def call(
    http: httpx2.AsyncClient, environment: Environment, effect_call: EffectCall
) -> EffectResult:
    """The outside call, with no transaction open. The call record says
    which port it is for."""
    match effect_call:
        case echo_flow.SendCall() | echo_flow.FindCall() | echo_flow.CancelCall():
            return await echo_flow.call(http, environment, effect_call)
        case echo_tracking.FetchEventsCall():
            return await echo_tracking.call_poll(http, environment, effect_call)
        case echo_reports.ListReportsCall() | echo_reports.FetchReportCall():
            return await echo_reports.call_reports(http, environment, effect_call)
        case _:
            assert_never(effect_call)


async def complete(
    conn: Connection,
    consumer: ConsumerName,
    fact: Fact,
    effect_call: EffectCall,
    result: EffectResult,
    now: datetime,
) -> StepResult:
    """An effect consumer's last step, in its own transaction."""
    match consumer:
        case ConsumerName.ECHO_SEND:
            if not isinstance(
                effect_call,
                echo_flow.SendCall | echo_flow.FindCall | echo_flow.CancelCall,
            ) or isinstance(result, Page | ReportList):
                raise WrongCall(f"echo_send got {effect_call!r}, {result!r}")
            return await echo_flow.complete(conn, fact, effect_call, result, now)
        case ConsumerName.ECHO_POLL:
            if not isinstance(effect_call, echo_tracking.FetchEventsCall):
                raise WrongCall(f"echo_poll got {effect_call!r}")
            if not isinstance(result, Page | Unavailable | OutcomeUnknown):
                raise WrongCall(f"echo_poll got {result!r}")
            return await echo_tracking.complete_poll(
                conn, fact, effect_call, result, now
            )
        case ConsumerName.ECHO_REPORT_FETCH:
            if not isinstance(
                effect_call, echo_reports.ListReportsCall | echo_reports.FetchReportCall
            ):
                raise WrongCall(f"echo_report_fetch got {effect_call!r}")
            if not isinstance(result, ReportList | Page | Unavailable | OutcomeUnknown):
                raise WrongCall(f"echo_report_fetch got {result!r}")
            return await echo_reports.complete_reports(
                conn, fact, effect_call, result, now
            )
        case (
            ConsumerName.PROBE_ECHO
            | ConsumerName.PROBE_FINISH
            | ConsumerName.ECHO_INBOX_DECODE
            | ConsumerName.ECHO_EVENT_APPLY
            | ConsumerName.ECHO_TRACKING_START
        ):
            raise WrongKind(f"{consumer} is a local consumer")
        case _:
            assert_never(consumer)

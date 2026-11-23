"""The one place that picks an echo provider module by name. Every call
first checks that this environment may use the account's kind
(`mor.provider_accounts`).

Transitional with the echo flow (docs/plan-messaging.md §10).
"""

from typing import assert_never

import httpx2

from mor.config import Environment
from mor.echo import fake_client
from mor.echo.port import (
    CancelResult,
    EchoAccount,
    EchoProvider,
    EventMode,
    FetchResult,
    FindResult,
    ListResult,
    SendRequest,
    SendResult,
    VendorEvent,
)
from mor.provider_accounts import require_allowed
from mor.vendor_failures import UndecodableBody


def _check(environment: Environment, account: EchoAccount) -> None:
    require_allowed(environment, account.kind, f"echo account {account.id}")


async def send(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: EchoAccount,
    request: SendRequest,
) -> SendResult:
    _check(environment, account)
    match account.provider:
        case (
            EchoProvider.FAKE_KEYED
            | EchoProvider.FAKE_CANCELLABLE
            | EchoProvider.FAKE_PLAIN
        ):
            return await fake_client.send(http, account, request)
        case _:
            assert_never(account.provider)


async def find_by_reference(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: EchoAccount,
    reference: str,
) -> FindResult:
    _check(environment, account)
    match account.provider:
        case (
            EchoProvider.FAKE_KEYED
            | EchoProvider.FAKE_CANCELLABLE
            | EchoProvider.FAKE_PLAIN
        ):
            return await fake_client.find_by_reference(http, account, reference)
        case _:
            assert_never(account.provider)


async def cancel(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: EchoAccount,
    echo_id: str,
) -> CancelResult:
    _check(environment, account)
    match account.provider:
        case (
            EchoProvider.FAKE_KEYED
            | EchoProvider.FAKE_CANCELLABLE
            | EchoProvider.FAKE_PLAIN
        ):
            return await fake_client.cancel(http, account, echo_id)
        case _:
            assert_never(account.provider)


def verify_webhook(
    account: EchoAccount, secret: str, body: bytes, signature: str | None
) -> bool:
    match account.provider:
        case (
            EchoProvider.FAKE_KEYED
            | EchoProvider.FAKE_CANCELLABLE
            | EchoProvider.FAKE_PLAIN
        ):
            return fake_client.verify_webhook(secret, body, signature)
        case _:
            assert_never(account.provider)


def decode_events(
    account: EchoAccount, mode: EventMode, body: bytes
) -> tuple[VendorEvent, ...] | UndecodableBody:
    """Raw bytes stored in the inbox, in the shape `mode` delivers, into
    our event records."""
    match account.provider:
        case (
            EchoProvider.FAKE_KEYED
            | EchoProvider.FAKE_CANCELLABLE
            | EchoProvider.FAKE_PLAIN
        ):
            match mode:
                case EventMode.PUSH | EventMode.POLL:
                    # A poll page has the full webhook's shape.
                    return fake_client.decode_full_webhook(body)
                case EventMode.PUSH_THIN:
                    raise AssertionError("a thin webhook carries no events")
                case EventMode.FILE:
                    return fake_client.decode_report(body)
                case EventMode.NONE:
                    raise AssertionError("a `none` account stores nothing")
                case _:
                    assert_never(mode)
        case _:
            assert_never(account.provider)


def decode_thin_webhook(account: EchoAccount, body: bytes) -> str | UndecodableBody:
    """A thin webhook into the reference of the job it is about."""
    match account.provider:
        case (
            EchoProvider.FAKE_KEYED
            | EchoProvider.FAKE_CANCELLABLE
            | EchoProvider.FAKE_PLAIN
        ):
            return fake_client.decode_thin_webhook(body)
        case _:
            assert_never(account.provider)


async def fetch_events(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: EchoAccount,
    reference: str,
) -> FetchResult:
    _check(environment, account)
    match account.provider:
        case (
            EchoProvider.FAKE_KEYED
            | EchoProvider.FAKE_CANCELLABLE
            | EchoProvider.FAKE_PLAIN
        ):
            return await fake_client.fetch_events(http, account, reference)
        case _:
            assert_never(account.provider)


async def list_reports(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: EchoAccount,
    after: str,
) -> ListResult:
    _check(environment, account)
    match account.provider:
        case (
            EchoProvider.FAKE_KEYED
            | EchoProvider.FAKE_CANCELLABLE
            | EchoProvider.FAKE_PLAIN
        ):
            return await fake_client.list_reports(http, account, after)
        case _:
            assert_never(account.provider)


async def fetch_report(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: EchoAccount,
    name: str,
) -> FetchResult:
    _check(environment, account)
    match account.provider:
        case (
            EchoProvider.FAKE_KEYED
            | EchoProvider.FAKE_CANCELLABLE
            | EchoProvider.FAKE_PLAIN
        ):
            return await fake_client.fetch_report(http, account, name)
        case _:
            assert_never(account.provider)

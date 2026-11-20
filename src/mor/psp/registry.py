"""The one place that picks a PSP module by its provider (plan-architecture
§6). Every call first checks that this environment may use the account's
kind (`mor.provider_accounts`)."""

from typing import assert_never

import httpx2

from mor.config import Environment
from mor.money.amount import Money
from mor.provider_accounts import require_allowed
from mor.psp import fake_client
from mor.psp.port import (
    CancelResult,
    CaptureResult,
    CreateResult,
    FetchResult,
    FindResult,
    PaymentRequest,
    PspAccount,
    PspEvent,
    PspProvider,
    ThinNotice,
    UndecodableBody,
)


def _check(environment: Environment, account: PspAccount) -> None:
    require_allowed(environment, account.kind, f"psp account {account.name}")


async def create_payment(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: PspAccount,
    request: PaymentRequest,
) -> CreateResult:
    _check(environment, account)
    match account.provider:
        case PspProvider.FAKE:
            return await fake_client.create_payment(http, account, request)
        case _:
            assert_never(account.provider)


async def find_payments(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: PspAccount,
    reference: str,
) -> FindResult:
    _check(environment, account)
    match account.provider:
        case PspProvider.FAKE:
            return await fake_client.find_payments(http, account, reference)
        case _:
            assert_never(account.provider)


async def capture(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: PspAccount,
    provider_payment_id: str,
    amount: Money,
    key: str,
) -> CaptureResult:
    _check(environment, account)
    match account.provider:
        case PspProvider.FAKE:
            return await fake_client.capture(
                http, account, provider_payment_id, amount, key
            )
        case _:
            assert_never(account.provider)


async def cancel(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: PspAccount,
    provider_payment_id: str,
) -> CancelResult:
    _check(environment, account)
    match account.provider:
        case PspProvider.FAKE:
            return await fake_client.cancel(http, account, provider_payment_id)
        case _:
            assert_never(account.provider)


async def fetch_events(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: PspAccount,
    provider_payment_id: str,
) -> FetchResult:
    _check(environment, account)
    match account.provider:
        case PspProvider.FAKE:
            return await fake_client.fetch_events(http, account, provider_payment_id)
        case _:
            assert_never(account.provider)


def signature_header(account: PspAccount) -> str:
    match account.provider:
        case PspProvider.FAKE:
            return fake_client.SIGNATURE_HEADER
        case _:
            assert_never(account.provider)


def verify_webhook(account: PspAccount, body: bytes, signature: str | None) -> bool:
    match account.provider:
        case PspProvider.FAKE:
            return fake_client.verify_webhook(account, body, signature)
        case _:
            assert_never(account.provider)


def decode_events(
    account: PspAccount, body: bytes
) -> tuple[PspEvent, ...] | UndecodableBody:
    match account.provider:
        case PspProvider.FAKE:
            return fake_client.decode_events(body)
        case _:
            assert_never(account.provider)


def decode_thin_webhook(
    account: PspAccount, body: bytes
) -> ThinNotice | UndecodableBody:
    match account.provider:
        case PspProvider.FAKE:
            return fake_client.decode_thin_webhook(body)
        case _:
            assert_never(account.provider)

"""The one place that picks a PSP module by its provider (plan-architecture
§6). Every call first checks that this environment may use the account's
kind (`mor.provider_accounts`)."""

from typing import Literal

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
    InboundMode,
    LinesResult,
    PaymentRequest,
    PayoutResult,
    PspAccount,
    PspEvent,
    PspProvider,
    SettlementDocument,
    SettlementFileResult,
    SettlementListResult,
    ThinNotice,
)
from mor.vendor_failures import UndecodableBody


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


def signature_header(account: PspAccount) -> str:
    match account.provider:
        case PspProvider.FAKE:
            return fake_client.SIGNATURE_HEADER


def verify_webhook(account: PspAccount, body: bytes, signature: str | None) -> bool:
    match account.provider:
        case PspProvider.FAKE:
            return fake_client.verify_webhook(account, body, signature)


def decode_events(
    account: PspAccount, body: bytes
) -> tuple[PspEvent, ...] | UndecodableBody:
    match account.provider:
        case PspProvider.FAKE:
            return fake_client.decode_events(body)


def decode_thin_webhook(
    account: PspAccount, body: bytes
) -> ThinNotice | UndecodableBody:
    match account.provider:
        case PspProvider.FAKE:
            return fake_client.decode_thin_webhook(body)


async def list_settlements(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: PspAccount,
    after: str,
) -> SettlementListResult | UndecodableBody:
    _check(environment, account)
    match account.provider:
        case PspProvider.FAKE:
            return await fake_client.list_settlements(http, account, after)


async def fetch_settlement_file(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: PspAccount,
    name: str,
) -> SettlementFileResult | UndecodableBody:
    _check(environment, account)
    match account.provider:
        case PspProvider.FAKE:
            return await fake_client.fetch_settlement_file(http, account, name)


async def fetch_payout(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: PspAccount,
    payout_id: str,
) -> PayoutResult | UndecodableBody:
    _check(environment, account)
    match account.provider:
        case PspProvider.FAKE:
            return await fake_client.fetch_payout(http, account, payout_id)


async def fetch_payout_lines(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: PspAccount,
    payout_id: str,
    after: str | None,
) -> LinesResult | UndecodableBody:
    _check(environment, account)
    match account.provider:
        case PspProvider.FAKE:
            return await fake_client.fetch_payout_lines(http, account, payout_id, after)


type SettlementInbound = Literal[
    InboundMode.SETTLEMENT_FILE,
    InboundMode.SETTLEMENT_PAYOUT,
    InboundMode.SETTLEMENT_PAGE,
]


def decode_settlement(
    account: PspAccount,
    mode: SettlementInbound,
    body: bytes,
    request_ref: str | None,
) -> SettlementDocument | UndecodableBody:
    """`request_ref`: what we asked for, stored with the bytes; a page of
    lines needs it to know its payout."""
    match account.provider:
        case PspProvider.FAKE:
            match mode:
                case InboundMode.SETTLEMENT_FILE:
                    return fake_client.decode_settlement_file(body)
                case InboundMode.SETTLEMENT_PAYOUT:
                    return fake_client.decode_payout(body)
                case InboundMode.SETTLEMENT_PAGE:
                    if request_ref is None:
                        return UndecodableBody("a page of lines names no payout")
                    return fake_client.decode_payout_lines(body, request_ref)

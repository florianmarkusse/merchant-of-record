"""The one place that picks a carrier module by its provider. Every call
first checks that this environment may use the account's kind
(`mor.provider_accounts`)."""

import httpx2

from mor.carriers import fake_client
from mor.carriers.port import (
    BookRequest,
    BookResult,
    CancelResult,
    CarrierEvent,
    FindResult,
    QuoteRateResult,
    ThinNotice,
)
from mor.carriers.records import CarrierAccount, CarrierProvider, PriceQuestion
from mor.config import Environment
from mor.ids import TrackingNumber
from mor.provider_accounts import require_allowed
from mor.vendor_failures import UndecodableBody


async def quote_rate(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: CarrierAccount,
    question: PriceQuestion,
) -> QuoteRateResult:
    require_allowed(environment, account.kind, f"carrier account {account.carrier}")
    match account.provider:
        case CarrierProvider.FAKE:
            return await fake_client.quote_rate(http, account, question)


async def book(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: CarrierAccount,
    request: BookRequest,
) -> BookResult:
    require_allowed(environment, account.kind, f"carrier account {account.carrier}")
    match account.provider:
        case CarrierProvider.FAKE:
            return await fake_client.book(http, account, request)


async def find_by_reference(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: CarrierAccount,
    reference: str,
) -> FindResult:
    require_allowed(environment, account.kind, f"carrier account {account.carrier}")
    match account.provider:
        case CarrierProvider.FAKE:
            return await fake_client.find_by_reference(http, account, reference)


async def cancel(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: CarrierAccount,
    tracking_number: TrackingNumber,
) -> CancelResult:
    require_allowed(environment, account.kind, f"carrier account {account.carrier}")
    match account.provider:
        case CarrierProvider.FAKE:
            return await fake_client.cancel(http, account, tracking_number)


# Tracking: what a carrier sends us is decoded on one path, whatever the
# mode (docs/plan-shipments.md §7). Decoding is pure: no environment check,
# the bytes are already ours.


def signature_header(account: CarrierAccount) -> str:
    match account.provider:
        case CarrierProvider.FAKE:
            return fake_client.SIGNATURE_HEADER


def verify_webhook(account: CarrierAccount, body: bytes, signature: str | None) -> bool:
    match account.provider:
        case CarrierProvider.FAKE:
            return fake_client.verify_webhook(account, body, signature)


def decode_events(
    account: CarrierAccount, body: bytes
) -> tuple[CarrierEvent, ...] | UndecodableBody:
    """A fat webhook's or a tracking page's events."""
    match account.provider:
        case CarrierProvider.FAKE:
            return fake_client.decode_events(body)


def decode_thin_webhook(
    account: CarrierAccount, body: bytes
) -> ThinNotice | UndecodableBody:
    match account.provider:
        case CarrierProvider.FAKE:
            return fake_client.decode_thin_webhook(body)

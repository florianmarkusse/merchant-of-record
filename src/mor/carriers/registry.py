"""The one place that picks a carrier module by its provider. Every call
first checks that this environment may use the account's kind
(`mor.provider_accounts`)."""

import httpx2

from mor.carriers import fake_client
from mor.carriers.port import (
    BookRequest,
    BookResult,
    CancelResult,
    FindResult,
    QuoteRateResult,
)
from mor.carriers.records import CarrierAccount, CarrierProvider, PriceQuestion
from mor.config import Environment
from mor.ids import TrackingNumber
from mor.provider_accounts import require_allowed


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

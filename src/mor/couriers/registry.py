"""The one place that picks a courier module by its provider. Every call
first checks that this environment may use the account's kind
(`mor.provider_accounts`)."""

import httpx2

from mor.config import Environment
from mor.couriers import fake_client
from mor.couriers.port import QuoteRateResult
from mor.couriers.records import CourierAccount, CourierProvider, PriceQuestion
from mor.provider_accounts import require_allowed


async def quote_rate(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: CourierAccount,
    question: PriceQuestion,
) -> QuoteRateResult:
    require_allowed(environment, account.kind, f"courier account {account.carrier}")
    match account.provider:
        case CourierProvider.FAKE:
            return await fake_client.quote_rate(http, account, question)

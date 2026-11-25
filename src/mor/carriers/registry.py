"""The one place that picks a carrier module by its provider. Every call
first checks that this environment may use the account's kind
(`mor.provider_accounts`)."""

import httpx2

from mor.carriers import fake_client
from mor.carriers.port import QuoteRateResult
from mor.carriers.records import CarrierAccount, CarrierProvider, PriceQuestion
from mor.config import Environment
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

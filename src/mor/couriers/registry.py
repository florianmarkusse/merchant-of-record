"""The one place that picks a courier module by its provider."""

from typing import assert_never

import httpx2

from mor.config import Environment
from mor.couriers import fake_client
from mor.couriers.port import QuoteRateResult
from mor.couriers.records import CourierAccount, CourierProvider, PriceQuestion


class FakeInProd(Exception):
    """A fake provider reached in prod: the fakes never run there
    (plan-architecture.md §6), so an account row is wrong."""


async def quote_rate(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: CourierAccount,
    question: PriceQuestion,
) -> QuoteRateResult:
    match account.provider:
        case CourierProvider.FAKE:
            if environment == Environment.PROD:
                raise FakeInProd(f"courier {account.carrier} is the fake")
            return await fake_client.quote_rate(http, account, question)
        case _:
            assert_never(account.provider)

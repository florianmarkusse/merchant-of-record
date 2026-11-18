"""The one place that picks a duty vendor module by its provider."""

from typing import assert_never

import httpx2

from mor.config import Environment
from mor.duties import fake_client
from mor.duties.port import AskResult, DutyProvider, DutyVendorAccount, RateQuestion


class FakeInProd(Exception):
    """A fake provider reached in prod: the fakes never run there
    (plan-architecture.md §6), so an account row is wrong."""


async def ask_rates(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: DutyVendorAccount,
    question: RateQuestion,
) -> AskResult:
    match account.provider:
        case DutyProvider.FAKE:
            if environment == Environment.PROD:
                raise FakeInProd(f"duty vendor {account.vendor_name} is the fake")
            return await fake_client.ask_rates(http, account, question)
        case _:
            assert_never(account.provider)

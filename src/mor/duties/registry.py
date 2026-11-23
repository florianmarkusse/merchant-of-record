"""The one place that picks a duty vendor module by its provider. Every call
first checks that this environment may use the account's kind
(`mor.provider_accounts`)."""

from typing import assert_never

import httpx2

from mor.config import Environment
from mor.duties import fake_client
from mor.duties.port import AskResult, DutyProvider, DutyVendorAccount, RateQuestion
from mor.provider_accounts import require_allowed


async def ask_rates(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: DutyVendorAccount,
    question: RateQuestion,
) -> AskResult:
    require_allowed(
        environment, account.kind, f"duty vendor account {account.vendor_name}"
    )
    match account.provider:
        case DutyProvider.FAKE:
            return await fake_client.ask_rates(http, account, question)
        case _:
            assert_never(account.provider)

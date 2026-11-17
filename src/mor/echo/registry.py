"""The one place that picks an echo provider module by name.

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
    FindResult,
    SendRequest,
    SendResult,
)


class FakeInProd(Exception):
    """A fake provider reached in prod: the fakes never run there
    (plan-architecture.md §6), so an account row is wrong."""


def _refuse_fake_in_prod(environment: Environment, account: EchoAccount) -> None:
    if environment == Environment.PROD:
        raise FakeInProd(f"echo account {account.id} is {account.provider}")


async def send(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: EchoAccount,
    request: SendRequest,
) -> SendResult:
    match account.provider:
        case (
            EchoProvider.FAKE_KEYED
            | EchoProvider.FAKE_CANCELLABLE
            | EchoProvider.FAKE_PLAIN
        ):
            _refuse_fake_in_prod(environment, account)
            return await fake_client.send(http, account, request)
        case _:
            assert_never(account.provider)


async def find_by_reference(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: EchoAccount,
    reference: str,
) -> FindResult:
    match account.provider:
        case (
            EchoProvider.FAKE_KEYED
            | EchoProvider.FAKE_CANCELLABLE
            | EchoProvider.FAKE_PLAIN
        ):
            _refuse_fake_in_prod(environment, account)
            return await fake_client.find_by_reference(http, account, reference)
        case _:
            assert_never(account.provider)


async def cancel(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: EchoAccount,
    echo_id: str,
) -> CancelResult:
    match account.provider:
        case (
            EchoProvider.FAKE_KEYED
            | EchoProvider.FAKE_CANCELLABLE
            | EchoProvider.FAKE_PLAIN
        ):
            _refuse_fake_in_prod(environment, account)
            return await fake_client.cancel(http, account, echo_id)
        case _:
            assert_never(account.provider)

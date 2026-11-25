"""The one place that picks a bank module by its provider
(plan-architecture §6). Every call first checks that this environment may
use the account's kind (`mor.provider_accounts`)."""

import httpx2

from mor.bank import fake_client
from mor.bank.port import BankAccount, BankProvider, BankTransaction, PageResult
from mor.config import Environment
from mor.provider_accounts import require_allowed
from mor.vendor_failures import UndecodableBody


async def list_transactions(
    http: httpx2.AsyncClient,
    environment: Environment,
    account: BankAccount,
    page_key: str | None,
) -> PageResult:
    require_allowed(environment, account.kind, f"bank account {account.name}")
    match account.provider:
        case BankProvider.FAKE:
            return await fake_client.list_transactions(http, account, page_key)


def decode_transactions(
    account: BankAccount, body: bytes
) -> tuple[BankTransaction, ...] | UndecodableBody:
    match account.provider:
        case BankProvider.FAKE:
            return fake_client.decode_transactions(account, body)

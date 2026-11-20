"""Which provider accounts an environment may use (plan-architecture §6,
decided 19-Nov-2026): fakes outside prod, a vendor's sandbox on the test
setup, live accounts in prod only. Every registry checks it before a call,
so a wrong account row can never reach a real vendor, nor a live key run
outside prod."""

from enum import StrEnum
from typing import assert_never

from mor.config import Environment


class ProviderAccountKind(StrEnum):
    """Matches the `provider_account_kind` enum type."""

    FAKE = "fake"
    SANDBOX = "sandbox"
    LIVE = "live"


class AccountNotAllowed(Exception):
    """An account of a kind this environment may not use: an account row or
    routing row is wrong. Raised, because it is a bug, never a vendor's
    answer."""


def allowed(environment: Environment, kind: ProviderAccountKind) -> bool:
    match environment:
        case Environment.DEV:
            return kind == ProviderAccountKind.FAKE
        case Environment.TEST:
            return kind in (ProviderAccountKind.FAKE, ProviderAccountKind.SANDBOX)
        case Environment.PROD:
            return kind == ProviderAccountKind.LIVE
        case _:
            assert_never(environment)


def require_allowed(
    environment: Environment, kind: ProviderAccountKind, account: str
) -> None:
    if not allowed(environment, kind):
        raise AccountNotAllowed(f"{account} is {kind} in {environment}")

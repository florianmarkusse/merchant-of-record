"""Merchants and their API keys (docs/plan-quote.md §5, 4d-1)."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


DEFAULT_DAILY_VENDOR_ASKS = 100  # decided 19-Nov-2026


class KybState(StrEnum):
    """Ours, never the merchant's: only `approved` may take a new payment
    (docs/plan-payments.md §6.1). Matches the `merchant_kyb_state` enum
    type."""

    PENDING = "pending"
    APPROVED = "approved"
    SUSPENDED = "suspended"


@dataclass(frozen=True)
class Merchant:
    id: uuid.UUID
    legal_name: str
    display_name: str  # what buyers see
    country: str
    daily_vendor_asks: int  # new vendor lookups its estimates may start a day
    created_at: datetime
    kyb_state: KybState
    support_email: str | None  # shown to its buyers; set by the merchant


class KeyEnv(StrEnum):
    """Where a key works, as its text shows: `sk_test_…`. Matches the
    `api_key_env` enum type."""

    DEV = "dev"
    TEST = "test"
    LIVE = "live"


class KeyState(StrEnum):
    """Matches the `api_key_state` enum type."""

    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True)
class NewKey:
    """A key as created: `text` is shown once and never stored."""

    id: str
    text: str


@dataclass(frozen=True)
class Authenticated:
    key_id: str
    merchant: Merchant

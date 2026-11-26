"""Carrier accounts: one per carrier, its capabilities as data
(docs/plan-shipments.md §2). Feature code reads the account's values,
never its provider. Never commits."""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from psycopg.rows import class_row

from mor.carriers.records import (
    CarrierAccount,
    CarrierProvider,
    CarrierTrackingMode,
    PriceExpiry,
)
from mor.db.connect import Connection
from mor.ids import CarrierAccountId, CarrierCode
from mor.messaging.guard import DuplicateGuard
from mor.provider_accounts import ProviderAccountKind


@dataclass(frozen=True, kw_only=True)
class _AccountRow:
    id: CarrierAccountId
    carrier: CarrierCode
    provider: CarrierProvider
    kind: ProviderAccountKind
    base_url: str
    timeout_ms: int
    price_valid_for_s: int
    price_expiry: PriceExpiry
    book_guard: DuplicateGuard
    tracking_mode: CarrierTrackingMode


def _account(row: _AccountRow) -> CarrierAccount:
    return CarrierAccount(
        id=row.id,
        carrier=row.carrier,
        provider=row.provider,
        kind=row.kind,
        base_url=row.base_url,
        timeout=timedelta(milliseconds=row.timeout_ms),
        price_valid_for=timedelta(seconds=row.price_valid_for_s),
        price_expiry=row.price_expiry,
        book_guard=row.book_guard,
        tracking_mode=row.tracking_mode,
    )


async def create_carrier_account(
    conn: Connection,
    carrier: CarrierCode,
    provider: CarrierProvider,
    kind: ProviderAccountKind,
    base_url: str,
    timeout: timedelta,
    price_valid_for: timedelta,
    price_expiry: PriceExpiry,
    book_guard: DuplicateGuard = DuplicateGuard.VENDOR_CONSTRAINT,
    tracking_mode: CarrierTrackingMode = CarrierTrackingMode.POLL,
    poll_interval: timedelta = timedelta(minutes=5),
    webhook_secret: str = "",
) -> CarrierAccountId:
    """A fake account's `base_url` names the fake's flavour, which must
    match `book_guard` (`mor.fakes.carrier`)."""
    account_id = CarrierAccountId(uuid.uuid7())
    await conn.execute(
        """
        INSERT INTO carrier_account
          (id, carrier, provider, kind, base_url, timeout_ms, price_valid_for_s,
           price_expiry, book_guard, tracking_mode, poll_interval_s,
           webhook_secret)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            account_id,
            carrier,
            provider,
            kind,
            base_url,
            int(timeout.total_seconds() * 1000),
            int(price_valid_for.total_seconds()),
            price_expiry,
            book_guard,
            tracking_mode,
            int(poll_interval.total_seconds()),
            webhook_secret,
        ),
    )
    return account_id


async def carrier_account(
    conn: Connection, carrier: CarrierCode
) -> CarrierAccount | None:
    async with conn.cursor(row_factory=class_row(_AccountRow)) as cur:
        await cur.execute(
            """
            SELECT id, carrier, provider, kind, base_url, timeout_ms,
                   price_valid_for_s, price_expiry, book_guard, tracking_mode
            FROM carrier_account WHERE carrier = %s
            """,
            (carrier,),
        )
        row = await cur.fetchone()
    return None if row is None else _account(row)


async def read_carrier_account(
    conn: Connection, account_id: CarrierAccountId
) -> CarrierAccount | None:
    async with conn.cursor(row_factory=class_row(_AccountRow)) as cur:
        await cur.execute(
            """
            SELECT id, carrier, provider, kind, base_url, timeout_ms,
                   price_valid_for_s, price_expiry, book_guard, tracking_mode
            FROM carrier_account WHERE id = %s
            """,
            (account_id,),
        )
        row = await cur.fetchone()
    return None if row is None else _account(row)

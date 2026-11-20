"""PSP accounts and the routing rows that pick one for a payment
(docs/plan-payments.md §3). Feature code never names a provider: which
account takes a payment is data."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from psycopg.rows import class_row

from mor.audit import Actor
from mor.db.connect import Connection
from mor.messaging.guard import DuplicateGuard
from mor.provider_accounts import ProviderAccountKind
from mor.psp.port import (
    CaptureMode,
    PaymentMethod,
    PspAccount,
    PspProvider,
    SettlementMode,
    StatusMode,
)


@dataclass(frozen=True)
class _AccountRow:
    id: uuid.UUID
    name: str
    provider: str
    kind: str
    base_url: str
    timeout_ms: int
    status_mode: str
    capture_mode: str
    create_guard: str
    capture_guard: str
    methods: list[str]
    poll_interval_s: int
    webhook_secret: str
    settlement_mode: str
    settlement_interval_s: int


def _account(row: _AccountRow) -> PspAccount:
    methods: list[PaymentMethod] = []
    for method in row.methods:
        methods.append(PaymentMethod(method))
    return PspAccount(
        row.id,
        row.name,
        PspProvider(row.provider),
        ProviderAccountKind(row.kind),
        row.base_url,
        timedelta(milliseconds=row.timeout_ms),
        StatusMode(row.status_mode),
        CaptureMode(row.capture_mode),
        DuplicateGuard(row.create_guard),
        DuplicateGuard(row.capture_guard),
        tuple(methods),
        timedelta(seconds=row.poll_interval_s),
        row.webhook_secret,
        SettlementMode(row.settlement_mode),
        timedelta(seconds=row.settlement_interval_s),
    )


async def create_psp_account(
    conn: Connection,
    name: str,
    provider: PspProvider,
    kind: ProviderAccountKind,
    base_url: str,
    timeout: timedelta,
    status_mode: StatusMode,
    capture_mode: CaptureMode,
    guard: DuplicateGuard,
    methods: tuple[PaymentMethod, ...],
    webhook_secret: str,
    settlement_mode: SettlementMode,
    now: datetime,
    poll_interval: timedelta = timedelta(seconds=30),
    settlement_interval: timedelta = timedelta(hours=1),
) -> PspAccount:
    """One guard for creating and capturing: the fake keeps keys for both or
    for neither. A real PSP's row can set them apart. Its settlement is read
    once `mor.settlement.fetch.start_settlement_reading` starts it, as the
    seed does. Never commits."""
    account_id = uuid.uuid7()
    method_values: list[str] = []
    for method in methods:
        method_values.append(method.value)
    await conn.execute(
        """
        INSERT INTO psp_account
          (id, name, provider, kind, base_url, timeout_ms, status_mode,
           capture_mode, create_guard, capture_guard, methods, poll_interval_s,
           webhook_secret, settlement_mode, settlement_interval_s, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::payment_method[], %s,
                %s, %s, %s, %s)
        """,
        (
            account_id,
            name,
            provider.value,
            kind.value,
            base_url,
            int(timeout.total_seconds() * 1000),
            status_mode.value,
            capture_mode.value,
            guard.value,
            guard.value,
            method_values,
            int(poll_interval.total_seconds()),
            webhook_secret,
            settlement_mode.value,
            int(settlement_interval.total_seconds()),
            now,
        ),
    )
    return PspAccount(
        account_id,
        name,
        provider,
        kind,
        base_url,
        timeout,
        status_mode,
        capture_mode,
        guard,
        guard,
        methods,
        poll_interval,
        webhook_secret,
        settlement_mode,
        settlement_interval,
    )


async def read_psp_account(
    conn: Connection, account_id: uuid.UUID
) -> PspAccount | None:
    async with conn.cursor(row_factory=class_row(_AccountRow)) as cur:
        await cur.execute(
            """
            SELECT id, name, provider::text, kind::text, base_url, timeout_ms,
                   status_mode::text, capture_mode::text, create_guard::text,
                   capture_guard::text, methods::text[], poll_interval_s,
                   webhook_secret, settlement_mode::text, settlement_interval_s
            FROM psp_account WHERE id = %s
            """,
            (account_id,),
        )
        row = await cur.fetchone()
    return None if row is None else _account(row)


async def psp_account_by_name(conn: Connection, name: str) -> PspAccount | None:
    async with conn.cursor(row_factory=class_row(_AccountRow)) as cur:
        await cur.execute(
            """
            SELECT id, name, provider::text, kind::text, base_url, timeout_ms,
                   status_mode::text, capture_mode::text, create_guard::text,
                   capture_guard::text, methods::text[], poll_interval_s,
                   webhook_secret, settlement_mode::text, settlement_interval_s
            FROM psp_account WHERE name = %s
            """,
            (name,),
        )
        row = await cur.fetchone()
    return None if row is None else _account(row)


async def add_route(
    conn: Connection,
    account_id: uuid.UUID,
    merchant_id: uuid.UUID | None,
    buyer_country: str | None,
    effective_from: datetime,
    actor: Actor,
    now: datetime,
) -> uuid.UUID:
    """A new routing row; rows are never changed. Never commits."""
    route_id = uuid.uuid7()
    await conn.execute(
        """
        INSERT INTO psp_route
          (id, merchant_id, buyer_country, psp_account_id, effective_from,
           created_at, actor_kind, actor_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            route_id,
            merchant_id,
            buyer_country,
            account_id,
            effective_from,
            now,
            actor.kind.value,
            actor.id,
        ),
    )
    return route_id


@dataclass(frozen=True)
class Routed:
    route_id: uuid.UUID
    account: PspAccount


async def route_for(
    conn: Connection, merchant_id: uuid.UUID, buyer_country: str, now: datetime
) -> Routed | None:
    """The account for a payment: among rows in force that match, the most
    specific (merchant and country, merchant, country, neither), then the
    newest."""
    cur = await conn.execute(
        """
        SELECT id, psp_account_id FROM psp_route
        WHERE effective_from <= %(now)s
          AND (merchant_id IS NULL OR merchant_id = %(merchant)s)
          AND (buyer_country IS NULL OR buyer_country = %(country)s)
        ORDER BY (merchant_id IS NOT NULL) DESC, (buyer_country IS NOT NULL) DESC,
                 effective_from DESC, id DESC
        LIMIT 1
        """,
        {"now": now, "merchant": merchant_id, "country": buyer_country},
    )
    row = await cur.fetchone()
    if row is None:
        return None
    route_id, account_id = row
    account = await read_psp_account(conn, account_id)
    assert account is not None
    return Routed(route_id, account)

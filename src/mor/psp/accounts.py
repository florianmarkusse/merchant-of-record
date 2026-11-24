"""PSP accounts and the routing rows that pick one for a payment
(docs/plan-payments.md §3). Feature code never names a provider: which
account takes a payment is data."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from psycopg.rows import class_row

from mor.audit import Actor
from mor.db.connect import Connection
from mor.ids import MerchantId, PspAccountId, PspRouteId
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


@dataclass(frozen=True, kw_only=True)
class _AccountRow:
    id: PspAccountId
    name: str
    provider: PspProvider
    kind: ProviderAccountKind
    base_url: str
    timeout_ms: int
    status_mode: StatusMode
    capture_mode: CaptureMode
    create_guard: DuplicateGuard
    capture_guard: DuplicateGuard
    methods: list[PaymentMethod]
    poll_interval_s: int
    webhook_secret: str
    settlement_mode: SettlementMode
    settlement_interval_s: int


def _account(row: _AccountRow) -> PspAccount:
    return PspAccount(
        id=row.id,
        name=row.name,
        provider=row.provider,
        kind=row.kind,
        base_url=row.base_url,
        timeout=timedelta(milliseconds=row.timeout_ms),
        status_mode=row.status_mode,
        capture_mode=row.capture_mode,
        create_guard=row.create_guard,
        capture_guard=row.capture_guard,
        methods=tuple(row.methods),
        poll_interval=timedelta(seconds=row.poll_interval_s),
        webhook_secret=row.webhook_secret,
        settlement_mode=row.settlement_mode,
        settlement_interval=timedelta(seconds=row.settlement_interval_s),
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
    account_id = PspAccountId(uuid.uuid7())
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
        id=account_id,
        name=name,
        provider=provider,
        kind=kind,
        base_url=base_url,
        timeout=timeout,
        status_mode=status_mode,
        capture_mode=capture_mode,
        create_guard=guard,
        capture_guard=guard,
        methods=methods,
        poll_interval=poll_interval,
        webhook_secret=webhook_secret,
        settlement_mode=settlement_mode,
        settlement_interval=settlement_interval,
    )


async def read_psp_account(
    conn: Connection, account_id: PspAccountId
) -> PspAccount | None:
    async with conn.cursor(row_factory=class_row(_AccountRow)) as cur:
        await cur.execute(
            """
            SELECT id, name, provider, kind, base_url, timeout_ms,
                   status_mode, capture_mode, create_guard,
                   capture_guard, methods, poll_interval_s,
                   webhook_secret, settlement_mode, settlement_interval_s
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
            SELECT id, name, provider, kind, base_url, timeout_ms,
                   status_mode, capture_mode, create_guard,
                   capture_guard, methods, poll_interval_s,
                   webhook_secret, settlement_mode, settlement_interval_s
            FROM psp_account WHERE name = %s
            """,
            (name,),
        )
        row = await cur.fetchone()
    return None if row is None else _account(row)


async def add_route(
    conn: Connection,
    account_id: PspAccountId,
    merchant_id: MerchantId | None,
    buyer_country: str | None,
    effective_from: datetime,
    actor: Actor,
    now: datetime,
) -> PspRouteId:
    """A new routing row; rows are never changed. Never commits."""
    route_id = PspRouteId(uuid.uuid7())
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
    route_id: PspRouteId
    account: PspAccount


async def route_for(
    conn: Connection, merchant_id: MerchantId, buyer_country: str, now: datetime
) -> Routed | None:
    """The account for a payment: among rows in force that match, the most
    specific (merchant and country, merchant, country, neither), then the
    newest."""
    async with conn.cursor(row_factory=class_row(_RouteRow)) as cur:
        await cur.execute(
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
        route = await cur.fetchone()
    if route is None:
        return None
    account = await read_psp_account(conn, route.psp_account_id)
    assert account is not None
    return Routed(route.id, account)


@dataclass(frozen=True)
class _RouteRow:
    id: PspRouteId
    psp_account_id: PspAccountId

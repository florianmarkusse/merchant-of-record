"""Seed the test setup with synthetic accounts (docs/plan-test-setup.md §3).

    ENVIRONMENT=test DATABASE_URL=postgresql:///mor_sandbox \\
        uv run python -m mor.seed

One echo account per provider and event mode, each its own vendor-side
account at the fake under SELF_URL. The server must be running: push
accounts subscribe their webhooks at the fake over HTTP. Refused in prod.
Running it again creates nothing and subscribes again, which the fake
treats as an update.

Every duty vendor the market data in force names (`zonos`) gets an account
pointing at the fake duty vendor, which is given a few rates
(FAKE_DUTY_RATES), one of them differing from our own rows so the staff
page shows a disagreement.

Every carrier a live route names (`carrier_api` in shipping_routes.csv)
gets an account pointing at the fake carrier, its prices used for
PRICE_VALID_FOR under the `carrier_first` rule (decided 19-Nov-2026). The
fake answers its own default prices.

One fake PSP account per wiring (how news arrives, how it captures, its
duplicate guard), each set up at the fake PSP as a merchant would on its
dashboard, and a default route to the push / sale / keyed one
(docs/plan-payments.md §2). Another wiring is tried by adding a route row.
Each reads its settlement, keyed accounts by report file and the others
through the API (§7.1).

One fake bank account per currency, each read for its transactions, and
every fake PSP account paying out each currency into it, on both sides
(docs/plan-payments.md §7, the bank statement).

A synthetic merchant, "Sandbox Shop", with an API key for this
environment: the key is printed when it is created, and only then
(docs/plan-quote.md §5, 4d-1); a made-up ship-from address in VN and
our codes for its SKUs SANDBOX-TEE and SANDBOX-SHIRT
(docs/plan-shipments.md §4).

Transitional with the echo flow: the seed grows a merchant per mode
combination as the real ports arrive (plan-architecture.md §6).
"""

import asyncio
import os
import secrets
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx2

from mor.audit import Actor, ActorKind
from mor.bank.accounts import (
    bank_account_by_name,
    create_bank_account,
    set_payout_destination,
)
from mor.bank.fetch import start_reading
from mor.bank.port import BankAccount, BankProvider
from mor.carriers.accounts import carrier_account, create_carrier_account
from mor.carriers.records import CarrierProvider, CarrierTrackingMode, PriceExpiry
from mor.checkout.addresses import Address
from mor.config import Environment, Settings, load_settings
from mor.db.connect import Connection, connect
from mor.duties.lookups import create_vendor_account, vendor_account
from mor.duties.port import DutyProvider
from mor.echo.flow import create_echo_account
from mor.echo.port import EchoProvider, EventMode
from mor.echo.reports import start_report_schedule
from mor.ids import CarrierCode, CountryCode, HsCode, MerchantId, MerchantSku
from mor.markets.store import latest_load, read_market
from mor.merchants.classifications import classifications_in_force, classify
from mor.merchants.records import DEFAULT_DAILY_VENDOR_ASKS, KybState, NewKey
from mor.merchants.ship_from import set_ship_from, ship_from_in_force
from mor.merchants.store import (
    create_api_key,
    create_merchant,
    key_env,
    merchant_has_key,
    merchants,
    set_kyb_state,
)
from mor.messaging.guard import DuplicateGuard
from mor.money.currency import Currency
from mor.provider_accounts import ProviderAccountKind
from mor.psp.accounts import add_route, create_psp_account, psp_account_by_name
from mor.psp.port import (
    CaptureMode,
    PaymentMethod,
    PspAccount,
    PspProvider,
    SettlementMode,
    StatusMode,
)
from mor.settlement.fetch import start_settlement_reading


SEEDER = Actor(ActorKind.SYSTEM, "seed")
TIMEOUT = timedelta(seconds=5)
POLL_INTERVAL = timedelta(minutes=1)
PRICE_VALID_FOR = timedelta(hours=24)
# One seed at a time: a second waits for the first to commit, then finds
# its accounts. Released with the transaction.
SEED_LOCK = "seed:echo_accounts"

# (destination, HS prefix, duty, tax) as the fake writes them. TH 6109
# duty differs from our hs_rates.csv on purpose.
FAKE_DUTY_RATES = (
    ("TH", "6109", "25%", "7%"),
    ("SG", "61", "0%", "9%"),
    ("SG", "62", "0%", "9%"),
)


@dataclass(frozen=True)
class SeedInProd:
    """The seed was started against prod: an operator mistake."""


@dataclass(frozen=True)
class FakeUnreachable:
    """The fake did not take a subscription; most likely the server is not
    running at SELF_URL."""

    detail: str


@dataclass(frozen=True, kw_only=True)
class SeededEchoAccount:
    account_id: uuid.UUID
    base_url: str
    event_mode: EventMode
    webhook_secret: str
    created: bool


def _flavour(provider: EchoProvider) -> str:
    match provider:
        case EchoProvider.FAKE_KEYED:
            return "keyed"
        case EchoProvider.FAKE_CANCELLABLE:
            return "cancellable"
        case EchoProvider.FAKE_PLAIN:
            return "plain"


def base_url(self_url: str, provider: EchoProvider, mode: EventMode) -> str:
    return f"{self_url}/_fake/echo/{_flavour(provider)}/sandbox-{mode.value}"


async def _seed_account(
    conn: Connection, url: str, provider: EchoProvider, mode: EventMode, now: datetime
) -> SeededEchoAccount:
    cur = await conn.execute(
        "SELECT id, webhook_secret FROM echo_account WHERE base_url = %s", (url,)
    )
    row = await cur.fetchone()
    if row is not None:
        return SeededEchoAccount(
            account_id=row[0],
            base_url=url,
            event_mode=mode,
            webhook_secret=row[1],
            created=False,
        )
    secret = secrets.token_hex(16)
    account_id = await create_echo_account(
        conn,
        provider,
        url,
        TIMEOUT,
        event_mode=mode,
        webhook_secret=secret,
        poll_interval=POLL_INTERVAL,
    )
    if mode == EventMode.FILE:
        await start_report_schedule(conn, account_id, SEEDER, now)
    return SeededEchoAccount(
        account_id=account_id,
        base_url=url,
        event_mode=mode,
        webhook_secret=secret,
        created=True,
    )


async def seed_accounts(
    conn: Connection, settings: Settings, now: datetime
) -> list[SeededEchoAccount] | SeedInProd:
    """Our side of the seed, in the caller's transaction. Never commits."""
    if settings.environment == Environment.PROD:
        return SeedInProd()
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (SEED_LOCK,)
    )
    seeded: list[SeededEchoAccount] = []
    for provider in EchoProvider:
        for mode in EventMode:
            url = base_url(settings.self_url, provider, mode)
            seeded.append(await _seed_account(conn, url, provider, mode, now))
    return seeded


async def seed_duty_vendors(
    conn: Connection, settings: Settings
) -> list[str] | SeedInProd:
    """An account at the fake for every vendor the market data in force
    names and this database lacks; the names created. Never commits."""
    if settings.environment == Environment.PROD:
        return SeedInProd()
    load = await latest_load(conn)
    if load is None:
        return []
    market = await read_market(conn, load.id)
    created: list[str] = []
    for source in market.duty_sources:
        name = source.vendor
        if name is None or name in created:
            continue
        if await vendor_account(conn, name) is not None:
            continue
        url = f"{settings.self_url}/_fake/duty/sandbox-{name}"
        await create_vendor_account(
            conn, name, DutyProvider.FAKE, ProviderAccountKind.FAKE, url, TIMEOUT
        )
        created.append(name)
    return created


# Every booking guard and tracking mode the fake carrier offers, spread
# over the seeded carriers in turn (docs/plan-shipments.md §2), so the test
# setup exercises each.
CARRIER_WIRINGS = (
    (DuplicateGuard.VENDOR_CONSTRAINT, CarrierTrackingMode.PUSH_FAT),
    (DuplicateGuard.COMPENSABLE, CarrierTrackingMode.POLL),
    (DuplicateGuard.NONE, CarrierTrackingMode.PUSH_THIN),
    (DuplicateGuard.VENDOR_CONSTRAINT, CarrierTrackingMode.POLL),
    (DuplicateGuard.COMPENSABLE, CarrierTrackingMode.PUSH_THIN),
    (DuplicateGuard.NONE, CarrierTrackingMode.PUSH_FAT),
    (DuplicateGuard.VENDOR_CONSTRAINT, CarrierTrackingMode.PUSH_THIN),
    (DuplicateGuard.COMPENSABLE, CarrierTrackingMode.PUSH_FAT),
    (DuplicateGuard.NONE, CarrierTrackingMode.POLL),
)
CARRIER_WEBHOOK_SECRET = "whsec-carrier-sandbox"


def carrier_flavour(guard: DuplicateGuard) -> str:
    """The fake carrier's flavour that keeps the guard's promise
    (`mor.fakes.carrier`)."""
    match guard:
        case DuplicateGuard.VENDOR_CONSTRAINT:
            return "keyed"
        case DuplicateGuard.COMPENSABLE:
            return "cancellable"
        case DuplicateGuard.NONE:
            return "plain"


@dataclass(frozen=True, kw_only=True)
class SeededCarrier:
    carrier: CarrierCode
    book_guard: DuplicateGuard
    tracking_mode: CarrierTrackingMode
    created: bool


async def seed_carriers(
    conn: Connection, settings: Settings
) -> list[SeededCarrier] | SeedInProd:
    """An account at the fake carrier for every carrier a route in the
    market data in force names, made if missing; the wirings go round
    `CARRIER_WIRINGS` in carrier order. Never commits."""
    if settings.environment == Environment.PROD:
        return SeedInProd()
    load = await latest_load(conn)
    if load is None:
        return []
    market = await read_market(conn, load.id)
    routed: set[CarrierCode] = set()
    for route in market.routes:
        for service in market.services:
            if service.code == route.service:
                routed.add(service.carrier)
    seeded: list[SeededCarrier] = []
    for number, carrier in enumerate(sorted(routed)):
        existing = await carrier_account(conn, carrier)
        if existing is not None:
            seeded.append(
                SeededCarrier(
                    carrier=carrier,
                    book_guard=existing.book_guard,
                    tracking_mode=existing.tracking_mode,
                    created=False,
                )
            )
            continue
        guard, mode = CARRIER_WIRINGS[number % len(CARRIER_WIRINGS)]
        await create_carrier_account(
            conn,
            carrier,
            CarrierProvider.FAKE,
            ProviderAccountKind.FAKE,
            f"{settings.self_url}/_fake/carrier/{carrier_flavour(guard)}"
            f"/sandbox-{carrier}",
            TIMEOUT,
            PRICE_VALID_FOR,
            PriceExpiry.CARRIER_FIRST,
            book_guard=guard,
            tracking_mode=mode,
            poll_interval=POLL_INTERVAL,
            webhook_secret=CARRIER_WEBHOOK_SECRET,
        )
        seeded.append(
            SeededCarrier(
                carrier=carrier, book_guard=guard, tracking_mode=mode, created=True
            )
        )
    return seeded


PSP_GUARDS = (DuplicateGuard.VENDOR_CONSTRAINT, DuplicateGuard.NONE)
DEFAULT_PSP = "sandbox-push_fat-sale-vendor_constraint"


# Short on the test setup, so a settlement cut on the sandbox page shows up
# soon (docs/plan-payments.md §7).
SETTLEMENT_INTERVAL = timedelta(minutes=1)


def settlement_mode_for(guard: DuplicateGuard) -> SettlementMode:
    """Both settlement modes on the test setup without doubling the
    accounts: keyed accounts read reports, the others the API. The fuzzer
    draws the two apart."""
    if guard == DuplicateGuard.VENDOR_CONSTRAINT:
        return SettlementMode.FILE
    return SettlementMode.API


def psp_name(mode: StatusMode, capture: CaptureMode, guard: DuplicateGuard) -> str:
    return f"sandbox-{mode.value}-{capture.value}-{guard.value}"


async def seed_psp_accounts(
    conn: Connection, settings: Settings, now: datetime
) -> list[PspAccount] | SeedInProd:
    """Every wiring's account, made if missing, and the default route if
    there is none. Never commits."""
    if settings.environment == Environment.PROD:
        return SeedInProd()
    seeded: list[PspAccount] = []
    for mode in StatusMode:
        for capture in CaptureMode:
            for guard in PSP_GUARDS:
                name = psp_name(mode, capture, guard)
                account = await psp_account_by_name(conn, name)
                if account is None:
                    account = await create_psp_account(
                        conn,
                        name,
                        PspProvider.FAKE,
                        ProviderAccountKind.FAKE,
                        f"{settings.self_url}/_fake/psp/{name}",
                        TIMEOUT,
                        mode,
                        capture,
                        guard,
                        (PaymentMethod.CARD, PaymentMethod.QR),
                        secrets.token_hex(16),
                        settlement_mode_for(guard),
                        now,
                        POLL_INTERVAL,
                        SETTLEMENT_INTERVAL,
                    )
                # Every account reads its settlement; the cursor's insert
                # keeps a second seed run from starting a second reader.
                await start_settlement_reading(conn, account.id, SEEDER, now)
                seeded.append(account)
    cur = await conn.execute("SELECT 1 FROM psp_route LIMIT 1")
    if await cur.fetchone() is None:
        default = await psp_account_by_name(conn, DEFAULT_PSP)
        assert default is not None
        await add_route(conn, default.id, None, None, now, SEEDER, now)
    return seeded


def bank_name(currency: Currency) -> str:
    return f"sandbox-bank-{currency.value.lower()}"


async def seed_bank_accounts(
    conn: Connection, settings: Settings, psps: list[PspAccount], now: datetime
) -> list[BankAccount] | SeedInProd:
    """A bank account per currency, made if missing, read from now on, with
    every PSP account paying out that currency into it. Never commits."""
    if settings.environment == Environment.PROD:
        return SeedInProd()
    seeded: list[BankAccount] = []
    for currency in Currency:
        name = bank_name(currency)
        account = await bank_account_by_name(conn, name)
        if account is None:
            account = await create_bank_account(
                conn,
                name,
                BankProvider.FAKE,
                ProviderAccountKind.FAKE,
                f"{settings.self_url}/_fake/bank/{name}",
                f"FAKE-{currency.value}-0001",
                currency,
                now,
                TIMEOUT,
                SETTLEMENT_INTERVAL,
            )
        await start_reading(conn, account.id, SEEDER, now)
        for psp in psps:
            await set_payout_destination(conn, psp.id, account, now)
        seeded.append(account)
    return seeded


async def set_up_fake_bank(
    http: httpx2.AsyncClient, banks: list[BankAccount], psps: list[PspAccount]
) -> FakeUnreachable | None:
    """The bank side: each account opened, and each PSP account's payouts
    in its currency pointed at it, as on the PSP's dashboard."""
    for bank in banks:
        try:
            response = await http.post(
                f"{bank.base_url}/setup", json={"currency": bank.currency.value}
            )
            if response.status_code != 204:
                return FakeUnreachable(f"status {response.status_code}")
            for psp in psps:
                response = await http.post(
                    f"{bank.base_url}/destinations", json={"psp_account": psp.name}
                )
                if response.status_code != 204:
                    return FakeUnreachable(f"status {response.status_code}")
        except httpx2.TransportError as error:
            return FakeUnreachable(repr(error))
    return None


async def set_up_fake_psp(
    http: httpx2.AsyncClient, settings: Settings, accounts: list[PspAccount]
) -> FakeUnreachable | None:
    """The PSP side: each account as set on the PSP's dashboard."""
    for account in accounts:
        pushes = account.status_mode != StatusMode.POLL
        payload: str | None = None
        if account.status_mode == StatusMode.PUSH_FAT:
            payload = "full"
        elif account.status_mode == StatusMode.PUSH_THIN:
            payload = "thin"
        try:
            response = await http.post(
                f"{account.base_url}/setup",
                json={
                    "capture": account.capture_mode.value,
                    "keyed": account.create_guard == DuplicateGuard.VENDOR_CONSTRAINT,
                    "webhook_url": (
                        f"{settings.self_url}/webhooks/psp/{account.id}"
                        if pushes
                        else None
                    ),
                    "webhook_secret": account.webhook_secret if pushes else None,
                    "webhook_payload": payload,
                },
            )
        except httpx2.TransportError as error:
            return FakeUnreachable(repr(error))
        if response.status_code != 204:
            return FakeUnreachable(f"status {response.status_code}")
    return None


async def set_fake_duty_rates(
    http: httpx2.AsyncClient, settings: Settings
) -> FakeUnreachable | None:
    """The vendor side: the fake's rates, as the vendor holds them."""
    for destination, hs_prefix, duty, tax in FAKE_DUTY_RATES:
        try:
            response = await http.post(
                f"{settings.self_url}/_fake/duty/rates",
                json={
                    "destination": destination,
                    "hs_prefix": hs_prefix,
                    "duty": duty,
                    "tax": tax,
                },
            )
        except httpx2.TransportError as error:
            return FakeUnreachable(repr(error))
        if response.status_code != 204:
            return FakeUnreachable(f"status {response.status_code}")
    return None


async def subscribe_webhooks(
    http: httpx2.AsyncClient, settings: Settings, seeded: list[SeededEchoAccount]
) -> FakeUnreachable | None:
    """The vendor side: register each push account's webhook at the fake,
    as one would on a vendor's dashboard."""
    for account in seeded:
        match account.event_mode:
            case EventMode.PUSH:
                payload = "full"
            case EventMode.PUSH_THIN:
                payload = "thin"
            case EventMode.POLL | EventMode.FILE | EventMode.NONE:
                continue
        try:
            response = await http.post(
                f"{account.base_url}/webhooks",
                json={
                    "url": f"{settings.self_url}/webhooks/echo/{account.account_id}",
                    "secret": account.webhook_secret,
                    "payload": payload,
                },
            )
        except httpx2.TransportError as error:
            return FakeUnreachable(repr(error))
        if response.status_code != 204:
            return FakeUnreachable(f"status {response.status_code}")
    return None


async def _main() -> int:
    settings = load_settings(os.environ)
    async with await connect(settings.database_url) as conn:
        seeded = await seed_accounts(conn, settings, datetime.now(UTC))
        if isinstance(seeded, SeedInProd):
            print("refused: the seed never runs in prod")
            return 1
        vendors = await seed_duty_vendors(conn, settings)
        assert not isinstance(vendors, SeedInProd)
        carriers = await seed_carriers(conn, settings)
        assert not isinstance(carriers, SeedInProd)
        key = await seed_merchant(conn, settings, datetime.now(UTC))
        assert not isinstance(key, SeedInProd)
        psps = await seed_psp_accounts(conn, settings, datetime.now(UTC))
        assert not isinstance(psps, SeedInProd)
        banks = await seed_bank_accounts(conn, settings, psps, datetime.now(UTC))
        assert not isinstance(banks, SeedInProd)
        await conn.commit()
    async with httpx2.AsyncClient() as http:
        unreachable = await subscribe_webhooks(http, settings, seeded)
        if unreachable is None:
            unreachable = await set_fake_duty_rates(http, settings)
        if unreachable is None:
            unreachable = await set_up_fake_psp(http, settings, psps)
        if unreachable is None:
            unreachable = await set_up_fake_bank(http, banks, psps)
    if unreachable is not None:
        print(f"accounts seeded, webhooks not subscribed: {unreachable.detail}")
        print(f"is the server running at {settings.self_url}? Then seed again.")
        return 1
    created = 0
    for account in seeded:
        if account.created:
            created += 1
    print(f"{len(seeded)} echo accounts, {created} new; webhooks subscribed")
    print(f"duty vendor accounts created: {', '.join(vendors) or 'none'}")
    for seeded_carrier in carriers:
        made = "new" if seeded_carrier.created else "kept"
        print(
            f"carrier {seeded_carrier.carrier}: books under"
            f" {seeded_carrier.book_guard}, tracks by"
            f" {seeded_carrier.tracking_mode} ({made})"
        )
    print(f"{len(psps)} fake PSP accounts set up; default route: {DEFAULT_PSP}")
    print(f"{len(banks)} fake bank accounts, one per currency")
    if key is not None:
        print(f"Sandbox Shop API key (shown once): {key.text}")
    return 0


SANDBOX_MERCHANT = ("Sandbox Shop Co., Ltd.", "Sandbox Shop")
# Made up: where the Sandbox Shop ships from, and its SKUs as we classify
# them (docs/plan-shipments.md §4).
SANDBOX_SHIP_FROM = Address(
    name="Sandbox Shop Warehouse",
    email="warehouse@sandbox-shop.example",
    phone_dial_code="+84",
    phone_number="281234567",
    line1="1 Example Street",
    line2=None,
    city="Thu Duc",
    region="Ho Chi Minh City",
    postcode="700000",
    country=CountryCode("VN"),
)
SANDBOX_SKUS = (
    (MerchantSku("SANDBOX-TEE"), HsCode("610910")),  # cotton T-shirt, knitted
    (MerchantSku("SANDBOX-SHIRT"), HsCode("620520")),  # men's cotton shirt, woven
)


async def seed_merchant(
    conn: Connection, settings: Settings, now: datetime
) -> NewKey | None | SeedInProd:
    """The sandbox merchant if missing, and a key for it if it has none:
    the new key, to be shown once. Never commits."""
    if settings.environment == Environment.PROD:
        return SeedInProd()
    legal_name, display_name = SANDBOX_MERCHANT
    merchant_id: MerchantId | None = None
    approved = False
    for seller in await merchants(conn):
        if seller.legal_name == legal_name:
            merchant_id = seller.id
            approved = seller.kyb_state == KybState.APPROVED
    if merchant_id is None:
        created = await create_merchant(
            conn,
            legal_name,
            display_name,
            CountryCode("VN"),
            DEFAULT_DAILY_VENDOR_ASKS,
            SEEDER,
            now,
        )
        merchant_id = created.id
    if not approved:
        # The sandbox's KYB is at once (docs/plan-payments.md §6).
        await set_kyb_state(conn, merchant_id, KybState.APPROVED, SEEDER, now)
    if await ship_from_in_force(conn, merchant_id) is None:
        await set_ship_from(conn, merchant_id, SANDBOX_SHIP_FROM, SEEDER, now)
    classified = await classifications_in_force(conn, merchant_id)
    for sku, hs_code in SANDBOX_SKUS:
        if sku not in classified:
            await classify(conn, merchant_id, sku, hs_code, SEEDER, now)
    if await merchant_has_key(conn, merchant_id):
        return None
    return await create_api_key(conn, merchant_id, key_env(settings.environment), now)


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))

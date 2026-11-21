"""Database enums read straight into our StrEnums.

Every connection registers psycopg's enum loader for each pair below, so a
column of that type comes back as the StrEnum member, not as text, and a
row can be read into its domain record with `class_row` directly. A member
is sent as the database type, so a member of the wrong enum is refused by
PostgreSQL instead of stored as text.

psycopg matches members to labels by member name (`CARD`); ours match by
value (`card`), so each registration passes the mapping by value.

Not listed: a database enum the code reads as a `Literal` or never reads
alone, and `echo_job_state` and `probe_state`, whose StrEnums live in flow
modules that import the database themselves.

This module may not import `mor.db.connect`, which imports it: it names
the connection type itself.
"""

from enum import StrEnum

import psycopg
from psycopg.rows import TupleRow
from psycopg.types.enum import EnumInfo, register_enum

from mor.audit import ActorKind
from mor.checkout.records import AfterPayment, SessionState
from mor.couriers.records import (
    CourierProvider,
    PriceExpiry,
    PricePartKind,
    PriceState,
)
from mor.duties.port import DutyProvider
from mor.duties.records import LookupState
from mor.echo.events import VendorStatus
from mor.echo.port import EchoProvider, EventKind, EventMode
from mor.invoices.records import InvoiceSection
from mor.ledger.account import AccountKind
from mor.markets.records import (
    Band,
    Base,
    CardFuel,
    Charge,
    Collector,
    DutiesPaid,
    DutyMode,
    FeeComponent,
    FuelApplies,
    GroupKind,
    LimitBasis,
    MarketStatus,
    PreferenceCondition,
    PriceSource,
    ResearchStatus,
    RestrictionRule,
    ServiceTier,
    Standing,
    SurchargeBasis,
    SurchargeTrigger,
)
from mor.merchants.records import KeyEnv, KeyState, KybState
from mor.messaging.facts import AggregateType, MessageType
from mor.messaging.guard import DuplicateGuard
from mor.messaging.routing import ConsumerName
from mor.money.currency import Currency
from mor.money.rounding import RoundingMode
from mor.payments.transitions import AttemptState
from mor.provider_accounts import ProviderAccountKind
from mor.psp.port import (
    CaptureMode,
    InboundMode,
    LineType,
    PaymentMethod,
    PspProvider,
    SettlementMode,
    StatusMode,
)
from mor.quote.groups import PartKind


DATABASE_ENUMS: tuple[tuple[str, type[StrEnum]], ...] = (
    ("account_kind", AccountKind),
    ("actor_kind", ActorKind),
    ("after_payment", AfterPayment),
    ("aggregate_type", AggregateType),
    ("api_key_env", KeyEnv),
    ("api_key_state", KeyState),
    ("card_fuel", CardFuel),
    ("carrier_price_part", PricePartKind),
    ("checkout_session_state", SessionState),
    ("consumer_name", ConsumerName),
    ("courier_provider", CourierProvider),
    ("currency", Currency),
    ("duplicate_guard", DuplicateGuard),
    ("duties_paid", DutiesPaid),
    ("duty_lookup_state", LookupState),
    ("duty_mode", DutyMode),
    ("duty_provider", DutyProvider),
    ("echo_event_kind", EventKind),
    ("echo_event_mode", EventMode),
    ("echo_provider", EchoProvider),
    ("echo_vendor_status", VendorStatus),
    ("fee_component", FeeComponent),
    ("fuel_applies", FuelApplies),
    ("invoice_section", InvoiceSection),
    ("landed_band", Band),
    ("landed_base", Base),
    ("landed_charge", Charge),
    ("landed_collector", Collector),
    ("landed_limit_basis", LimitBasis),
    ("market_group_kind", GroupKind),
    ("market_status", MarketStatus),
    ("merchant_kyb_state", KybState),
    ("message_type", MessageType),
    ("payment_attempt_state", AttemptState),
    ("payment_method", PaymentMethod),
    ("preference_condition", PreferenceCondition),
    ("price_expiry", PriceExpiry),
    ("price_source", PriceSource),
    ("provider_account_kind", ProviderAccountKind),
    ("psp_capture_mode", CaptureMode),
    ("psp_inbound_mode", InboundMode),
    ("psp_provider", PspProvider),
    ("psp_settlement_mode", SettlementMode),
    ("psp_status_mode", StatusMode),
    ("quote_part_kind", PartKind),
    ("research_status", ResearchStatus),
    ("restriction_rule", RestrictionRule),
    ("rounding_mode", RoundingMode),
    ("row_standing", Standing),
    ("service_tier", ServiceTier),
    ("settlement_line_type", LineType),
    ("shipping_price_state", PriceState),
    ("surcharge_basis", SurchargeBasis),
    ("surcharge_trigger", SurchargeTrigger),
)


async def register_enums(conn: psycopg.AsyncConnection[TupleRow]) -> None:
    """Register every listed enum the database has, in one query. One it
    lacks yet (a database before its migration, as the migration runner
    meets it) is skipped: its column cannot be read either."""
    by_name: dict[str, type[StrEnum]] = {}
    for name, enum in DATABASE_ENUMS:
        by_name[name] = enum
    cur = await conn.execute(
        """
        SELECT t.typname, t.oid, t.typarray,
               array_agg(e.enumlabel ORDER BY e.enumsortorder)
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        JOIN pg_enum e ON e.enumtypid = t.oid
        WHERE n.nspname = 'public' AND t.typname = ANY(%s)
        GROUP BY t.typname, t.oid, t.typarray
        """,
        (list(by_name),),
    )
    rows = await cur.fetchall()
    for name, oid, array_oid, labels in rows:
        enum = by_name[name]
        mapping: dict[StrEnum, str] = {}
        for member in enum:
            mapping[member] = member.value
        register_enum(
            EnumInfo(name, oid, array_oid, labels), conn, enum, mapping=mapping
        )

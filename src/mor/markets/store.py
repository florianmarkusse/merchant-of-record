"""Market loads in PostgreSQL (docs/plan-quote.md §2). Functions take the
connection first and never commit."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from fractions import Fraction

from psycopg.rows import class_row

from mor.audit import Actor
from mor.db.connect import Connection
from mor.ids import (
    CarrierCode,
    CountryCode,
    GroupCode,
    HsCode,
    MarketLoadId,
    ServiceCode,
)
from mor.markets.records import (
    Amount,
    Band,
    Base,
    CardRate,
    CardZone,
    Category,
    Charge,
    Collector,
    Country,
    DutiesPaid,
    DutySource,
    FeeComponent,
    FeeRule,
    FuelApplies,
    FxRate,
    Group,
    GroupKind,
    HsRate,
    LandedBasis,
    LandedRate,
    Limit,
    LimitBasis,
    MarketSet,
    Percent,
    PerLine,
    PerParcel,
    Preference,
    PreferenceCondition,
    PriceSource,
    RateCard,
    Registration,
    ResearchStatus,
    Scope,
    Service,
    ServiceRestriction,
    ServiceTier,
    ShippingRoute,
    Standing,
    Surcharge,
    SurchargeBasis,
    SurchargeTrigger,
    Transit,
    content_sha256,
)
from mor.money.amount import Money
from mor.money.currency import Currency


# One load at a time: a second waits, then finds the first's set.
LOAD_LOCK = "markets:load"


@dataclass(frozen=True, kw_only=True)
class LoadRow:
    id: MarketLoadId
    content_sha256: str
    git_commit: str
    loaded_at: datetime


@dataclass(frozen=True)
class Loaded:
    load_id: MarketLoadId
    previous: LoadRow | None


@dataclass(frozen=True)
class Unchanged:
    """The files hold exactly what the latest load holds: nothing written."""

    load_id: MarketLoadId


async def latest_load(conn: Connection) -> LoadRow | None:
    """The load in force: the newest. Ids are uuid7, so they sort by time."""
    async with conn.cursor(row_factory=class_row(LoadRow)) as cur:
        await cur.execute(
            "SELECT id, content_sha256, git_commit, loaded_at FROM market_load"
            " ORDER BY id DESC LIMIT 1"
        )
        return await cur.fetchone()


@dataclass(frozen=True)
class MarketInForce:
    load_id: MarketLoadId
    market: MarketSet


async def load_in_force(conn: Connection) -> MarketInForce | None:
    """The load in force with its complete set, or None before any load."""
    load = await latest_load(conn)
    if load is None:
        return None
    return MarketInForce(load.id, await read_market(conn, load.id))


async def market_in_force(conn: Connection) -> MarketSet | None:
    """The complete set of the load in force, or None before any load."""
    loaded = await load_in_force(conn)
    if loaded is None:
        return None
    return loaded.market


async def recent_loads(conn: Connection, limit: int) -> list[LoadRow]:
    """The newest loads first; the first is the one in force."""
    async with conn.cursor(row_factory=class_row(LoadRow)) as cur:
        await cur.execute(
            "SELECT id, content_sha256, git_commit, loaded_at FROM market_load"
            " ORDER BY id DESC LIMIT %s",
            (limit,),
        )
        return await cur.fetchall()


async def load_market(
    conn: Connection, market: MarketSet, git_commit: str, actor: Actor, now: datetime
) -> Loaded | Unchanged:
    """Write `market` as a new load, unless it equals the latest one. Under
    a transaction-level lock, so two loads never interleave. Never commits."""
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (LOAD_LOCK,)
    )
    previous = await latest_load(conn)
    sha = content_sha256(market)
    if previous is not None and previous.content_sha256 == sha:
        return Unchanged(previous.id)
    load_id = MarketLoadId(uuid.uuid7())
    await conn.execute(
        "INSERT INTO market_load"
        " (id, content_sha256, git_commit, loaded_at, actor_kind, actor_id)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (load_id, sha, git_commit, now, actor.kind.value, actor.id),
    )
    await _insert_rows(conn, load_id, market)
    return Loaded(load_id, previous)


async def _insert_rows(
    conn: Connection, load_id: MarketLoadId, market: MarketSet
) -> None:
    async with conn.cursor() as cur:
        await cur.executemany(
            "INSERT INTO market_category (load_id, category, label)"
            " VALUES (%s, %s, %s)",
            [(load_id, c.category, c.label) for c in market.categories],
        )
        await cur.executemany(
            "INSERT INTO market_country"
            " (load_id, iso2, name, currency, status, status_note)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (load_id, c.iso2, c.name, c.currency, c.status.value, c.status_note)
                for c in market.countries
            ],
        )
        await cur.executemany(
            "INSERT INTO market_group (load_id, code, label, kind)"
            " VALUES (%s, %s, %s, %s)",
            [(load_id, g.code, g.label, g.kind.value) for g in market.groups],
        )
        members: list[tuple[MarketLoadId, str, str]] = []
        for group in market.groups:
            for iso2 in group.members:
                members.append((load_id, group.code, iso2))
        await cur.executemany(
            "INSERT INTO market_group_member (load_id, group_code, iso2)"
            " VALUES (%s, %s, %s)",
            members,
        )
        rates: list[tuple[object, ...]] = []
        for rate in market.landed_rates:
            rates.append(_landed_params(load_id, rate))
        await cur.executemany(
            "INSERT INTO landed_rate"
            " (load_id, line_no, country_iso2, group_code, category, charge,"
            "  collector, band, limit_minor, limit_currency, limit_basis, basis,"
            "  rate_numerator, rate_denominator, amount_minor, amount_currency,"
            "  base, effective_from, status, source, source_url)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
            "  %s, %s, %s, %s, %s, %s, %s)",
            rates,
        )
        await cur.executemany(
            "INSERT INTO market_registration"
            " (load_id, iso2, scheme, number, effective_from)"
            " VALUES (%s, %s, %s, %s, %s)",
            [
                (load_id, r.country, r.scheme, r.number, r.effective_from)
                for r in market.registrations
            ],
        )
        await cur.executemany(
            "INSERT INTO market_fx_rate"
            " (load_id, base_currency, quote_currency, rate_numerator,"
            "  rate_denominator, as_of, status, source)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    load_id,
                    fx.base.value,
                    fx.quote.value,
                    fx.rate.numerator,
                    fx.rate.denominator,
                    fx.as_of,
                    fx.status.value,
                    fx.source,
                )
                for fx in market.fx_rates
            ],
        )
        hs_rows: list[tuple[object, ...]] = []
        for hs in market.hs_rates:
            hs_rows.append(_hs_params(load_id, hs))
        await cur.executemany(
            "INSERT INTO hs_rate"
            " (load_id, line_no, country_iso2, group_code, hs_prefix, charge,"
            "  origin_iso2, agreement, conditions, rate_numerator,"
            "  rate_denominator, effective_from, status, source, source_url)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s,"
            "  %s::text[]::preference_condition[], %s, %s, %s, %s, %s, %s)",
            hs_rows,
        )
        await cur.executemany(
            "INSERT INTO duty_source (load_id, iso2, mode, vendor_name)"
            " VALUES (%s, %s, %s, %s)",
            [(load_id, d.country, d.mode.value, d.vendor) for d in market.duty_sources],
        )
    await _insert_shipping(conn, load_id, market)


def _numerator(value: Fraction | None) -> int | None:
    return value.numerator if value is not None else None


def _denominator(value: Fraction | None) -> int | None:
    return value.denominator if value is not None else None


async def _insert_shipping(
    conn: Connection, load_id: MarketLoadId, market: MarketSet
) -> None:
    async with conn.cursor() as cur:
        await cur.executemany(
            "INSERT INTO shipping_service"
            " (load_id, code, provider, name, origin_iso2, tier, duties_paid,"
            "  volumetric_divisor, max_weight_numerator, max_weight_denominator,"
            "  status)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    load_id,
                    s.code,
                    s.provider,
                    s.name,
                    s.origin,
                    s.tier.value,
                    s.duties_paid.value,
                    s.volumetric_divisor,
                    _numerator(s.max_weight_kg),
                    _denominator(s.max_weight_kg),
                    s.status.value,
                )
                for s in market.services
            ],
        )
        await cur.executemany(
            "INSERT INTO rate_card"
            " (load_id, code, service, currency, effective_from, effective_to,"
            "  fuel, status, source)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    load_id,
                    c.code,
                    c.service,
                    c.currency.value,
                    c.effective_from,
                    c.effective_to,
                    c.fuel.value,
                    c.status.value,
                    c.source,
                )
                for c in market.rate_cards
            ],
        )
        await cur.executemany(
            "INSERT INTO rate_card_zone"
            " (load_id, rate_card, destination_iso2, zone, status)"
            " VALUES (%s, %s, %s, %s, %s)",
            [
                (load_id, z.rate_card, z.destination, z.zone, z.status.value)
                for z in market.zones
            ],
        )
        await cur.executemany(
            "INSERT INTO rate_card_rate"
            " (load_id, rate_card, zone, weight_numerator, weight_denominator,"
            "  amount_minor, amount_max_minor)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    load_id,
                    r.rate_card,
                    r.zone,
                    r.weight_kg.numerator,
                    r.weight_kg.denominator,
                    r.amount_minor,
                    r.amount_max_minor,
                )
                for r in market.rates
            ],
        )
        await cur.executemany(
            "INSERT INTO shipping_surcharge"
            " (load_id, line_no, service, rate_card, name, basis, rate_numerator,"
            "  rate_denominator, amount_minor, min_amount_minor, currency, trigger,"
            "  destinations, fuel_applies, valid_until, status, source)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
            "  %s, %s)",
            [
                (
                    load_id,
                    s.line_no,
                    s.service,
                    s.rate_card,
                    s.name,
                    s.basis.value,
                    _numerator(s.rate),
                    _denominator(s.rate),
                    s.amount_minor,
                    s.min_amount_minor,
                    s.currency.value,
                    s.trigger.value,
                    list(s.destinations),
                    s.fuel_applies.value,
                    s.valid_until,
                    s.status.value,
                    s.source,
                )
                for s in market.surcharges
            ],
        )
        await cur.executemany(
            "INSERT INTO shipping_transit"
            " (load_id, service, country_iso2, group_code, days_min, days_max,"
            "  status)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    load_id,
                    t.service,
                    t.scope.country,
                    t.scope.group,
                    t.days_min,
                    t.days_max,
                    t.status.value,
                )
                for t in market.transit
            ],
        )
        await cur.executemany(
            "INSERT INTO service_restriction (load_id, service, category, rule, status)"
            " VALUES (%s, %s, %s, %s, %s)",
            [
                (load_id, r.service, r.category, r.rule.value, r.status.value)
                for r in market.restrictions
            ],
        )
        await cur.executemany(
            "INSERT INTO shipping_route"
            " (load_id, country_iso2, group_code, service, rate_card, source,"
            " standing) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    load_id,
                    r.scope.country,
                    r.scope.group,
                    r.service,
                    r.rate_card,
                    r.source.value,
                    r.standing.value,
                )
                for r in market.routes
            ],
        )
        fee_rows: list[tuple[object, ...]] = []
        for fee in market.fees:
            country = fee.scope.country if fee.scope is not None else None
            group = fee.scope.group if fee.scope is not None else None
            fixed_minor = fee.fixed.amount_minor if fee.fixed is not None else None
            fixed_currency = fee.fixed.currency.value if fee.fixed is not None else None
            fee_rows.append(
                (
                    load_id,
                    fee.line_no,
                    fee.component.value,
                    country,
                    group,
                    fee.rate.numerator,
                    fee.rate.denominator,
                    fixed_minor,
                    fixed_currency,
                    fee.effective_from,
                    fee.standing.value,
                    fee.source,
                )
            )
        await cur.executemany(
            "INSERT INTO fee_rule"
            " (load_id, line_no, component, country_iso2, group_code,"
            "  rate_numerator, rate_denominator, fixed_minor, fixed_currency,"
            "  effective_from, standing, source)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            fee_rows,
        )


def _hs_params(load_id: MarketLoadId, hs: HsRate) -> tuple[object, ...]:
    agreement: str | None = None
    conditions: list[str] = []
    if hs.preference is not None:
        agreement = hs.preference.agreement
        for condition in hs.preference.conditions:
            conditions.append(condition.value)
    return (
        load_id,
        hs.line_no,
        hs.scope.country,
        hs.scope.group,
        hs.hs_prefix,
        hs.charge.value,
        hs.origin,
        agreement,
        sorted(conditions),
        hs.rate.numerator,
        hs.rate.denominator,
        hs.effective_from,
        hs.status.value,
        hs.source,
        hs.source_url,
    )


def _landed_params(load_id: MarketLoadId, rate: LandedRate) -> tuple[object, ...]:
    limit_minor: int | None = None
    limit_currency: str | None = None
    limit_basis: str | None = None
    if rate.limit is not None:
        limit_minor = rate.limit.amount.amount_minor
        limit_currency = rate.limit.amount.currency.value
        limit_basis = rate.limit.basis.value
    numerator: int | None = None
    denominator: int | None = None
    amount_minor: int | None = None
    amount_currency: str | None = None
    base: str | None = None
    basis: LandedBasis
    match rate.amount:
        case Percent(rate=fraction, base=percent_base):
            basis = LandedBasis.PERCENT
            numerator = fraction.numerator
            denominator = fraction.denominator
            base = percent_base.value
        case PerLine(amount=money):
            basis = LandedBasis.PER_LINE
            amount_minor = money.amount_minor
            amount_currency = money.currency.value
        case PerParcel(amount=money):
            basis = LandedBasis.PER_PARCEL
            amount_minor = money.amount_minor
            amount_currency = money.currency.value
    return (
        load_id,
        rate.line_no,
        rate.scope.country,
        rate.scope.group,
        rate.category,
        rate.charge.value,
        rate.collector.value,
        rate.band.value,
        limit_minor,
        limit_currency,
        limit_basis,
        basis,
        numerator,
        denominator,
        amount_minor,
        amount_currency,
        base,
        rate.effective_from,
        rate.status.value,
        rate.source,
        rate.source_url,
    )


# --- reading a load back --------------------------------------------------------
# Each table is read with `class_row` into a row record named after its
# columns, then turned into its domain record with keyword arguments.


async def read_market(conn: Connection, load_id: MarketLoadId) -> MarketSet:
    """The complete set of one load, sorted by key (landed rates by their
    line in the file). Its content hash is the stored one, never recomputed
    from this order."""
    async with conn.cursor(row_factory=class_row(Category)) as cur:
        await cur.execute(
            "SELECT category, label FROM market_category WHERE load_id = %s"
            " ORDER BY category",
            (load_id,),
        )
        categories = await cur.fetchall()
    async with conn.cursor(row_factory=class_row(Country)) as cur:
        await cur.execute(
            "SELECT iso2, name, currency, status, status_note FROM market_country"
            " WHERE load_id = %s ORDER BY iso2",
            (load_id,),
        )
        countries = await cur.fetchall()
    async with conn.cursor(row_factory=class_row(Registration)) as cur:
        await cur.execute(
            "SELECT iso2 AS country, scheme, number, effective_from"
            " FROM market_registration WHERE load_id = %s ORDER BY iso2, scheme",
            (load_id,),
        )
        registrations = await cur.fetchall()
    async with conn.cursor(row_factory=class_row(DutySource)) as cur:
        await cur.execute(
            "SELECT iso2 AS country, mode, vendor_name AS vendor FROM duty_source"
            " WHERE load_id = %s ORDER BY iso2",
            (load_id,),
        )
        duty_sources = await cur.fetchall()
    shipping = await _read_shipping(conn, load_id)
    return MarketSet(
        categories=tuple(categories),
        countries=tuple(countries),
        groups=await _read_groups(conn, load_id),
        landed_rates=await _read_landed(conn, load_id),
        registrations=tuple(registrations),
        fx_rates=await _read_fx_rates(conn, load_id),
        hs_rates=await _read_hs_rates(conn, load_id),
        duty_sources=tuple(duty_sources),
        services=shipping.services,
        rate_cards=shipping.rate_cards,
        zones=shipping.zones,
        rates=shipping.rates,
        surcharges=shipping.surcharges,
        transit=shipping.transit,
        restrictions=shipping.restrictions,
        routes=shipping.routes,
        fees=shipping.fees,
    )


def _optional_fraction(
    numerator: int | None, denominator: int | None
) -> Fraction | None:
    if numerator is None or denominator is None:
        return None
    return Fraction(numerator, denominator)


@dataclass(frozen=True, kw_only=True)
class _GroupRow:
    code: GroupCode
    label: str
    kind: GroupKind
    members: list[CountryCode]


async def _read_groups(conn: Connection, load_id: MarketLoadId) -> tuple[Group, ...]:
    async with conn.cursor(row_factory=class_row(_GroupRow)) as cur:
        await cur.execute(
            "SELECT g.code, g.label, g.kind,"
            " array(SELECT m.iso2::text FROM market_group_member m"
            "       WHERE m.load_id = g.load_id AND m.group_code = g.code"
            "       ORDER BY m.iso2) AS members"
            " FROM market_group g WHERE g.load_id = %s ORDER BY g.code",
            (load_id,),
        )
        rows = await cur.fetchall()
    groups: list[Group] = []
    for row in rows:
        groups.append(
            Group(
                code=row.code,
                label=row.label,
                kind=row.kind,
                members=tuple(row.members),
            )
        )
    return tuple(groups)


@dataclass(frozen=True, kw_only=True)
class _LandedRow:
    line_no: int
    country_iso2: CountryCode | None
    group_code: GroupCode | None
    category: str | None
    charge: Charge
    collector: Collector
    band: Band
    limit_minor: int | None
    limit_currency: Currency | None
    limit_basis: LimitBasis | None
    basis: LandedBasis
    rate_numerator: int | None
    rate_denominator: int | None
    amount_minor: int | None
    amount_currency: Currency | None
    base: Base | None
    effective_from: date | None
    status: ResearchStatus
    source: str
    source_url: str


async def _read_landed(
    conn: Connection, load_id: MarketLoadId
) -> tuple[LandedRate, ...]:
    async with conn.cursor(row_factory=class_row(_LandedRow)) as cur:
        await cur.execute(
            "SELECT line_no, country_iso2, group_code, category, charge,"
            " collector, band, limit_minor, limit_currency,"
            " limit_basis, basis, rate_numerator, rate_denominator,"
            " amount_minor, amount_currency, base, effective_from,"
            " status, source, source_url"
            " FROM landed_rate WHERE load_id = %s ORDER BY line_no",
            (load_id,),
        )
        rows = await cur.fetchall()
    landed: list[LandedRate] = []
    for row in rows:
        landed.append(_landed_from_row(row))
    return tuple(landed)


def _landed_from_row(row: _LandedRow) -> LandedRate:
    limit: Limit | None = None
    if (
        row.limit_minor is not None
        and row.limit_currency is not None
        and row.limit_basis is not None
    ):
        limit = Limit(
            amount=Money(row.limit_minor, row.limit_currency), basis=row.limit_basis
        )
    amount: Amount
    # CHECKs on landed_rate keep each basis's columns set and the others
    # empty.
    match row.basis:
        case LandedBasis.PERCENT:
            assert row.rate_numerator is not None and row.rate_denominator is not None
            assert row.base is not None
            amount = Percent(
                Fraction(row.rate_numerator, row.rate_denominator), row.base
            )
        case LandedBasis.PER_LINE:
            assert row.amount_minor is not None and row.amount_currency is not None
            amount = PerLine(Money(row.amount_minor, row.amount_currency))
        case LandedBasis.PER_PARCEL:
            assert row.amount_minor is not None and row.amount_currency is not None
            amount = PerParcel(Money(row.amount_minor, row.amount_currency))
    return LandedRate(
        line_no=row.line_no,
        scope=Scope(country=row.country_iso2, group=row.group_code),
        category=row.category,
        charge=row.charge,
        collector=row.collector,
        band=row.band,
        limit=limit,
        amount=amount,
        effective_from=row.effective_from,
        status=row.status,
        source=row.source,
        source_url=row.source_url,
    )


@dataclass(frozen=True, kw_only=True)
class _FxRow:
    base_currency: Currency
    quote_currency: Currency
    rate_numerator: int
    rate_denominator: int
    as_of: date
    status: ResearchStatus
    source: str


async def _read_fx_rates(conn: Connection, load_id: MarketLoadId) -> tuple[FxRate, ...]:
    async with conn.cursor(row_factory=class_row(_FxRow)) as cur:
        await cur.execute(
            "SELECT base_currency, quote_currency, rate_numerator,"
            " rate_denominator, as_of, status, source FROM market_fx_rate"
            " WHERE load_id = %s ORDER BY base_currency, quote_currency",
            (load_id,),
        )
        rows = await cur.fetchall()
    fx_rates: list[FxRate] = []
    for row in rows:
        fx_rates.append(
            FxRate(
                base=row.base_currency,
                quote=row.quote_currency,
                rate=Fraction(row.rate_numerator, row.rate_denominator),
                as_of=row.as_of,
                status=row.status,
                source=row.source,
            )
        )
    return tuple(fx_rates)


@dataclass(frozen=True, kw_only=True)
class _HsRow:
    line_no: int
    country_iso2: CountryCode | None
    group_code: GroupCode | None
    hs_prefix: HsCode
    charge: Charge
    origin_iso2: CountryCode | None
    agreement: str | None
    conditions: list[PreferenceCondition]
    rate_numerator: int
    rate_denominator: int
    effective_from: date | None
    status: ResearchStatus
    source: str
    source_url: str


async def _read_hs_rates(conn: Connection, load_id: MarketLoadId) -> tuple[HsRate, ...]:
    async with conn.cursor(row_factory=class_row(_HsRow)) as cur:
        await cur.execute(
            "SELECT line_no, country_iso2, group_code, hs_prefix, charge,"
            " origin_iso2, agreement, conditions, rate_numerator,"
            " rate_denominator, effective_from, status, source, source_url"
            " FROM hs_rate WHERE load_id = %s ORDER BY line_no",
            (load_id,),
        )
        rows = await cur.fetchall()
    hs_rates: list[HsRate] = []
    for row in rows:
        preference: Preference | None = None
        if row.agreement is not None:
            preference = Preference(
                agreement=row.agreement, conditions=frozenset(row.conditions)
            )
        hs_rates.append(
            HsRate(
                line_no=row.line_no,
                scope=Scope(country=row.country_iso2, group=row.group_code),
                hs_prefix=row.hs_prefix,
                charge=row.charge,
                origin=row.origin_iso2,
                preference=preference,
                rate=Fraction(row.rate_numerator, row.rate_denominator),
                effective_from=row.effective_from,
                status=row.status,
                source=row.source,
                source_url=row.source_url,
            )
        )
    return tuple(hs_rates)


# --- shipping -----------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class _Shipping:
    services: tuple[Service, ...]
    rate_cards: tuple[RateCard, ...]
    zones: tuple[CardZone, ...]
    rates: tuple[CardRate, ...]
    surcharges: tuple[Surcharge, ...]
    transit: tuple[Transit, ...]
    restrictions: tuple[ServiceRestriction, ...]
    routes: tuple[ShippingRoute, ...]
    fees: tuple[FeeRule, ...]


async def _read_shipping(conn: Connection, load_id: MarketLoadId) -> _Shipping:
    async with conn.cursor(row_factory=class_row(RateCard)) as cur:
        await cur.execute(
            "SELECT code, service, currency, effective_from, effective_to, fuel,"
            " status, source FROM rate_card WHERE load_id = %s ORDER BY code",
            (load_id,),
        )
        cards = await cur.fetchall()
    async with conn.cursor(row_factory=class_row(CardZone)) as cur:
        await cur.execute(
            "SELECT rate_card, destination_iso2 AS destination, zone, status"
            " FROM rate_card_zone WHERE load_id = %s"
            " ORDER BY rate_card, destination_iso2",
            (load_id,),
        )
        zones = await cur.fetchall()
    async with conn.cursor(row_factory=class_row(ServiceRestriction)) as cur:
        await cur.execute(
            "SELECT service, category, rule, status FROM service_restriction"
            " WHERE load_id = %s ORDER BY service, category",
            (load_id,),
        )
        restrictions = await cur.fetchall()
    return _Shipping(
        services=await _read_services(conn, load_id),
        rate_cards=tuple(cards),
        zones=tuple(zones),
        rates=await _read_card_rates(conn, load_id),
        surcharges=await _read_surcharges(conn, load_id),
        transit=await _read_transit(conn, load_id),
        restrictions=tuple(restrictions),
        routes=await _read_routes(conn, load_id),
        fees=await _read_fees(conn, load_id),
    )


@dataclass(frozen=True, kw_only=True)
class _ServiceRow:
    code: ServiceCode
    provider: CarrierCode
    name: str
    origin_iso2: CountryCode
    tier: ServiceTier
    duties_paid: DutiesPaid
    volumetric_divisor: int | None
    max_weight_numerator: int | None
    max_weight_denominator: int | None
    status: ResearchStatus


async def _read_services(
    conn: Connection, load_id: MarketLoadId
) -> tuple[Service, ...]:
    async with conn.cursor(row_factory=class_row(_ServiceRow)) as cur:
        await cur.execute(
            "SELECT code, provider, name, origin_iso2, tier, duties_paid,"
            " volumetric_divisor, max_weight_numerator, max_weight_denominator,"
            " status FROM shipping_service WHERE load_id = %s ORDER BY code",
            (load_id,),
        )
        rows = await cur.fetchall()
    services: list[Service] = []
    for row in rows:
        services.append(
            Service(
                code=row.code,
                provider=row.provider,
                name=row.name,
                origin=row.origin_iso2,
                tier=row.tier,
                duties_paid=row.duties_paid,
                volumetric_divisor=row.volumetric_divisor,
                max_weight_kg=_optional_fraction(
                    row.max_weight_numerator, row.max_weight_denominator
                ),
                status=row.status,
            )
        )
    return tuple(services)


@dataclass(frozen=True, kw_only=True)
class _CardRateRow:
    rate_card: str
    zone: str
    weight_numerator: int
    weight_denominator: int
    amount_minor: int
    amount_max_minor: int | None


async def _read_card_rates(
    conn: Connection, load_id: MarketLoadId
) -> tuple[CardRate, ...]:
    async with conn.cursor(row_factory=class_row(_CardRateRow)) as cur:
        await cur.execute(
            "SELECT rate_card, zone, weight_numerator, weight_denominator,"
            " amount_minor, amount_max_minor FROM rate_card_rate"
            " WHERE load_id = %s ORDER BY rate_card, zone,"
            " weight_numerator::numeric / weight_denominator",
            (load_id,),
        )
        rows = await cur.fetchall()
    rates: list[CardRate] = []
    for row in rows:
        rates.append(
            CardRate(
                rate_card=row.rate_card,
                zone=row.zone,
                weight_kg=Fraction(row.weight_numerator, row.weight_denominator),
                amount_minor=row.amount_minor,
                amount_max_minor=row.amount_max_minor,
            )
        )
    return tuple(rates)


@dataclass(frozen=True, kw_only=True)
class _SurchargeRow:
    line_no: int
    service: ServiceCode
    rate_card: str | None
    name: str
    basis: SurchargeBasis
    rate_numerator: int | None
    rate_denominator: int | None
    amount_minor: int | None
    min_amount_minor: int | None
    currency: Currency
    trigger: SurchargeTrigger
    destinations: list[str]
    fuel_applies: FuelApplies
    valid_until: date | None
    status: ResearchStatus
    source: str


async def _read_surcharges(
    conn: Connection, load_id: MarketLoadId
) -> tuple[Surcharge, ...]:
    async with conn.cursor(row_factory=class_row(_SurchargeRow)) as cur:
        await cur.execute(
            "SELECT line_no, service, rate_card, name, basis, rate_numerator,"
            " rate_denominator, amount_minor, min_amount_minor, currency,"
            " trigger, destinations, fuel_applies, valid_until,"
            " status, source FROM shipping_surcharge WHERE load_id = %s"
            " ORDER BY line_no",
            (load_id,),
        )
        rows = await cur.fetchall()
    surcharges: list[Surcharge] = []
    for row in rows:
        surcharges.append(
            Surcharge(
                line_no=row.line_no,
                service=row.service,
                rate_card=row.rate_card,
                name=row.name,
                basis=row.basis,
                rate=_optional_fraction(row.rate_numerator, row.rate_denominator),
                amount_minor=row.amount_minor,
                min_amount_minor=row.min_amount_minor,
                currency=row.currency,
                trigger=row.trigger,
                destinations=tuple(row.destinations),
                fuel_applies=row.fuel_applies,
                valid_until=row.valid_until,
                status=row.status,
                source=row.source,
            )
        )
    return tuple(surcharges)


@dataclass(frozen=True, kw_only=True)
class _TransitRow:
    service: ServiceCode
    country_iso2: CountryCode | None
    group_code: GroupCode | None
    days_min: int
    days_max: int
    status: ResearchStatus


async def _read_transit(conn: Connection, load_id: MarketLoadId) -> tuple[Transit, ...]:
    async with conn.cursor(row_factory=class_row(_TransitRow)) as cur:
        await cur.execute(
            "SELECT service, country_iso2, group_code, days_min, days_max, status"
            " FROM shipping_transit WHERE load_id = %s"
            " ORDER BY service, country_iso2, group_code",
            (load_id,),
        )
        rows = await cur.fetchall()
    transit: list[Transit] = []
    for row in rows:
        transit.append(
            Transit(
                service=row.service,
                scope=Scope(country=row.country_iso2, group=row.group_code),
                days_min=row.days_min,
                days_max=row.days_max,
                status=row.status,
            )
        )
    return tuple(transit)


@dataclass(frozen=True, kw_only=True)
class _RouteRow:
    country_iso2: CountryCode | None
    group_code: GroupCode | None
    service: ServiceCode
    rate_card: str | None
    source: PriceSource
    standing: Standing


async def _read_routes(
    conn: Connection, load_id: MarketLoadId
) -> tuple[ShippingRoute, ...]:
    async with conn.cursor(row_factory=class_row(_RouteRow)) as cur:
        await cur.execute(
            "SELECT country_iso2, group_code, service, rate_card, source,"
            " standing FROM shipping_route WHERE load_id = %s"
            " ORDER BY service, rate_card, country_iso2, group_code",
            (load_id,),
        )
        rows = await cur.fetchall()
    routes: list[ShippingRoute] = []
    for row in rows:
        routes.append(
            ShippingRoute(
                scope=Scope(country=row.country_iso2, group=row.group_code),
                service=row.service,
                rate_card=row.rate_card,
                source=row.source,
                standing=row.standing,
            )
        )
    return tuple(routes)


@dataclass(frozen=True, kw_only=True)
class _FeeRow:
    line_no: int
    component: FeeComponent
    country_iso2: CountryCode | None
    group_code: GroupCode | None
    rate_numerator: int
    rate_denominator: int
    fixed_minor: int | None
    fixed_currency: Currency | None
    effective_from: date
    standing: Standing
    source: str


async def _read_fees(conn: Connection, load_id: MarketLoadId) -> tuple[FeeRule, ...]:
    async with conn.cursor(row_factory=class_row(_FeeRow)) as cur:
        await cur.execute(
            "SELECT line_no, component, country_iso2, group_code, rate_numerator,"
            " rate_denominator, fixed_minor, fixed_currency, effective_from,"
            " standing, source FROM fee_rule WHERE load_id = %s ORDER BY line_no",
            (load_id,),
        )
        rows = await cur.fetchall()
    fees: list[FeeRule] = []
    for row in rows:
        # A fee with neither country nor group applies everywhere.
        scope: Scope | None = None
        if row.country_iso2 is not None or row.group_code is not None:
            scope = Scope(country=row.country_iso2, group=row.group_code)
        fixed: Money | None = None
        if row.fixed_minor is not None and row.fixed_currency is not None:
            fixed = Money(row.fixed_minor, row.fixed_currency)
        fees.append(
            FeeRule(
                line_no=row.line_no,
                component=row.component,
                scope=scope,
                rate=Fraction(row.rate_numerator, row.rate_denominator),
                fixed=fixed,
                effective_from=row.effective_from,
                standing=row.standing,
                source=row.source,
            )
        )
    return tuple(fees)

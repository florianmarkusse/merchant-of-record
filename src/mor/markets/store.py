"""Market loads in PostgreSQL (docs/plan-quote.md §2). Functions take the
connection first and never commit."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from fractions import Fraction

from psycopg.rows import class_row

from mor.audit import Actor
from mor.db.connect import Connection
from mor.markets.records import (
    Amount,
    Band,
    Base,
    CardFuel,
    CardRate,
    CardZone,
    Category,
    Charge,
    Collector,
    Country,
    DutiesPaid,
    DutyMode,
    DutySource,
    FeeComponent,
    FeeRule,
    FuelApplies,
    FxRate,
    Group,
    GroupKind,
    HsRate,
    LandedRate,
    Limit,
    LimitBasis,
    MarketSet,
    MarketStatus,
    Percent,
    PerLine,
    PerParcel,
    Preference,
    PreferenceCondition,
    PriceSource,
    RateCard,
    Registration,
    ResearchStatus,
    RestrictionRule,
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


@dataclass(frozen=True)
class LoadRow:
    id: uuid.UUID
    content_sha256: str
    git_commit: str
    loaded_at: datetime


@dataclass(frozen=True)
class Loaded:
    load_id: uuid.UUID
    previous: LoadRow | None


@dataclass(frozen=True)
class Unchanged:
    """The files hold exactly what the latest load holds: nothing written."""

    load_id: uuid.UUID


async def latest_load(conn: Connection) -> LoadRow | None:
    """The load in force: the newest. Ids are uuid7, so they sort by time."""
    async with conn.cursor(row_factory=class_row(LoadRow)) as cur:
        await cur.execute(
            "SELECT id, content_sha256, git_commit, loaded_at FROM market_load"
            " ORDER BY id DESC LIMIT 1"
        )
        return await cur.fetchone()


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
    load_id = uuid.uuid7()
    await conn.execute(
        "INSERT INTO market_load"
        " (id, content_sha256, git_commit, loaded_at, actor_kind, actor_id)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (load_id, sha, git_commit, now, actor.kind.value, actor.id),
    )
    await _insert_rows(conn, load_id, market)
    return Loaded(load_id, previous)


async def _insert_rows(conn: Connection, load_id: uuid.UUID, market: MarketSet) -> None:
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
        members: list[tuple[uuid.UUID, str, str]] = []
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
    conn: Connection, load_id: uuid.UUID, market: MarketSet
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
            " (load_id, country_iso2, group_code, rate_card, source, standing)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (
                    load_id,
                    r.scope.country,
                    r.scope.group,
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


def _hs_params(load_id: uuid.UUID, hs: HsRate) -> tuple[object, ...]:
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


def _landed_params(load_id: uuid.UUID, rate: LandedRate) -> tuple[object, ...]:
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
    basis: str
    match rate.amount:
        case Percent(rate=fraction, base=percent_base):
            basis = "percent"
            numerator = fraction.numerator
            denominator = fraction.denominator
            base = percent_base.value
        case PerLine(amount=money):
            basis = "per_line"
            amount_minor = money.amount_minor
            amount_currency = money.currency.value
        case PerParcel(amount=money):
            basis = "per_parcel"
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


@dataclass(frozen=True)
class _LandedRow:
    line_no: int
    country_iso2: str | None
    group_code: str | None
    category: str | None
    charge: str
    collector: str
    band: str
    limit_minor: int | None
    limit_currency: str | None
    limit_basis: str | None
    basis: str
    rate_numerator: int | None
    rate_denominator: int | None
    amount_minor: int | None
    amount_currency: str | None
    base: str | None
    effective_from: date | None
    status: str
    source: str
    source_url: str


async def read_market(conn: Connection, load_id: uuid.UUID) -> MarketSet:
    """The complete set of one load, sorted by key (landed rates by their
    line in the file). Its content hash is the stored one, never recomputed
    from this order."""
    cur = await conn.execute(
        "SELECT category, label FROM market_category WHERE load_id = %s"
        " ORDER BY category",
        (load_id,),
    )
    categories: list[Category] = []
    for category, label in await cur.fetchall():
        categories.append(Category(category, label))
    cur = await conn.execute(
        "SELECT iso2, name, currency, status::text, status_note FROM market_country"
        " WHERE load_id = %s ORDER BY iso2",
        (load_id,),
    )
    countries: list[Country] = []
    for iso2, name, currency, status, note in await cur.fetchall():
        countries.append(Country(iso2, name, currency, MarketStatus(status), note))
    cur = await conn.execute(
        "SELECT g.code, g.label, g.kind::text,"
        " array(SELECT m.iso2::text FROM market_group_member m"
        "       WHERE m.load_id = g.load_id AND m.group_code = g.code"
        "       ORDER BY m.iso2)"
        " FROM market_group g WHERE g.load_id = %s ORDER BY g.code",
        (load_id,),
    )
    groups: list[Group] = []
    for code, label, kind, members in await cur.fetchall():
        groups.append(Group(code, label, GroupKind(kind), tuple(members)))
    async with conn.cursor(row_factory=class_row(_LandedRow)) as landed_cur:
        await landed_cur.execute(
            "SELECT line_no, country_iso2, group_code, category, charge::text,"
            " collector::text, band::text, limit_minor, limit_currency::text,"
            " limit_basis::text, basis::text, rate_numerator, rate_denominator,"
            " amount_minor, amount_currency::text, base::text, effective_from,"
            " status::text, source, source_url"
            " FROM landed_rate WHERE load_id = %s ORDER BY line_no",
            (load_id,),
        )
        landed: list[LandedRate] = []
        for row in await landed_cur.fetchall():
            landed.append(_landed_from_row(row))
    cur = await conn.execute(
        "SELECT iso2, scheme, number, effective_from FROM market_registration"
        " WHERE load_id = %s ORDER BY iso2, scheme",
        (load_id,),
    )
    registrations: list[Registration] = []
    for iso2, scheme, number, effective_from in await cur.fetchall():
        registrations.append(Registration(iso2, scheme, number, effective_from))
    cur = await conn.execute(
        "SELECT base_currency::text, quote_currency::text, rate_numerator,"
        " rate_denominator, as_of, status::text, source FROM market_fx_rate"
        " WHERE load_id = %s ORDER BY base_currency, quote_currency",
        (load_id,),
    )
    fx_rates: list[FxRate] = []
    for (
        base,
        quote,
        numerator,
        denominator,
        as_of,
        status,
        source,
    ) in await cur.fetchall():
        fx_rates.append(
            FxRate(
                Currency(base),
                Currency(quote),
                Fraction(numerator, denominator),
                as_of,
                ResearchStatus(status),
                source,
            )
        )
    async with conn.cursor(row_factory=class_row(_HsRow)) as hs_cur:
        await hs_cur.execute(
            "SELECT line_no, country_iso2, group_code, hs_prefix, charge::text,"
            " origin_iso2, agreement, conditions::text[], rate_numerator,"
            " rate_denominator, effective_from, status::text, source, source_url"
            " FROM hs_rate WHERE load_id = %s ORDER BY line_no",
            (load_id,),
        )
        hs_rates: list[HsRate] = []
        for row in await hs_cur.fetchall():
            hs_rates.append(_hs_from_row(row))
    cur = await conn.execute(
        "SELECT iso2, mode::text, vendor_name FROM duty_source"
        " WHERE load_id = %s ORDER BY iso2",
        (load_id,),
    )
    duty_sources: list[DutySource] = []
    for iso2, mode, vendor_name in await cur.fetchall():
        duty_sources.append(DutySource(iso2, DutyMode(mode), vendor_name))
    shipping = await _read_shipping(conn, load_id)
    return MarketSet(
        tuple(categories),
        tuple(countries),
        tuple(groups),
        tuple(landed),
        tuple(registrations),
        tuple(fx_rates),
        tuple(hs_rates),
        tuple(duty_sources),
        shipping.services,
        shipping.rate_cards,
        shipping.zones,
        shipping.rates,
        shipping.surcharges,
        shipping.transit,
        shipping.restrictions,
        shipping.routes,
        shipping.fees,
    )


@dataclass(frozen=True)
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


def _optional_fraction(
    numerator: int | None, denominator: int | None
) -> Fraction | None:
    if numerator is None or denominator is None:
        return None
    return Fraction(numerator, denominator)


async def _read_shipping(conn: Connection, load_id: uuid.UUID) -> _Shipping:
    cur = await conn.execute(
        "SELECT code, provider, name, origin_iso2, tier::text, duties_paid::text,"
        " volumetric_divisor, max_weight_numerator, max_weight_denominator,"
        " status::text FROM shipping_service WHERE load_id = %s ORDER BY code",
        (load_id,),
    )
    services: list[Service] = []
    for (
        code,
        provider,
        name,
        origin,
        tier,
        duties_paid,
        divisor,
        max_numerator,
        max_denominator,
        status,
    ) in await cur.fetchall():
        services.append(
            Service(
                code,
                provider,
                name,
                origin,
                ServiceTier(tier),
                DutiesPaid(duties_paid),
                divisor,
                _optional_fraction(max_numerator, max_denominator),
                ResearchStatus(status),
            )
        )
    cur = await conn.execute(
        "SELECT code, service, currency::text, effective_from, effective_to,"
        " fuel::text, status::text, source FROM rate_card WHERE load_id = %s"
        " ORDER BY code",
        (load_id,),
    )
    cards: list[RateCard] = []
    for (
        code,
        service,
        currency,
        effective_from,
        effective_to,
        fuel,
        status,
        source,
    ) in await cur.fetchall():
        cards.append(
            RateCard(
                code,
                service,
                Currency(currency),
                effective_from,
                effective_to,
                CardFuel(fuel),
                ResearchStatus(status),
                source,
            )
        )
    cur = await conn.execute(
        "SELECT rate_card, destination_iso2, zone, status::text FROM rate_card_zone"
        " WHERE load_id = %s ORDER BY rate_card, destination_iso2",
        (load_id,),
    )
    zones: list[CardZone] = []
    for card, destination, zone, status in await cur.fetchall():
        zones.append(CardZone(card, destination, zone, ResearchStatus(status)))
    cur = await conn.execute(
        "SELECT rate_card, zone, weight_numerator, weight_denominator, amount_minor,"
        " amount_max_minor FROM rate_card_rate WHERE load_id = %s"
        " ORDER BY rate_card, zone, weight_numerator::numeric / weight_denominator",
        (load_id,),
    )
    rates: list[CardRate] = []
    for card, zone, numerator, denominator, amount, amount_max in await cur.fetchall():
        rates.append(
            CardRate(card, zone, Fraction(numerator, denominator), amount, amount_max)
        )
    cur = await conn.execute(
        "SELECT line_no, service, rate_card, name, basis::text, rate_numerator,"
        " rate_denominator, amount_minor, min_amount_minor, currency::text,"
        " trigger::text, destinations, fuel_applies::text, valid_until,"
        " status::text, source FROM shipping_surcharge WHERE load_id = %s"
        " ORDER BY line_no",
        (load_id,),
    )
    surcharges: list[Surcharge] = []
    for (
        line_no,
        service,
        card,
        name,
        basis,
        rate_numerator,
        rate_denominator,
        amount,
        minimum,
        currency,
        trigger,
        destinations,
        fuel_applies,
        valid_until,
        status,
        source,
    ) in await cur.fetchall():
        surcharges.append(
            Surcharge(
                line_no,
                service,
                card,
                name,
                SurchargeBasis(basis),
                _optional_fraction(rate_numerator, rate_denominator),
                amount,
                minimum,
                Currency(currency),
                SurchargeTrigger(trigger),
                tuple(destinations),
                FuelApplies(fuel_applies),
                valid_until,
                ResearchStatus(status),
                source,
            )
        )
    cur = await conn.execute(
        "SELECT service, country_iso2, group_code, days_min, days_max, status::text"
        " FROM shipping_transit WHERE load_id = %s"
        " ORDER BY service, country_iso2, group_code",
        (load_id,),
    )
    transit: list[Transit] = []
    for service, country, group, days_min, days_max, status in await cur.fetchall():
        transit.append(
            Transit(
                service,
                Scope(country, group),
                days_min,
                days_max,
                ResearchStatus(status),
            )
        )
    cur = await conn.execute(
        "SELECT service, category, rule::text, status::text FROM service_restriction"
        " WHERE load_id = %s ORDER BY service, category",
        (load_id,),
    )
    restrictions: list[ServiceRestriction] = []
    for service, category, rule, status in await cur.fetchall():
        restrictions.append(
            ServiceRestriction(
                service, category, RestrictionRule(rule), ResearchStatus(status)
            )
        )
    cur = await conn.execute(
        "SELECT country_iso2, group_code, rate_card, source::text, standing::text"
        " FROM shipping_route WHERE load_id = %s"
        " ORDER BY rate_card, country_iso2, group_code",
        (load_id,),
    )
    routes: list[ShippingRoute] = []
    for country, group, card, source, standing in await cur.fetchall():
        routes.append(
            ShippingRoute(
                Scope(country, group), card, PriceSource(source), Standing(standing)
            )
        )
    cur = await conn.execute(
        "SELECT line_no, component::text, country_iso2, group_code, rate_numerator,"
        " rate_denominator, fixed_minor, fixed_currency::text, effective_from,"
        " standing::text, source FROM fee_rule WHERE load_id = %s ORDER BY line_no",
        (load_id,),
    )
    fees: list[FeeRule] = []
    for (
        line_no,
        component,
        country,
        group,
        numerator,
        denominator,
        fixed_minor,
        fixed_currency,
        effective_from,
        standing,
        source,
    ) in await cur.fetchall():
        scope: Scope | None = None
        if country is not None or group is not None:
            scope = Scope(country, group)
        fixed: Money | None = None
        if fixed_minor is not None and fixed_currency is not None:
            fixed = Money(fixed_minor, Currency(fixed_currency))
        fees.append(
            FeeRule(
                line_no,
                FeeComponent(component),
                scope,
                Fraction(numerator, denominator),
                fixed,
                effective_from,
                Standing(standing),
                source,
            )
        )
    return _Shipping(
        tuple(services),
        tuple(cards),
        tuple(zones),
        tuple(rates),
        tuple(surcharges),
        tuple(transit),
        tuple(restrictions),
        tuple(routes),
        tuple(fees),
    )


@dataclass(frozen=True)
class _HsRow:
    line_no: int
    country_iso2: str | None
    group_code: str | None
    hs_prefix: str
    charge: str
    origin_iso2: str | None
    agreement: str | None
    conditions: list[str]
    rate_numerator: int
    rate_denominator: int
    effective_from: date | None
    status: str
    source: str
    source_url: str


def _hs_from_row(row: _HsRow) -> HsRate:
    preference: Preference | None = None
    if row.agreement is not None:
        conditions: set[PreferenceCondition] = set()
        for condition in row.conditions:
            conditions.add(PreferenceCondition(condition))
        preference = Preference(row.agreement, frozenset(conditions))
    return HsRate(
        row.line_no,
        Scope(row.country_iso2, row.group_code),
        row.hs_prefix,
        Charge(row.charge),
        row.origin_iso2,
        preference,
        Fraction(row.rate_numerator, row.rate_denominator),
        row.effective_from,
        ResearchStatus(row.status),
        row.source,
        row.source_url,
    )


def _landed_from_row(row: _LandedRow) -> LandedRate:
    limit: Limit | None = None
    if (
        row.limit_minor is not None
        and row.limit_currency is not None
        and row.limit_basis is not None
    ):
        limit = Limit(
            Money(row.limit_minor, Currency(row.limit_currency)),
            LimitBasis(row.limit_basis),
        )
    amount: Amount
    if row.basis == "percent":
        assert row.rate_numerator is not None and row.rate_denominator is not None
        assert row.base is not None
        amount = Percent(
            Fraction(row.rate_numerator, row.rate_denominator), Base(row.base)
        )
    else:
        assert row.amount_minor is not None and row.amount_currency is not None
        money = Money(row.amount_minor, Currency(row.amount_currency))
        amount = PerLine(money) if row.basis == "per_line" else PerParcel(money)
    return LandedRate(
        row.line_no,
        Scope(row.country_iso2, row.group_code),
        row.category,
        Charge(row.charge),
        Collector(row.collector),
        Band(row.band),
        limit,
        amount,
        row.effective_from,
        ResearchStatus(row.status),
        row.source,
        row.source_url,
    )

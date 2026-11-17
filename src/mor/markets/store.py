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
    Category,
    Charge,
    Collector,
    Country,
    FxRate,
    Group,
    GroupKind,
    LandedRate,
    Limit,
    LimitBasis,
    MarketSet,
    MarketStatus,
    Percent,
    PerLine,
    PerParcel,
    Registration,
    ResearchStatus,
    Scope,
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
    return MarketSet(
        tuple(categories),
        tuple(countries),
        tuple(groups),
        tuple(landed),
        tuple(registrations),
        tuple(fx_rates),
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

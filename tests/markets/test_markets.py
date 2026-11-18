"""Markets in the database (docs/plan-quote.md §2): the research files load
as a versioned set, an edit is a new load showing exactly that change,
rows cannot be changed in place, and a country's rules follow its market
status, the research status of its rates and their start dates."""

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime
from fractions import Fraction
from pathlib import Path

import psycopg
import pytest
from hypothesis import given
from hypothesis import strategies as st

from mor.audit import Actor, ActorKind
from mor.db.connect import connect
from mor.markets.changes import describe_changes
from mor.markets.load import MARKETS_DIR
from mor.markets.records import (
    Charge,
    MarketSet,
    MarketStatus,
    Percent,
    ResearchStatus,
    content_sha256,
)
from mor.markets.rules import (
    NotListed,
    NotOpen,
    Open,
    RatesMissing,
    RulesNotConfirmed,
    country_rules,
)
from mor.markets.source import SourceError, parse, read_texts
from mor.markets.store import Loaded, Unchanged, load_market, read_market
from tests.markets.fixtures import LANDED_HEADER, SG_GST, market_texts


NOW = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
TODAY = date(2026, 11, 17)
LOADER = Actor(ActorKind.SYSTEM, "test")


def _market(texts: dict[str, str]) -> MarketSet:
    market = parse(texts)
    assert not isinstance(market, list), market
    return market


# --- the files ----------------------------------------------------------------


def test_the_research_files_parse_into_a_complete_set() -> None:
    texts = read_texts(MARKETS_DIR)
    assert not isinstance(texts, list), texts
    market = _market(texts)
    assert len(market.countries) > 200
    assert any(rate.scope.group == "EU" for rate in market.landed_rates)


def test_a_rate_naming_an_unknown_category_is_refused_with_its_line() -> None:
    row = SG_GST.replace("SG,,import_tax,seller", "SG,toys,import_tax,seller", 1)
    errors = parse(market_texts(row))
    assert errors == [SourceError("landed_rates", 2, "unknown category toys")]


def test_a_rate_that_does_not_parse_names_its_column() -> None:
    row = SG_GST.replace("percent,9,", "percent,nine,", 1)
    errors = parse(market_texts(row))
    assert errors == [
        SourceError("landed_rates", 2, "rate_percent 'nine' is not a decimal number")
    ]


@given(text=st.text(max_size=400))
def test_any_landed_rates_text_gives_a_set_or_errors(text: str) -> None:
    result = parse(market_texts("") | {"landed_rates": LANDED_HEADER + text})
    assert isinstance(result, MarketSet | list)


# --- loads --------------------------------------------------------------------


def test_the_research_files_load_once_and_read_back_as_loaded(
    database_url: str,
) -> None:
    texts = read_texts(MARKETS_DIR)
    assert not isinstance(texts, list)
    market = _market(texts)

    async def body() -> None:
        async with await connect(database_url) as conn:
            first = await load_market(conn, market, "abc123", LOADER, NOW)
            await conn.commit()
            again = await load_market(conn, market, "abc123", LOADER, NOW)
            await conn.commit()
            assert isinstance(first, Loaded)
            assert again == Unchanged(first.load_id)
            stored = await read_market(conn, first.load_id)
        assert set(stored.countries) == set(market.countries)
        assert set(stored.groups) == set(market.groups)
        assert set(stored.categories) == set(market.categories)
        assert set(stored.landed_rates) == set(market.landed_rates)
        assert set(stored.fx_rates) == set(market.fx_rates)
        assert set(stored.services) == set(market.services)
        assert set(stored.rate_cards) == set(market.rate_cards)
        assert set(stored.zones) == set(market.zones)
        assert set(stored.rates) == set(market.rates)
        assert set(stored.surcharges) == set(market.surcharges)
        assert set(stored.transit) == set(market.transit)
        assert set(stored.restrictions) == set(market.restrictions)
        assert set(stored.routes) == set(market.routes)
        assert set(stored.fees) == set(market.fees)

    asyncio.run(body())


def test_an_edited_rate_is_a_new_load_showing_only_that_change(
    database_url: str,
) -> None:
    before = _market(market_texts(SG_GST))
    after = _market(market_texts(SG_GST.replace("percent,9,", "percent,10,", 1)))

    async def body() -> None:
        async with await connect(database_url) as conn:
            first = await load_market(conn, before, "one", LOADER, NOW)
            second = await load_market(conn, after, "two", LOADER, NOW)
            await conn.commit()
        assert isinstance(first, Loaded) and isinstance(second, Loaded)
        assert second.previous is not None
        assert second.previous.id == first.load_id

    asyncio.run(body())
    assert describe_changes(before, after) == [
        "~ landed rate SG, every category, import_tax, seller, up_to:"
        " amount 9% of goods_freight_duty -> 10% of goods_freight_duty"
    ]


def test_loaded_rows_cannot_be_changed_in_place(database_url: str) -> None:
    async def body() -> None:
        async with await connect(database_url) as conn:
            await load_market(conn, _market(market_texts(SG_GST)), "one", LOADER, NOW)
            await conn.commit()
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                await conn.execute("UPDATE landed_rate SET source = 'edited'")

    asyncio.run(body())


@given(
    line=st.integers(min_value=0, max_value=1),
    percent=st.integers(min_value=0, max_value=40),
)
def test_editing_one_rate_changes_the_hash_and_reports_one_line(
    line: int, percent: int
) -> None:
    before = _market(market_texts(SG_GST))
    rates = list(before.landed_rates)
    old = rates[line]
    assert isinstance(old.amount, Percent)
    rates[line] = replace(old, amount=replace(old.amount, rate=Fraction(percent, 100)))
    after = replace(before, landed_rates=tuple(rates))
    report = describe_changes(before, after)
    if Fraction(percent, 100) == old.amount.rate:
        assert content_sha256(after) == content_sha256(before)
        assert report == []
    else:
        assert content_sha256(after) != content_sha256(before)
        assert len(report) == 1


# --- what a country's rules are -------------------------------------------------


def test_an_open_country_lists_its_rates_most_specific_first() -> None:
    eu_fee = (
        "EU,,fee,border,always,,,,per_line,,200,EUR,,,verified,EU,https://e.test,,,,\n"
    )
    fr_fee = (
        "FR,apparel,fee,border,always,,,,per_line,,300,EUR,,,verified,FR,"
        "https://f.test,,,,\n"
    )
    fr_vat = (
        "FR,,import_tax,border,always,,,,percent,20,,,goods_freight_duty,,"
        "verified,FR,https://f.test,,,,\n"
    )
    market = _market(market_texts(eu_fee + fr_fee + fr_vat))
    rules = country_rules(market, "FR", "apparel", TODAY)
    assert isinstance(rules, Open)
    places: list[str | None] = []
    for rate in rules.candidates[Charge.FEE]:
        places.append(rate.scope.country or rate.scope.group)
    assert places == ["FR", "EU"]


def test_a_country_not_open_is_closed_with_its_reason() -> None:
    market = _market(market_texts(SG_GST))
    assert country_rules(market, "DE", "apparel", TODAY) == NotOpen(
        MarketStatus.CONDITIONAL, "product-safety rep needed"
    )
    assert country_rules(market, "ZZ", "apparel", TODAY) == NotListed("ZZ")


def test_a_rate_weaker_than_secondary_closes_the_country() -> None:
    estimated = SG_GST.replace(",verified,IRAS", ",estimate,IRAS", 1)
    market = _market(market_texts(estimated))
    assert country_rules(market, "SG", "apparel", TODAY) == RulesNotConfirmed((2,))
    assert market.landed_rates[0].status == ResearchStatus.ESTIMATE


def test_a_rate_entered_ahead_takes_over_on_its_date() -> None:
    later = (
        "SG,,import_tax,seller,up_to,40000,SGD,goods,percent,10,,,"
        "goods_freight_duty,2027-01-01,verified,IRAS,https://example.test/sg,,,,\n"
    )
    market = _market(market_texts(SG_GST + later))

    def seller_rate(on: date) -> Fraction:
        rules = country_rules(market, "SG", "apparel", on)
        assert isinstance(rules, Open)
        for rate in rules.candidates[Charge.IMPORT_TAX]:
            if rate.collector.value == "seller":
                assert isinstance(rate.amount, Percent)
                return rate.amount.rate
        raise AssertionError("no seller rate")

    assert seller_rate(date(2026, 12, 31)) == Fraction(9, 100)
    assert seller_rate(date(2027, 1, 1)) == Fraction(10, 100)


def test_the_paths_point_at_the_repository() -> None:
    assert MARKETS_DIR == Path(__file__).resolve().parents[2] / "docs" / "markets"


def test_a_new_limit_entered_ahead_replaces_the_old_band_on_its_date() -> None:
    lower = (
        "SG,,import_tax,seller,up_to,30000,SGD,goods,percent,9,,,"
        "goods_freight_duty,2027-01-01,verified,IRAS,https://example.test/sg,,,,\n"
    )
    market = _market(market_texts(SG_GST + lower))

    def seller_limits(on: date) -> list[int]:
        rules = country_rules(market, "SG", "apparel", on)
        assert isinstance(rules, Open)
        limits: list[int] = []
        for rate in rules.candidates[Charge.IMPORT_TAX]:
            if rate.collector.value == "seller" and rate.limit is not None:
                limits.append(rate.limit.amount.amount_minor)
        return limits

    assert seller_limits(date(2026, 12, 31)) == [40000]
    assert seller_limits(date(2027, 1, 1)) == [30000]


def test_an_open_country_without_import_tax_rows_is_closed_not_tax_free() -> None:
    market = _market(market_texts(SG_GST))
    assert country_rules(market, "TH", "apparel", TODAY) == RatesMissing("apparel")

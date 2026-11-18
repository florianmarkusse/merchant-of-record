"""The staff markets page (docs/plan-quote.md §5b), step 4a: the loads,
what the newest changed, and each country's answer on a day. Not mounted
in prod until staff sign-in exists."""

import asyncio
from datetime import UTC, datetime

import httpx2

from mor.audit import Actor, ActorKind
from mor.config import Environment, Settings
from mor.db.connect import connect
from mor.markets.source import parse
from mor.markets.store import load_market
from mor.web.main import make_app
from tests.markets.fixtures import SG_GST, TH_BORDER, market_texts


NOW = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
LOADER = Actor(ActorKind.SYSTEM, "test")
AHEAD = (
    "SG,,import_tax,seller,up_to,40000,SGD,goods,percent,10,,,"
    "goods_freight_duty,2027-01-01,verified,IRAS,https://example.test/sg,,,,\n"
)


async def _load(url: str, rows: str, commit: str, hs_rows: str = "") -> None:
    market = parse(market_texts(rows, hs_rows=hs_rows))
    assert not isinstance(market, list)
    async with await connect(url) as conn:
        await load_market(conn, market, commit, LOADER, NOW)
        await conn.commit()


async def _get(url: str, environment: Environment, path: str) -> httpx2.Response:
    app = make_app(Settings(environment, url, "http://mor"), None, lambda: NOW)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport) as client:
        return await client.get(f"http://mor{path}")


def test_the_page_shows_the_loads_what_changed_and_each_answer(
    database_url: str,
) -> None:
    async def body() -> None:
        await _load(database_url, SG_GST, "first")
        await _load(
            database_url, SG_GST.replace("percent,9,", "percent,10,", 1), "second"
        )
        page = await _get(database_url, Environment.TEST, "/staff/markets")
        assert page.status_code == 200
        text = page.text
        assert "second" in text and "first" in text
        assert "9% of goods_freight_duty -&gt; 10% of goods_freight_duty" in text
        assert "conditional: product-safety rep needed" in text
        assert "closed: no import tax recorded for apparel" in text
        assert "import_tax: 10% of goods_freight_duty, seller, up_to 400.00 SGD" in text

    asyncio.run(body())


def test_the_day_asked_for_picks_the_rule_in_force(database_url: str) -> None:
    async def body() -> None:
        await _load(database_url, SG_GST + AHEAD, "ahead")
        before = await _get(
            database_url, Environment.TEST, "/staff/markets?on=2026-12-31"
        )
        after = await _get(
            database_url, Environment.TEST, "/staff/markets?on=2027-01-01"
        )
        assert "import_tax: 9% of goods_freight_duty, seller" in before.text
        assert "import_tax: 10% of goods_freight_duty, seller" in after.text
        assert "import_tax: 9% of goods_freight_duty, seller" not in after.text

    asyncio.run(body())


def test_the_page_does_not_exist_in_prod(database_url: str) -> None:
    async def body() -> None:
        page = await _get(database_url, Environment.PROD, "/staff/markets")
        assert page.status_code == 404

    asyncio.run(body())


TH_HS = (
    "TH,6109,duty,,,,30,,secondary,WTO,https://example.test,,,\n"
    "TH,6109,duty,VN,ATIGA,certificate_of_origin direct_shipment,0,,"
    "unverified,ASEAN,https://example.test,,,\n"
)


def test_an_order_check_explains_both_paths_and_the_rates(database_url: str) -> None:
    async def body() -> None:
        await _load(database_url, SG_GST + TH_BORDER, "one", TH_HS)
        query = (
            "/staff/markets?country=TH&goods=50.00&currency=USD&hs=6109.10&origin=VN"
        )
        text = (await _get(database_url, Environment.TEST, query)).text
        assert "VAT/GST: charged at the border" in text
        assert "Duty: charged at the border" in text
        assert "Rate 30% of goods_plus_freight: hs_rates.csv line 2" in text
        assert "Not applied: ATIGA 0%" in text
        assert "Only duty-paid services may carry this order." in text

    asyncio.run(body())


def test_an_order_check_with_a_bad_amount_says_so(database_url: str) -> None:
    async def body() -> None:
        await _load(database_url, SG_GST, "one")
        query = "/staff/markets?country=SG&goods=lots&currency=SGD"
        text = (await _get(database_url, Environment.TEST, query)).text
        assert "Goods value: not a number." in text

    asyncio.run(body())

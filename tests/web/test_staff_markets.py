"""The staff markets page (docs/plan-quote.md §5b): the loads, what the
newest changed, each country's answer on a day (4a), and the order check
with our own rows or a vendor's answer (4b). Not mounted in prod until
staff sign-in exists."""

import asyncio
import html
import re
from datetime import UTC, datetime, timedelta

import httpx2

from mor.audit import Actor, ActorKind
from mor.config import Environment, Settings
from mor.couriers.prices import create_courier_account
from mor.couriers.records import CourierProvider, PriceExpiry
from mor.db.connect import connect
from mor.duties.lookups import create_vendor_account
from mor.duties.port import DutyProvider
from mor.markets.source import parse
from mor.markets.store import load_market
from mor.messaging.worker import drain_once
from mor.provider_accounts import ProviderAccountKind
from mor.web.main import make_app
from tests.helpers import ENV, wire
from tests.markets.fixtures import SG_GST, TH_BORDER, market_texts
from tests.quote.fixtures import live_market, quote_market


NOW = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
LOADER = Actor(ActorKind.SYSTEM, "test")
AHEAD = (
    "SG,,import_tax,seller,up_to,40000,SGD,goods,percent,10,,,"
    "goods_freight_duty,2027-01-01,verified,IRAS,https://example.test/sg,,,,\n"
)


async def _load(
    url: str, rows: str, commit: str, hs_rows: str = "", duty_sources: str = ""
) -> None:
    market = parse(market_texts(rows, hs_rows=hs_rows, duty_sources=duty_sources))
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
        page = await _get(database_url, Environment.TEST, "/staff/markets/data")
        assert page.status_code == 200
        text = page.text
        assert "second" in text and "first" in text
        assert "9% of goods_freight_duty -&gt; 10% of goods_freight_duty" in text
        assert "conditional: product-safety rep needed" in text
        assert "no import tax recorded for apparel" in text
        assert "import_tax: 10% of goods_freight_duty, seller, up_to 400.00 SGD" in text

    asyncio.run(body())


def test_the_day_asked_for_picks_the_rule_in_force(database_url: str) -> None:
    async def body() -> None:
        await _load(database_url, SG_GST + AHEAD, "ahead")
        before = await _get(
            database_url, Environment.TEST, "/staff/markets/data?on=2026-12-31"
        )
        after = await _get(
            database_url, Environment.TEST, "/staff/markets/data?on=2027-01-01"
        )
        assert "import_tax: 9% of goods_freight_duty, seller" in before.text
        assert "import_tax: 10% of goods_freight_duty, seller" in after.text
        assert "import_tax: 9% of goods_freight_duty, seller" not in after.text
        # The day as people read it, beside the browser's own date picker.
        assert "31-Dec-2026" in before.text

    asyncio.run(body())


def test_the_page_does_not_exist_in_prod(database_url: str) -> None:
    async def body() -> None:
        for path in ("/staff/markets", "/staff/markets/answers", "/staff/markets/data"):
            page = await _get(database_url, Environment.PROD, path)
            assert page.status_code == 404, path

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
        said = _words(text)
        assert "charges VAT/GST and duty at the border on this order" in said
        assert "only duty-paid services can carry the order" in said
        assert "Duty at the border, on every order" in said
        assert "Duty at the border, on every order 30% of the price with fees" in said
        assert "not applied: needs certificate of origin" in said
        assert "our own row for HS 6109" in said
        assert "hs_rates.csv line 2 HS 6109: 30% secondary" in said
        # "Made in" is a list of the countries file, the order's one chosen.
        assert '<option value="VN" selected>' in text

    asyncio.run(body())


def test_an_order_check_with_a_bad_amount_says_so(database_url: str) -> None:
    async def body() -> None:
        await _load(database_url, SG_GST, "one")
        query = "/staff/markets?country=SG&goods=lots&currency=SGD"
        text = (await _get(database_url, Environment.TEST, query)).text
        # Said beside the field it is about.
        assert (
            '<span id="error-goods" class="error" data-live="">'
            "Enter a price (not a number).</span>"
        ) in text

    asyncio.run(body())


CHECK = "/staff/markets?country=TH&goods=50.00&currency=USD&hs=6109.10&origin=VN"


def test_a_vendor_country_is_asked_from_the_page_and_its_answer_shown(
    database_url: str,
) -> None:
    async def body() -> None:
        await _load(database_url, SG_GST + TH_BORDER, "one", TH_HS, "TH,vendor,acme,\n")
        wired = wire(database_url, lambda: NOW)
        async with await connect(database_url) as conn:
            await create_vendor_account(
                conn,
                "acme",
                DutyProvider.FAKE,
                ProviderAccountKind.FAKE,
                "http://mor/_fake/duty/acct",
                timedelta(seconds=5),
            )
            await conn.commit()
        rate = await wired.control.post(
            "http://mor/_fake/duty/rates",
            json={"destination": "TH", "hs_prefix": "61", "duty": "25%", "tax": "7%"},
        )
        assert rate.status_code == 204

        before = (await wired.control.get(f"http://mor{CHECK}")).text
        assert "Rates per HS code come from acme" in _words(before)
        assert "acme has not been asked about HS 610910 made in VN" in before
        assert "Ask acme" in before

        asked = await wired.control.post(
            "http://mor/staff/markets/duty_lookups",
            data={"country": "TH", "origin": "VN", "hs": "610910", "back": CHECK},
            headers={"origin": "http://mor"},
        )
        assert asked.status_code == 303
        assert asked.headers["location"].startswith(CHECK)
        waiting = (await wired.control.get(f"http://mor{CHECK}")).text
        assert "waiting for the answer" in waiting and "Ask acme" not in waiting

        async with await connect(database_url, autocommit=True) as worker:
            await drain_once(worker, wired.http, ENV, NOW)
        after = (await wired.control.get(f"http://mor{CHECK}")).text
        said = _words(after)
        assert (
            "25% of the price with fees + freight acme, for HS 610910 made in VN"
            " 30%: differs (hs_rates.csv line 2)"
        ) in said
        assert "Check the duty rate: our own data differs" in said
        assert "acme duty for HS 610910: '25%'" in said
        answers = (await wired.control.get("http://mor/staff/markets/answers")).text
        # Each answer listed beside our row, the difference first.
        listed = answers[answers.index("Each answer") :]
        assert listed.index("acme: 25%") < listed.index("acme: 7%")
        assert "30% (hs_rates.csv line 2)" in listed

    asyncio.run(body())


def test_asking_a_vendor_from_another_site_is_refused(database_url: str) -> None:
    async def body() -> None:
        await _load(database_url, SG_GST + TH_BORDER, "one", TH_HS, "TH,vendor,acme,\n")
        wired = wire(database_url, lambda: NOW)
        refused = await wired.control.post(
            "http://mor/staff/markets/duty_lookups",
            data={"country": "TH", "origin": "VN", "hs": "610910", "back": CHECK},
            headers={"origin": "http://evil.example"},
        )
        assert refused.status_code == 403
        async with await connect(database_url) as conn:
            cur = await conn.execute("SELECT count(*) FROM duty_lookup")
            row = await cur.fetchone()
        assert row == (0,)

    asyncio.run(body())


def test_with_a_parcel_the_check_shows_each_option_and_its_parts(
    database_url: str,
) -> None:
    async def body() -> None:
        async with await connect(database_url) as conn:
            await load_market(conn, quote_market(), "quote", LOADER, NOW)
            await conn.commit()
        page = await _get(
            database_url,
            Environment.TEST,
            "/staff/markets?country=SG&goods=500.00&currency=SGD&hs=610910"
            "&origin=VN&weight=1000&length=200&width=150&height=100",
        )
        text = page.text
        # The worked example of tests/quote/test_build_quote.py.
        # The options on one line each, then how the chosen one is built.
        assert 'data-option="exp_card" aria-current="true">Express</a>' in text
        assert "<h2>How Express comes to 649.28 SGD</h2>" in text
        assert "= Buyer pays" in text
        assert "fuel rate ended 15-Nov-2026" in text
        # Beside the quote's own currency, for reading (ECB rates in the set).
        assert "≈ 506.62 USD · 453.76 EUR" in text
        assert (
            '<code>eco_card</code></td><td colspan="5">Not offered: not duty-paid'
            in text
        )
        assert "fees.csv line 2 (psp_fee)" in text
        assert "52.78 SGD" in text and "half to even" in text
        assert "/static/css/staff.css" in text

    asyncio.run(body())


def _words(page: str) -> str:
    """A page's text as a person reads it: tags gone, spaces collapsed."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    return " ".join(text.split())


def _error(text: str, name: str) -> str:
    """What the page says beside one field ("" when nothing)."""
    start = text.index(f'<span id="error-{name}"')
    start = text.index(">", start) + 1
    return html.unescape(text[start : text.index("</span>", start)])


def test_pressing_quote_marks_each_field_a_quote_still_needs(
    database_url: str,
) -> None:
    async def body() -> None:
        async with await connect(database_url) as conn:
            await load_market(conn, quote_market(), "quote", LOADER, NOW)
            await conn.commit()
        empty = (await _get(database_url, Environment.TEST, "/staff/markets?go=1")).text
        assert _error(empty, "country") == "Choose where it ships to."
        assert _error(empty, "goods") == "Enter the merchant's price."
        assert _error(empty, "weight") == "Enter the parcel's weight to see shipping."
        marked = "Fix the fields marked above: Ship to, Merchant's price, Weight."
        assert marked in html.unescape(empty)
        # The HS code once the country's rates turn out to need it.
        no_hs = "/staff/markets?country=TH&goods=50.00&currency=USD&weight=1000&go=1"
        text = (await _get(database_url, Environment.TEST, no_hs)).text
        assert _error(text, "hs") == "This country's rates need the HS code."
        assert _error(text, "weight") == ""

    asyncio.run(body())


def test_while_typing_an_empty_field_is_not_marked(database_url: str) -> None:
    async def body() -> None:
        await _load(database_url, SG_GST, "one")
        query = "/staff/markets?country=SG&currency=SGD"
        text = (await _get(database_url, Environment.TEST, query)).text
        assert _error(text, "goods") == ""
        assert _error(text, "weight") == ""

    asyncio.run(body())


def test_an_option_named_in_the_address_is_the_one_explained(
    database_url: str,
) -> None:
    async def body() -> None:
        async with await connect(database_url) as conn:
            await load_market(conn, quote_market(), "quote", LOADER, NOW)
            await conn.commit()
        # Under the GST band, nothing is due at the border: both cards carry.
        order = (
            "/staff/markets?country=SG&goods=50.00&currency=SGD&hs=610910"
            "&origin=VN&weight=1000"
        )
        cheapest = (await _get(database_url, Environment.TEST, order)).text
        chosen = (
            await _get(database_url, Environment.TEST, order + "&option=exp_card")
        ).text
        unknown = (
            await _get(database_url, Environment.TEST, order + "&option=gone")
        ).text
        assert 'data-option="eco_card" aria-current="true"' in cheapest
        assert "<h2>How Economy comes to" in cheapest
        assert "none due on this order" in cheapest
        assert 'data-option="exp_card" aria-current="true"' in chosen
        assert "<h2>How Express comes to" in chosen
        # A card no longer offered falls back to the cheapest.
        assert "<h2>How Economy comes to" in unknown

    asyncio.run(body())


LIVE_QUOTE = (
    "/staff/markets?country=SG&goods=500.00&currency=SGD&hs=610910"
    "&origin=VN&weight=1000&length=200&width=150&height=100"
)


def test_a_live_route_is_priced_by_asking_the_carrier_from_the_page(
    database_url: str,
) -> None:
    async def body() -> None:
        async with await connect(database_url) as conn:
            await load_market(conn, live_market(), "live", LOADER, NOW)
            await create_courier_account(
                conn,
                "carrier",
                CourierProvider.FAKE,
                ProviderAccountKind.FAKE,
                "http://mor/_fake/courier/acct",
                timedelta(seconds=5),
                timedelta(hours=24),
                PriceExpiry.CARRIER_FIRST,
            )
            await conn.commit()
        wired = wire(database_url, lambda: NOW)

        before = (await wired.control.get(f"http://mor{LIVE_QUOTE}")).text
        assert "No usable carrier price for this parcel." in before
        assert "Ask carrier for exp" in before

        asked = await wired.control.post(
            "http://mor/staff/markets/shipping_prices",
            data={
                "country": "SG",
                "service": "exp",
                "weight": "1000",
                "length": "200",
                "width": "150",
                "height": "100",
                "back": LIVE_QUOTE,
            },
            headers={"origin": "http://mor"},
        )
        assert asked.status_code == 303
        waiting = (await wired.control.get(f"http://mor{LIVE_QUOTE}")).text
        assert "Waiting for the carrier." in waiting

        async with await connect(database_url, autocommit=True) as worker:
            await drain_once(worker, wired.http, ENV, NOW)
        after = (await wired.control.get(f"http://mor{LIVE_QUOTE}")).text
        assert 'data-option="live:exp"' in after
        assert "carrier&#39;s price, used until 18-Nov-2026 09:30 UTC" in after
        # Our exp_card for the same parcel, in the carrier's currency.
        assert "our card" in after and "carrier rate call" in after
        answers = (await wired.control.get("http://mor/staff/markets/answers")).text
        listed = answers[answers.index("Live carrier prices") :]
        assert "carrier exp" in listed and "(exp_card)" in listed

    asyncio.run(body())


def test_asking_a_carrier_from_another_site_is_refused(database_url: str) -> None:
    async def body() -> None:
        async with await connect(database_url) as conn:
            await load_market(conn, live_market(), "live", LOADER, NOW)
            await conn.commit()
        wired = wire(database_url, lambda: NOW)
        refused = await wired.control.post(
            "http://mor/staff/markets/shipping_prices",
            data={
                "country": "SG",
                "service": "exp",
                "weight": "1000",
                "length": "200",
                "width": "150",
                "height": "100",
                "back": LIVE_QUOTE,
            },
            headers={"origin": "http://evil.example"},
        )
        assert refused.status_code == 403
        async with await connect(database_url) as conn:
            cur = await conn.execute("SELECT count(*) FROM shipping_price_lookup")
            row = await cur.fetchone()
        assert row == (0,)

    asyncio.run(body())


def test_a_carrier_without_an_account_here_is_not_offered_to_ask(
    database_url: str,
) -> None:
    async def body() -> None:
        async with await connect(database_url) as conn:
            await load_market(conn, live_market(), "live", LOADER, NOW)
            await conn.commit()
        page = (await _get(database_url, Environment.TEST, LIVE_QUOTE)).text
        assert "There is no carrier account in this environment" in page
        assert "Ask carrier for exp" not in page

    asyncio.run(body())

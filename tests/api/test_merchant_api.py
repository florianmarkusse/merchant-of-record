"""The merchant API through its edge (docs/plan-quote.md §5, 4d-1):
requests in, responses and rows out, on the synthetic quote market.
Every merchant, key and line here is made up."""

import asyncio
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import LiteralString

import httpx2

from mor.audit import Actor, ActorKind
from mor.config import Environment, Settings
from mor.couriers.prices import create_courier_account
from mor.couriers.records import CourierProvider, PriceExpiry
from mor.db.connect import connect
from mor.markets.records import MarketSet
from mor.markets.store import load_market
from mor.merchants.records import KeyEnv, Merchant
from mor.merchants.store import create_api_key, create_merchant, revoke_api_key
from mor.messaging.worker import drain_once
from mor.web.main import make_app
from tests.helpers import ENV, VENDOR_TIMEOUT, wire
from tests.quote.fixtures import live_market, quote_market


NOW = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
ACTOR = Actor(ActorKind.SYSTEM, "test")
PARCEL = {"weight_g": 1000, "length_mm": 200, "width_mm": 150, "height_mm": 100}
LINE = {
    "name": "Linen shirt",
    "quantity": 2,
    "unit_price": {"amount_minor": 2500, "currency": "SGD"},
    "category": "apparel",
    "customs": {
        "description": "Men's linen shirt, woven",
        "origin_country": "VN",
        "weight_g": 300,
        "hs_code": "610910",
    },
}
NO_HS_LINE = LINE | {
    "customs": {
        "description": "Men's linen shirt, woven",
        "origin_country": "VN",
        "weight_g": 300,
    }
}
SESSION = {
    "lines": [LINE],
    "parcel": PARCEL,
    "return_url": "https://shop.example/thanks",
    "cancel_url": "https://shop.example/cart",
    "merchant_reference": "order-1042",
}


@dataclass(frozen=True)
class Shop:
    merchant: Merchant
    key: str


async def _shop(
    url: str,
    market: MarketSet | None = None,
    daily_vendor_asks: int = 100,
    name: str = "Test Shop",
) -> Shop:
    async with await connect(url) as conn:
        if market is not None:
            await load_market(conn, market, "test", ACTOR, NOW)
        merchant = await create_merchant(
            conn, f"{name} Co.", name, "VN", daily_vendor_asks, ACTOR, NOW
        )
        key = await create_api_key(conn, merchant.id, KeyEnv.TEST, NOW)
        await conn.commit()
    return Shop(merchant, key.text)


async def _call(
    url: str,
    method: str,
    path: str,
    key: str | None,
    body: object = None,
    idempotency_key: str | None = None,
    environment: Environment = Environment.TEST,
    now: datetime = NOW,
) -> httpx2.Response:
    app = make_app(Settings(environment, url, "http://mor"), None, lambda: now)
    headers: dict[str, str] = {}
    if key is not None:
        headers["authorization"] = f"Bearer {key}"
    if idempotency_key is not None:
        headers["idempotency-key"] = idempotency_key
    content = b""
    if isinstance(body, bytes):
        content = body
    elif body is not None:
        content = json.dumps(body).encode()
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport) as client:
        return await client.request(
            method, f"http://mor{path}", headers=headers, content=content
        )


async def _count(url: str, sql: LiteralString) -> int:
    async with await connect(url) as conn:
        cur = await conn.execute(sql)
        row = await cur.fetchone()
    assert row is not None
    return row[0]


def _error(response: httpx2.Response) -> dict[str, str | None]:
    body = response.json()
    assert set(body) == {"error"}, body
    error = body["error"]
    assert set(error) == {"type", "code", "message", "param"}, error
    return error


def test_every_failed_authentication_gets_the_same_answer(database_url: str) -> None:
    async def body() -> None:
        shop = await _shop(database_url, quote_market())
        async with await connect(database_url) as conn:
            live = await create_api_key(conn, shop.merchant.id, KeyEnv.LIVE, NOW)
            revoked = await create_api_key(conn, shop.merchant.id, KeyEnv.TEST, NOW)
            await revoke_api_key(conn, revoked.id, NOW)
            await conn.commit()
        parts = shop.key.split("_")
        wrong_secret = "_".join(parts[:3] + ["a" * len(parts[3])])
        unknown_id = "_".join(parts[:2] + ["aaaaaaaaaaaaa", parts[3]])
        answers: list[tuple[int, bytes]] = []
        for key in (
            None,
            "nonsense",
            wrong_secret,
            unknown_id,
            live.text,
            revoked.text,
        ):
            response = await _call(
                database_url, "GET", f"/v1/checkout_sessions/cs_{'0' * 32}", key
            )
            answers.append((response.status_code, response.content))
        assert len(set(answers)) == 1, answers
        status, content = answers[0]
        assert status == 401
        assert json.loads(content)["error"]["code"] == "authentication_failed"

    asyncio.run(body())


def test_a_session_is_stored_as_sent_and_read_back(database_url: str) -> None:
    async def body() -> None:
        shop = await _shop(database_url, quote_market())
        created = await _call(
            database_url, "POST", "/v1/checkout_sessions", shop.key, SESSION, "k-1"
        )
        assert created.status_code == 201, created.text
        session = created.json()
        assert session["id"].startswith("cs_")
        assert session["state"] == "open"
        assert session["url"] == f"http://mor/c/{session['id']}"
        assert session["merchant"] == f"mer_{shop.merchant.id.hex}"
        assert session["created_at"] == "2026-11-18T09:30:00Z"
        assert session["expires_at"] == "2026-11-19T09:30:00Z"
        assert session["lines"] == [LINE | {"merchant_sku": None}]
        assert session["parcel"] == PARCEL
        assert session["merchant_reference"] == "order-1042"

        read = await _call(
            database_url, "GET", f"/v1/checkout_sessions/{session['id']}", shop.key
        )
        assert read.status_code == 200
        assert read.json() == session
        assert await _count(database_url, "SELECT count(*) FROM checkout_session") == 1
        facts = await _count(
            database_url,
            "SELECT count(*) FROM outbox"
            " WHERE message_type = 'checkout_session_created'",
        )
        assert facts == 1

    asyncio.run(body())


def test_another_merchants_session_does_not_exist_for_you(database_url: str) -> None:
    async def body() -> None:
        shop = await _shop(database_url, quote_market())
        other = await _shop(database_url, name="Other Shop")
        created = await _call(
            database_url, "POST", "/v1/checkout_sessions", shop.key, SESSION, "k-1"
        )
        session_id = created.json()["id"]
        for key, path in (
            (other.key, f"/v1/checkout_sessions/{session_id}"),
            (shop.key, f"/v1/checkout_sessions/cs_{uuid.uuid7().hex}"),
            (shop.key, "/v1/checkout_sessions/not-an-id"),
        ):
            response = await _call(database_url, "GET", path, key)
            assert response.status_code == 404
            assert _error(response)["code"] == "not_found"

    asyncio.run(body())


def test_the_same_key_and_request_replays_the_first_answer(database_url: str) -> None:
    async def body() -> None:
        shop = await _shop(database_url, quote_market())
        first = await _call(
            database_url, "POST", "/v1/checkout_sessions", shop.key, SESSION, "k-1"
        )
        # The same request in other whitespace and key order.
        reordered = json.dumps(dict(reversed(list(SESSION.items()))), indent=2)
        again = await _call(
            database_url,
            "POST",
            "/v1/checkout_sessions",
            shop.key,
            reordered.encode(),
            "k-1",
            now=NOW + timedelta(days=400),
        )
        assert again.status_code == 201
        assert again.content == first.content
        assert await _count(database_url, "SELECT count(*) FROM checkout_session") == 1

    asyncio.run(body())


def test_the_same_key_for_another_request_is_refused(database_url: str) -> None:
    async def body() -> None:
        shop = await _shop(database_url, quote_market())
        await _call(
            database_url, "POST", "/v1/checkout_sessions", shop.key, SESSION, "k-1"
        )
        changed = SESSION | {"merchant_reference": "order-1043"}
        response = await _call(
            database_url, "POST", "/v1/checkout_sessions", shop.key, changed, "k-1"
        )
        assert response.status_code == 409
        assert _error(response)["code"] == "idempotency_key_reused"
        # Keys are per merchant: another merchant may use the same one.
        other = await _shop(database_url, name="Other Shop")
        theirs = await _call(
            database_url, "POST", "/v1/checkout_sessions", other.key, changed, "k-1"
        )
        assert theirs.status_code == 201
        assert await _count(database_url, "SELECT count(*) FROM checkout_session") == 2

    asyncio.run(body())


def test_a_refused_request_leaves_its_key_free(database_url: str) -> None:
    async def body() -> None:
        shop = await _shop(database_url, quote_market())
        wrong = SESSION | {"lines": [LINE | {"category": "spaceships"}]}
        refused = await _call(
            database_url, "POST", "/v1/checkout_sessions", shop.key, wrong, "k-1"
        )
        assert refused.status_code == 400
        error = _error(refused)
        assert error["code"] == "unknown_category"
        assert error["param"] == "lines[0].category"
        fixed = await _call(
            database_url, "POST", "/v1/checkout_sessions", shop.key, SESSION, "k-1"
        )
        assert fixed.status_code == 201

    asyncio.run(body())


def test_bad_requests_name_what_is_wrong(database_url: str) -> None:
    async def body() -> None:
        shop = await _shop(database_url, quote_market())
        cases: list[tuple[object, str | None, str, str | None]] = [
            (SESSION, None, "idempotency_key_missing", None),
            (b"{not json", "k", "invalid_body", None),
            (
                SESSION | {"lines": [LINE | {"quantity": 0}]},
                "k",
                "invalid_field",
                "lines[0].quantity",
            ),
            (
                SESSION | {"lines": [LINE | {"tax_behavior": "inclusive"}]},
                "k",
                "invalid_field",
                "lines[0]",
            ),
            (
                SESSION
                | {
                    "lines": [
                        LINE | {"unit_price": {"amount_minor": 1, "currency": "XYZ"}}
                    ]
                },
                "k",
                "currency_not_supported",
                "lines[0].unit_price",
            ),
            (SESSION | {"lines": []}, "k", "invalid_field", "lines"),
        ]
        for request, key, code, param in cases:
            response = await _call(
                database_url, "POST", "/v1/checkout_sessions", shop.key, request, key
            )
            assert response.status_code == 400, (code, response.text)
            error = _error(response)
            assert error["type"] == "invalid_request"
            assert (error["code"], error["param"]) == (code, param), error
        assert await _count(database_url, "SELECT count(*) FROM checkout_session") == 0
        assert await _count(database_url, "SELECT count(*) FROM api_idempotency") == 0

    asyncio.run(body())


def test_two_requests_at_once_with_one_key_make_one_session(
    database_url: str,
) -> None:
    async def body() -> None:
        shop = await _shop(database_url, quote_market())
        responses = await asyncio.gather(
            *(
                _call(
                    database_url,
                    "POST",
                    "/v1/checkout_sessions",
                    shop.key,
                    SESSION,
                    "k-1",
                )
                for _ in range(5)
            )
        )
        assert {response.status_code for response in responses} == {201}
        assert len({response.content for response in responses}) == 1
        assert await _count(database_url, "SELECT count(*) FROM checkout_session") == 1

    asyncio.run(body())


def _estimate(country: str, line: Mapping[str, object] = LINE) -> dict[str, object]:
    return {"lines": [line], "parcel": PARCEL, "country": country}


def test_an_estimate_lists_each_option_with_parts_that_sum_to_its_total(
    database_url: str,
) -> None:
    async def body() -> None:
        shop = await _shop(database_url, quote_market())
        response = await _call(
            database_url, "POST", "/v1/estimates", shop.key, _estimate("SG")
        )
        assert response.status_code == 200, response.text
        estimate = response.json()
        assert estimate["currency"] == "SGD"
        assert len(estimate["options"]) == 2
        for option in estimate["options"]:
            total = 0
            groups: set[str] = set()
            for part in option["parts"]:
                assert part["amount"]["currency"] == "SGD"
                total += part["amount"]["amount_minor"]
                groups.add(part["group"])
            assert total == option["total"]["amount_minor"]
            assert {"goods", "fees", "shipping"} <= groups
        # Outside prod the placeholder fee rules are answered and named.
        assert estimate["placeholders"]
        assert await _count(database_url, "SELECT count(*) FROM checkout_session") == 0

    asyncio.run(body())


def test_prod_does_not_estimate_on_a_placeholder_fee(database_url: str) -> None:
    async def body() -> None:
        shop = await _shop(database_url, quote_market())
        async with await connect(database_url) as conn:
            live = await create_api_key(conn, shop.merchant.id, KeyEnv.LIVE, NOW)
            await conn.commit()
        response = await _call(
            database_url,
            "POST",
            "/v1/estimates",
            live.text,
            _estimate("SG"),
            environment=Environment.PROD,
        )
        assert response.status_code == 503
        assert _error(response)["code"] == "pricing_unavailable"

    asyncio.run(body())


def test_an_estimate_is_refused_with_the_reason(database_url: str) -> None:
    async def body() -> None:
        market = quote_market(duty_sources="TH,vendor,acme,\n")
        shop = await _shop(database_url, market)
        cases = (
            (_estimate("ZZ"), 422, "country_not_supported", "country"),
            (_estimate("TH", NO_HS_LINE), 422, "classification_needed", None),
        )
        for request, status, code, param in cases:
            response = await _call(
                database_url, "POST", "/v1/estimates", shop.key, request
            )
            assert response.status_code == status, response.text
            error = _error(response)
            assert error["code"] == code
            if param is not None:
                assert error["param"] == param
        assert await _count(database_url, "SELECT count(*) FROM duty_lookup") == 0

    asyncio.run(body())


def test_a_live_price_is_fetched_once_while_the_merchant_waits(
    database_url: str,
) -> None:
    async def body() -> None:
        shop = await _shop(database_url, live_market())
        wired = wire(database_url, lambda: NOW)
        async with await connect(database_url) as conn:
            await create_courier_account(
                conn,
                "carrier",
                CourierProvider.FAKE,
                "http://mor/_fake/courier/acct",
                VENDOR_TIMEOUT,
                timedelta(hours=24),
                PriceExpiry.CARRIER_FIRST,
            )
            await conn.commit()
        try:
            for _ in range(2):
                pending = await _call(
                    database_url, "POST", "/v1/estimates", shop.key, _estimate("SG")
                )
                assert pending.status_code == 503, pending.text
                assert pending.headers["retry-after"] == "2"
                assert _error(pending)["code"] == "estimate_pending"
            # Asked once, counted once.
            lookups = "SELECT count(*) FROM shipping_price_lookup"
            assert await _count(database_url, lookups) == 1
            asks = "SELECT coalesce(sum(asks), 0) FROM merchant_vendor_asks"
            assert await _count(database_url, asks) == 1
            async with await connect(database_url, autocommit=True) as worker:
                await drain_once(worker, wired.http, ENV, NOW)
            ready = await _call(
                database_url, "POST", "/v1/estimates", shop.key, _estimate("SG")
            )
            assert ready.status_code == 200, ready.text
            keys = [option["key"] for option in ready.json()["options"]]
            assert "live:exp" in keys
        finally:
            await wired.http.aclose()
            await wired.control.aclose()

    asyncio.run(body())


def test_new_lookups_over_the_daily_limit_are_not_asked(database_url: str) -> None:
    async def body() -> None:
        shop = await _shop(database_url, live_market(), daily_vendor_asks=0)
        async with await connect(database_url) as conn:
            await create_courier_account(
                conn,
                "carrier",
                CourierProvider.FAKE,
                "http://mor/_fake/courier/acct",
                VENDOR_TIMEOUT,
                timedelta(hours=24),
                PriceExpiry.CARRIER_FIRST,
            )
            await conn.commit()
        response = await _call(
            database_url, "POST", "/v1/estimates", shop.key, _estimate("SG")
        )
        assert response.status_code == 429
        assert _error(response)["code"] == "rate_limited"
        # Until the next UTC midnight: 14 h 30 min after 09:30.
        assert response.headers["retry-after"] == str(14 * 3600 + 30 * 60)
        lookups = "SELECT count(*) FROM shipping_price_lookup"
        assert await _count(database_url, lookups) == 0

    asyncio.run(body())

"""The fake carrier's tracking: scripts released on request, signed pushes
and the tracking page per label (docs/plan-shipments.md §3)."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx2
import msgspec

from mor.fakes.carrier import (
    SIGNATURE_HEADER,
    FatWebhook,
    ThinWebhook,
    make_app,
    sign,
)
from mor.fakes.hang import hang_a_minute
from tests.helpers import NO_HTTP


NOW = datetime(2027, 1, 1, 9, 0, tzinfo=UTC)
A = "/_fake/carrier/keyed/acct"
SECRET = "whsec-test"


def _client(
    database_url: str, outgoing: httpx2.AsyncClient = NO_HTTP
) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(
            app=make_app(database_url, outgoing, lambda: NOW, hang_a_minute)
        ),
        base_url="http://fake",
    )


def _receiving(
    received: list[httpx2.Request], statuses: list[int]
) -> httpx2.AsyncClient:
    """Our side as the fake sees it: records each push and answers the
    next planned status, then 200."""

    def answer(request: httpx2.Request) -> httpx2.Response:
        received.append(request)
        if statuses:
            return httpx2.Response(statuses.pop(0))
        return httpx2.Response(200)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(answer))


def _party(line1: str, country: str) -> dict[str, str | None]:
    return {
        "name": "Lan Nguyen",
        "email": "lan@example.test",
        "phone": "+6500000000",
        "line1": line1,
        "line2": None,
        "city": "Singapore",
        "region": None,
        "postcode": "018956",
        "country": country,
    }


def _book_body(line1: str) -> dict[str, object]:
    return {
        "reference": "shp-1",
        "service": "FAKE-EXPRESS",
        "shipper": _party("2 Workshop Lane", "VN"),
        "receiver": _party(line1, "SG"),
        "parcel": {"weight_g": 400, "length_mm": 300, "width_mm": 200, "height_mm": 50},
        "incoterm": "dap",
        "items": [
            {
                "description": "Cotton shirt",
                "hs_code": "620520",
                "origin": "VN",
                "quantity": 1,
                "value_minor": 2500,
                "currency": "SGD",
            }
        ],
    }


async def _book(http: httpx2.AsyncClient, line1: str = "1 Test Street") -> str:
    response = await http.post(
        f"{A}/shipments",
        json=_book_body(line1),
        headers={"Idempotency-Key": f"key-{line1}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["tracking_number"]


async def _release(
    http: httpx2.AsyncClient, tracking_number: str, **extra: object
) -> httpx2.Response:
    return await http.post(
        f"{A}/events/release", json={"tracking_number": tracking_number, **extra}
    )


async def _codes(http: httpx2.AsyncClient, tracking_number: str) -> list[str]:
    page = await http.get(f"{A}/tracking?tracking_number={tracking_number}")
    codes: list[str] = []
    for event in page.json()["events"]:
        codes.append(event["code"])
    return codes


def test_a_label_plays_its_script_one_event_at_a_time(database_url: str) -> None:
    async def scenario() -> None:
        async with _client(database_url) as http:
            tracking_number = await _book(http)
            for _ in range(6):
                released = await _release(http, tracking_number)
                assert released.status_code == 200
                assert released.json()["released"][0]["push"] == "not_subscribed"
            done = await _release(http, tracking_number)
            page = await http.get(f"{A}/tracking?tracking_number={tracking_number}")
        assert done.status_code == 409
        assert done.json()["error"] == "script_done"
        codes: list[str] = []
        occurred: list[datetime] = []
        for event in page.json()["events"]:
            codes.append(event["code"])
            occurred.append(datetime.fromisoformat(event["occurred_at"]))
        assert codes == [
            "picked_up",
            "in_transit",
            "customs_hold",
            "customs_released",
            "out_for_delivery",
            "delivered",
        ]
        # When it happened is fixed by the script, not by the release.
        assert occurred[0] - timedelta(hours=2) == occurred[1] - timedelta(hours=4)
        assert occurred == sorted(occurred)

    asyncio.run(scenario())


def test_the_address_line_picks_the_script(database_url: str) -> None:
    async def scenario() -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        async with _client(database_url) as http:
            for line1 in ["LOST 3 Street", "RETURN 4 Street", "WITHDRAW 5 Street"]:
                tracking_number = await _book(http, line1)
                await _release(http, tracking_number, all=True)
                found[line1.split()[0]] = await _codes(http, tracking_number)
        return found

    assert asyncio.run(scenario()) == {
        "LOST": ["picked_up", "in_transit", "lost"],
        "RETURN": [
            "picked_up",
            "in_transit",
            "customs_hold",
            "return_started",
            "returned",
        ],
        "WITHDRAW": [
            "picked_up",
            "in_transit",
            "delivered",
            "delivery_corrected",
            "out_for_delivery",
            "delivered",
        ],
    }


def test_a_step_released_out_of_turn_arrives_before_earlier_ones(
    database_url: str,
) -> None:
    async def scenario() -> None:
        async with _client(database_url) as http:
            tracking_number = await _book(http)
            await _release(http, tracking_number, step=2)
            again = await _release(http, tracking_number, step=2)
            await _release(http, tracking_number)
            page = await http.get(f"{A}/tracking?tracking_number={tracking_number}")
        assert again.status_code == 409
        assert again.json()["error"] == "already_released"
        held, picked = page.json()["events"]
        assert (held["code"], picked["code"]) == ("customs_hold", "picked_up")
        assert held["occurred_at"] > picked["occurred_at"]

    asyncio.run(scenario())


def test_a_cancelled_or_unknown_label_releases_nothing(database_url: str) -> None:
    async def scenario() -> None:
        async with _client(database_url) as http:
            made = await http.post(
                "/_fake/carrier/cancellable/acct/shipments",
                json=_book_body("1 Test Street"),
            )
            tracking_number = made.json()["tracking_number"]
            await http.post(
                f"/_fake/carrier/cancellable/acct/shipments/{tracking_number}/cancel"
            )
            cancelled = await http.post(
                "/_fake/carrier/cancellable/acct/events/release",
                json={"tracking_number": tracking_number},
            )
            # Another account never sees the label.
            elsewhere = await http.get(
                f"/_fake/carrier/cancellable/other/tracking"
                f"?tracking_number={tracking_number}"
            )
            unknown = await _release(http, "FK999")
        assert cancelled.json()["error"] == "label_cancelled"
        assert elsewhere.status_code == 404
        assert unknown.status_code == 404

    asyncio.run(scenario())


def test_a_subscribed_account_gets_signed_fat_and_thin_pushes(
    database_url: str,
) -> None:
    received: list[httpx2.Request] = []

    async def scenario() -> tuple[str, str]:
        async with _client(database_url, _receiving(received, [])) as http:
            await http.post(
                f"{A}/webhooks",
                json={"url": "http://us/hook", "secret": SECRET, "payload": "fat"},
            )
            tracking_number = await _book(http)
            await _release(http, tracking_number)
            await http.post(
                f"{A}/webhooks",
                json={"url": "http://us/hook", "secret": SECRET, "payload": "thin"},
            )
            released = await _release(http, tracking_number)
        return tracking_number, released.json()["released"][0]["event"]["id"]

    tracking_number, second_id = asyncio.run(scenario())
    fat, thin = received
    for push in received:
        assert push.headers[SIGNATURE_HEADER] == sign(SECRET, push.content)
    fat_body = msgspec.json.decode(fat.content, type=FatWebhook)
    assert [event.code for event in fat_body.events] == ["picked_up"]
    assert msgspec.json.decode(thin.content, type=ThinWebhook) == ThinWebhook(
        second_id, tracking_number
    )


def test_a_push_twice_or_badly_signed_is_sent_as_asked(database_url: str) -> None:
    received: list[httpx2.Request] = []

    async def scenario() -> None:
        async with _client(database_url, _receiving(received, [])) as http:
            await http.post(
                f"{A}/webhooks",
                json={"url": "http://us/hook", "secret": SECRET, "payload": "fat"},
            )
            tracking_number = await _book(http)
            await _release(http, tracking_number, push="twice")
            await _release(http, tracking_number, push="bad_signature")

    asyncio.run(scenario())
    first, repeat, bad = received
    assert first.content == repeat.content
    assert bad.headers[SIGNATURE_HEADER] != sign(SECRET, bad.content)


def test_a_failed_push_is_pushed_again_on_redelivery(database_url: str) -> None:
    received: list[httpx2.Request] = []

    async def scenario() -> tuple[str, int, int]:
        async with _client(database_url, _receiving(received, [500])) as http:
            await http.post(
                f"{A}/webhooks",
                json={"url": "http://us/hook", "secret": SECRET, "payload": "fat"},
            )
            tracking_number = await _book(http)
            released = await _release(http, tracking_number, all=True)
            first = await http.post(f"{A}/events/redeliver")
            second = await http.post(f"{A}/events/redeliver")
        pushes: list[str] = []
        for entry in released.json()["released"]:
            pushes.append(entry["push"])
        assert pushes == ["failed"] + ["delivered"] * 5
        return (
            tracking_number,
            first.json()["redelivered"],
            second.json()["redelivered"],
        )

    _, first, second = asyncio.run(scenario())
    assert (first, second) == (1, 0)
    assert received[0].content == received[-1].content


def test_tracking_faults_apply_to_the_next_fetches(database_url: str) -> None:
    async def scenario() -> tuple[int, bool, int]:
        async with _client(database_url) as http:
            tracking_number = await _book(http)
            await _release(http, tracking_number)
            for fault in ["fail_500", "timeout_after_work"]:
                await http.post(
                    "/_fake/carrier/faults",
                    json={"operation": "track", "fault": fault},
                )
            url = f"{A}/tracking?tracking_number={tracking_number}"
            failed = await http.get(url)
            timed_out = False
            try:
                async with asyncio.timeout(0.2):
                    await http.get(url)
            except TimeoutError:
                timed_out = True
            fine = await http.get(url)
        return failed.status_code, timed_out, fine.status_code

    assert asyncio.run(scenario()) == (500, True, 200)

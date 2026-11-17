"""The fake echo vendor keeps the promises each flavour makes."""

import asyncio

import httpx2

from mor.fakes.echo import make_app
from tests.helpers import NO_HTTP


def _client(database_url: str) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=make_app(database_url, NO_HTTP)),
        base_url="http://fake",
    )


SEND = {"reference": "job-1", "message": "hello"}


def test_keyed_returns_the_first_echo_for_a_known_key(database_url: str) -> None:
    async def scenario() -> None:
        async with _client(database_url) as http:
            headers = {"Idempotency-Key": "k-1"}
            first = await http.post(
                "/_fake/echo/keyed/acct/echoes", json=SEND, headers=headers
            )
            again = await http.post(
                "/_fake/echo/keyed/acct/echoes", json=SEND, headers=headers
            )
            found = await http.get("/_fake/echo/keyed/acct/echoes?reference=job-1")
        assert (first.status_code, again.status_code) == (201, 200)
        assert again.json()["id"] == first.json()["id"]
        assert len(found.json()["echoes"]) == 1

    asyncio.run(scenario())


def test_plain_makes_a_second_echo_for_a_resend(database_url: str) -> None:
    async def scenario() -> None:
        async with _client(database_url) as http:
            headers = {"Idempotency-Key": "k-1"}  # ignored by plain
            await http.post("/_fake/echo/plain/acct/echoes", json=SEND, headers=headers)
            await http.post("/_fake/echo/plain/acct/echoes", json=SEND, headers=headers)
            found = await http.get("/_fake/echo/plain/acct/echoes?reference=job-1")
        assert len(found.json()["echoes"]) == 2

    asyncio.run(scenario())


def test_only_cancellable_cancels(database_url: str) -> None:
    async def scenario() -> None:
        async with _client(database_url) as http:
            made = await http.post("/_fake/echo/cancellable/acct/echoes", json=SEND)
            echo_id = made.json()["id"]
            cancelled = await http.post(
                f"/_fake/echo/cancellable/acct/echoes/{echo_id}/cancel"
            )
            plain = await http.post("/_fake/echo/plain/acct/echoes", json=SEND)
            refused = await http.post(
                f"/_fake/echo/plain/acct/echoes/{plain.json()['id']}/cancel"
            )
        assert cancelled.json()["state"] == "cancelled"
        assert refused.status_code == 404

    asyncio.run(scenario())


def test_scripted_faults_apply_to_the_next_sends_in_order(database_url: str) -> None:
    async def scenario() -> None:
        async with _client(database_url) as http:
            for fault in ["refuse", "fail_500", "fail_500_after_work"]:
                await http.post(
                    "/_fake/echo/faults", json={"flavour": "plain", "fault": fault}
                )
            statuses: list[int] = []
            for _ in range(4):
                response = await http.post("/_fake/echo/plain/acct/echoes", json=SEND)
                statuses.append(response.status_code)
            found = await http.get("/_fake/echo/plain/acct/echoes?reference=job-1")
        assert statuses == [422, 500, 500, 201]
        # The 500 after work and the last send each made an echo.
        assert len(found.json()["echoes"]) == 2

    asyncio.run(scenario())


def test_timeout_after_work_keeps_the_work(database_url: str) -> None:
    async def scenario() -> int:
        async with _client(database_url) as http:
            await http.post(
                "/_fake/echo/faults",
                json={"flavour": "plain", "fault": "timeout_after_work"},
            )
            try:
                async with asyncio.timeout(0.2):
                    await http.post("/_fake/echo/plain/acct/echoes", json=SEND)
            except TimeoutError:
                pass
            found = await http.get("/_fake/echo/plain/acct/echoes?reference=job-1")
        return len(found.json()["echoes"])

    assert asyncio.run(scenario()) == 1

"""The fake PSP keeps the promises the fuzzers rely on."""

import asyncio
from datetime import UTC, datetime

import httpx2

from mor.db.connect import connect
from mor.fakes.hang import hang_a_minute
from mor.fakes.psp import make_app
from tests.helpers import NO_HTTP


NOW = datetime(2026, 11, 25, 9, 30, tzinfo=UTC)


def _client(database_url: str) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(
            app=make_app(database_url, NO_HTTP, lambda: NOW, hang_a_minute)
        ),
        base_url="http://fake",
    )


def test_a_cut_lists_payments_made_at_one_moment_in_reference_order(
    database_url: str,
) -> None:
    # A fuzzer replays the same draws and must meet the same lines in the
    # same order; the fake's own payment ids are random, so they cannot
    # break a tie between payments made at one moment of the test clock.
    references = [f"attempt-{number}" for number in range(8)]

    async def scenario() -> list[str]:
        async with _client(database_url) as http:
            setup = await http.post(
                "/_fake/psp/acct/setup", json={"capture": "sale", "keyed": False}
            )
            assert setup.status_code == 204, setup.text
            for reference in references:
                made = await http.post(
                    "/_fake/psp/acct/payments",
                    json={
                        "reference": reference,
                        "amount_minor": 1000,
                        "currency": "SGD",
                        "method": "card",
                        "return_url": "http://mor/c/back",
                    },
                )
                assert made.status_code == 201, made.text
                payment_id = made.json()["payment"]["id"]
                paid = await http.post(
                    f"/_fake/psp/acct/payments/{payment_id}/release",
                    json={"outcome": "paid"},
                )
                assert paid.status_code == 200, paid.text
            cut = await http.post("/_fake/psp/acct/settlements", json={})
            assert cut.status_code == 200, cut.text
        async with await connect(database_url) as conn:
            cur = await conn.execute(
                "SELECT reference FROM fake_vendor.psp_fake_settlement_line"
                " ORDER BY seq"
            )
            return [str(row[0]) for row in await cur.fetchall()]

    assert asyncio.run(scenario()) == references

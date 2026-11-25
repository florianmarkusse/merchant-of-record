"""The Content-Security-Policy the app sends (decided 25-Nov-2026,
AGENTS.md; docs/plan-payments.md §4, card fields): on every page of ours,
with the PSP's origin only where its fake may be used, and never on the
fakes, which stand for vendors' own pages."""

import asyncio
from datetime import UTC, datetime

import httpx2

from mor.config import Environment, Settings
from mor.web.main import make_app
from tests.helpers import wire


NOW = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)


def _directives(header: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for directive in header.split(";"):
        name, _, sources = directive.strip().partition(" ")
        found[name] = sources
    return found


def test_our_pages_carry_the_policy_with_the_fake_psps_origin(
    database_url: str,
) -> None:
    async def body() -> None:
        wired = wire(database_url, lambda: NOW)
        try:
            staff = await wired.control.get("http://mor/staff/settlement")
            fake = await wired.control.get("http://mor/_fake/psp/js/fields.js")
        finally:
            await wired.http.aclose()
            await wired.control.aclose()
        policy = _directives(staff.headers["content-security-policy"])
        assert policy["default-src"] == "'self'"
        assert policy["script-src"] == "'self' http://psp.mor"
        assert policy["frame-src"] == "http://psp.mor"
        assert policy["style-src"] == "'self'"
        assert policy["frame-ancestors"] == "'none'"
        assert "content-security-policy" not in fake.headers

    asyncio.run(body())


def test_prod_allows_no_fake_psp(database_url: str) -> None:
    async def body() -> None:
        app = make_app(
            Settings(
                environment=Environment.PROD,
                database_url=database_url,
                self_url="http://mor",
                fake_psp_origin="http://psp.mor",
            ),
            None,
            lambda: NOW,
        )
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://mor"
        ) as client:
            answer = await client.get("/v1/merchant")
        policy = _directives(answer.headers["content-security-policy"])
        assert policy["script-src"] == "'self'"
        assert policy["frame-src"] == "'none'"

    asyncio.run(body())

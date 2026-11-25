"""Duty and VAT rates from a vendor through the worker, against the fake
duty vendor in-process (docs/plan-quote.md §3, 4b-2): asked once per
combination, every call counted, each answer stored beside our own row."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fractions import Fraction

import httpx2
import pytest

from mor.audit import Actor, ActorKind
from mor.config import Environment
from mor.db.connect import Connection, connect
from mor.duties import registry
from mor.duties.lookups import (
    NoVendorAccount,
    agreement,
    create_vendor_account,
    read_lookup,
    request_duty_rate,
    vendor_account,
)
from mor.duties.port import DutyProvider, RateQuestion
from mor.duties.records import LookupState, StoredAnswer, VendorLookup
from mor.fakes.duty import make_app
from mor.fakes.hang import hang_a_minute
from mor.ids import CountryCode, DutyLookupId, HsCode
from mor.markets.records import Charge
from mor.markets.source import parse
from mor.markets.store import load_market
from mor.messaging.worker import drain_once
from mor.provider_accounts import AccountNotAllowed, ProviderAccountKind
from tests.helpers import ENV, RefusingTransport, count_rows
from tests.markets.fixtures import SG_GST, TH_BORDER, market_texts


NOW = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
LATER = NOW + timedelta(seconds=3)  # past the first retry's backoff
ACTOR = Actor(ActorKind.SYSTEM, "test")
T_SHIRT = RateQuestion(CountryCode("TH"), CountryCode("VN"), HsCode("610910"))
TH_HS = "TH,6109,duty,,,,30,,secondary,WTO,https://example.test,,,\n"


@dataclass(kw_only=True)
class Setup:
    url: str
    http: httpx2.AsyncClient
    network: RefusingTransport
    worker: Connection


async def _with_setup(url: str, body: Callable[[Setup], Awaitable[None]]) -> None:
    network = RefusingTransport(httpx2.ASGITransport(app=make_app(url, hang_a_minute)))
    async with (
        httpx2.AsyncClient(transport=network) as http,
        await connect(url, autocommit=True) as worker,
    ):
        async with await connect(url) as conn:
            await create_vendor_account(
                conn,
                "acme",
                DutyProvider.FAKE,
                ProviderAccountKind.FAKE,
                "http://fake/_fake/duty/acct",
                timedelta(milliseconds=200),
            )
            await conn.commit()
        await body(Setup(url=url, http=http, network=network, worker=worker))


async def _ask(setup: Setup, question: RateQuestion = T_SHIRT) -> DutyLookupId:
    async with await connect(setup.url) as conn:
        asked = await request_duty_rate(conn, "acme", question, ACTOR, NOW)
        await conn.commit()
    assert not isinstance(asked, NoVendorAccount)
    return asked


async def _lookup(setup: Setup, question: RateQuestion = T_SHIRT) -> VendorLookup:
    async with await connect(setup.url) as conn:
        lookup = await read_lookup(conn, "acme", question)
    assert lookup is not None
    return lookup


def _answer(lookup: VendorLookup, charge: Charge) -> StoredAnswer:
    for answer in lookup.answers:
        if answer.charge == charge:
            return answer
    raise AssertionError(f"no {charge} answer")


async def _vendor_calls(setup: Setup) -> int:
    return await count_rows(setup.url, "SELECT count(*) FROM fake_vendor.duty_call")


async def _facts(setup: Setup, lookup_id: DutyLookupId) -> list[str]:
    async with await connect(setup.url) as conn:
        cur = await conn.execute(
            "SELECT message_type::text FROM outbox WHERE aggregate_id = %s"
            " ORDER BY seq",
            (lookup_id,),
        )
        rows = await cur.fetchall()
    types: list[str] = []
    for (message_type,) in rows:
        types.append(message_type)
    return types


async def _fault(setup: Setup, fault: str) -> None:
    response = await setup.http.post(
        "http://fake/_fake/duty/faults", json={"fault": fault}
    )
    assert response.status_code == 204


async def _vendor_rate(setup: Setup, hs_prefix: HsCode, duty: str, tax: str) -> None:
    response = await setup.http.post(
        "http://fake/_fake/duty/rates",
        json={"destination": "TH", "hs_prefix": hs_prefix, "duty": duty, "tax": tax},
    )
    assert response.status_code == 204


async def _load_market(url: str) -> None:
    market = parse(market_texts(SG_GST + TH_BORDER, hs_rows=TH_HS))
    assert not isinstance(market, list)
    async with await connect(url) as conn:
        await load_market(conn, market, "test", ACTOR, NOW)
        await conn.commit()


def test_a_combination_is_asked_once_and_its_rates_stored(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        first = await _ask(setup)
        again = await _ask(setup)
        assert again == first
        await drain_once(setup.worker, setup.http, ENV, NOW)
        await _ask(setup)
        await drain_once(setup.worker, setup.http, ENV, NOW)

        lookup = await _lookup(setup)
        assert lookup.state == LookupState.ANSWERED and lookup.calls == 1
        rates: dict[Charge, Fraction | None] = {}
        for answer in lookup.answers:
            rates[answer.charge] = answer.rate
        assert rates == {
            Charge.DUTY: Fraction(5, 100),
            Charge.IMPORT_TAX: Fraction(1, 10),
        }
        assert await _vendor_calls(setup) == 1
        assert await _facts(setup, first) == ["duty_rate_needed", "duty_rate_found"]

    asyncio.run(_with_setup(database_url, body))


def test_an_answer_that_differs_from_our_row_is_recorded(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        await _load_market(setup.url)
        await _vendor_rate(setup, HsCode("6109"), "25%", "7%")
        lookup_id = await _ask(setup)
        await drain_once(setup.worker, setup.http, ENV, NOW)

        lookup = await _lookup(setup)
        duty = _answer(lookup, Charge.DUTY)
        assert duty.rate == Fraction(1, 4)
        assert duty.own_rate == Fraction(3, 10) and duty.own_line_no == 2
        assert await _facts(setup, lookup_id) == [
            "duty_rate_needed",
            "duty_rate_disagreed",
            "duty_rate_found",
        ]
        async with await connect(setup.url) as conn:
            rows = await agreement(conn)
        assert len(rows) == 2
        duty_row, vat_row = rows
        assert duty_row.charge == Charge.DUTY and duty_row.differed == 1
        # Our rows hold no VAT per HS code: nothing to compare.
        assert vat_row.charge == Charge.IMPORT_TAX and vat_row.own_none == 1

    asyncio.run(_with_setup(database_url, body))


def test_a_lost_answer_is_asked_again_and_every_call_counted(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await _ask(setup)
        await _fault(setup, "timeout_after_work")
        await drain_once(setup.worker, setup.http, ENV, NOW)
        assert (await _lookup(setup)).state == LookupState.REQUESTED

        setup.network.refuse_next = 1
        await drain_once(setup.worker, setup.http, ENV, LATER)
        assert (await _lookup(setup)).state == LookupState.REQUESTED

        await drain_once(setup.worker, setup.http, ENV, LATER + timedelta(hours=1))
        lookup = await _lookup(setup)
        assert lookup.state == LookupState.ANSWERED
        # Three calls counted; the refused one never reached the vendor.
        assert lookup.calls == 3
        assert await _vendor_calls(setup) == 2

    asyncio.run(_with_setup(database_url, body))


@pytest.mark.parametrize(
    ("fault", "state"),
    [
        ("unknown_code", LookupState.UNKNOWN_CODE),
        ("undecodable", LookupState.NEEDS_PERSON),
    ],
)
def test_an_unknown_code_or_an_unreadable_answer_ends_the_lookup(
    database_url: str, fault: str, state: LookupState
) -> None:
    async def body(setup: Setup) -> None:
        lookup_id = await _ask(setup)
        await _fault(setup, fault)
        await drain_once(setup.worker, setup.http, ENV, NOW)
        lookup = await _lookup(setup)
        assert lookup.state == state and lookup.answers == ()
        facts = await _facts(setup, lookup_id)
        assert facts[-1] == f"duty_rate_{state.value}"

    asyncio.run(_with_setup(database_url, body))


def test_a_rate_by_weight_is_stored_as_the_vendor_wrote_it(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        await _ask(setup)
        await _fault(setup, "specific_duty")
        await drain_once(setup.worker, setup.http, ENV, NOW)
        lookup = await _lookup(setup)
        duty = _answer(lookup, Charge.DUTY)
        assert duty.formula == "2.00 USD/kg" and duty.rate is None

    asyncio.run(_with_setup(database_url, body))


def test_a_vendor_without_an_account_here_cannot_be_asked(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        async with await connect(setup.url) as conn:
            asked = await request_duty_rate(conn, "other", T_SHIRT, ACTOR, NOW)
        assert asked == NoVendorAccount("other")

    asyncio.run(_with_setup(database_url, body))


def test_the_fake_vendor_is_refused_in_prod(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        async with await connect(setup.url) as conn:
            account = await vendor_account(conn, "acme")
        assert account is not None
        with pytest.raises(AccountNotAllowed):
            await registry.ask_rates(setup.http, Environment.PROD, account, T_SHIRT)

    asyncio.run(_with_setup(database_url, body))

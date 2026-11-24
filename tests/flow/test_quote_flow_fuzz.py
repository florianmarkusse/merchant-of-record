"""The quote flow as a Hypothesis state machine against mor_test, the
fake courier and the worker (docs/plan-quote.md §5, 4d-2), on the
synthetic set with express priced live by its carrier.

Actions: a buyer gives an address (again, or another; also a step that
crashes before it commits); asks for the current price; run a delivery;
the worker catches up; crash after an effect's `prepare`; script a
carrier fault; refuse the next connection; move the clock, now and then
past a quote's hold or a session's life.

After every action: each session points at its newest address, and its
state matches that address and its quote;
a session quoting always has something that will move it on (an open
lookup it waits on, or an unhandled delivery that rebuilds it), unless its
life is over; every made quote's options are their parts summed; no
session has more addresses than allowed; no advisory lock is left held.
After draining with faults off and the clock inside the sessions' life: no
session is still quoting, and no delivery is open.
"""

import asyncio
import os
import uuid
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from mor.audit import Actor, ActorKind
from mor.checkout.addresses import Address
from mor.checkout.flow import QUOTE_VALID_FOR, refresh_quote, submit_address
from mor.checkout.records import (
    MAX_ADDRESSES,
    SESSION_LIFETIME,
    AfterPayment,
    Customs,
    SessionDraft,
    SoldLine,
)
from mor.checkout.sessions import create_session
from mor.couriers.prices import create_courier_account
from mor.couriers.records import CourierProvider, Parcel, PriceExpiry
from mor.db.connect import Connection, connect
from mor.markets.store import load_market
from mor.merchants.store import create_merchant
from mor.messaging.delivery import (
    Candidate,
)
from mor.messaging.routing import Effect, consumer_kind
from mor.messaging.worker import (
    Failed,
    find_candidates,
    run_one,
)
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.provider_accounts import ProviderAccountKind
from tests.helpers import (
    ENV,
    TEST_URL,
    VENDOR_TIMEOUT,
    crash,
    crash_around_a_call,
    empty_tables,
    wire,
)
from tests.quote.fixtures import live_market


T0 = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
ACTOR = Actor(ActorKind.SYSTEM, "fuzz")
PARCELS = (
    Parcel(weight_g=1000, length_mm=200, width_mm=150, height_mm=100),
    Parcel(weight_g=2500, length_mm=400, width_mm=300, height_mm=200),
)
FAULTS = (
    "fail_500",
    "timeout_after_work",
    "not_served",
    "undecodable",
    "parts_mismatch",
    "other_currency",
    "already_expired",
)


def _address(country: str, n: int) -> Address:
    if country == "SG":
        return Address(
            name=f"Buyer {n}",
            email=f"b{n}@example.test",
            phone_dial_code="+65",
            phone_number="91234567",
            line1="1 Example Road",
            line2=None,
            city="Singapore",
            region=None,
            postcode="123456",
            country="SG",
        )
    return Address(
        name=f"Buyer {n}",
        email=f"b{n}@example.test",
        phone_dial_code="+66",
        phone_number="812345678",
        line1="99/1 Soi Example",
        line2=None,
        city="Khlong Toei",
        region="Bangkok",
        postcode="10110",
        country="TH",
    )


class QuoteFlow(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.run(empty_tables(TEST_URL))
        self.now = T0
        wired = wire(TEST_URL, lambda: self.now)
        self.network = wired.network
        self.http = wired.http
        self.control = wired.control
        self.worker: Connection = self.run(connect(TEST_URL, autocommit=True))
        self.sessions: list[uuid.UUID] = self.run(self._set_up())
        self.given = 0

    def run[T](self, step: Coroutine[object, object, T]) -> T:
        return self.loop.run_until_complete(step)

    def teardown(self) -> None:
        try:
            self.run(self._drain_and_check())
        finally:
            self.run(self.http.aclose())
            self.run(self.control.aclose())
            self.run(self.worker.close())
            self.loop.close()

    # --- actions ---------------------------------------------------------

    @initialize(country=st.sampled_from(("SG", "TH")))
    def a_first_address_is_given(self, country: str) -> None:
        self.give_an_address(0, country)

    @rule(session=st.integers(0, 1), country=st.sampled_from(("SG", "TH")))
    def give_an_address(self, session: int, country: str) -> None:
        self.given += 1

        async def step() -> None:
            async with await connect(TEST_URL) as conn:
                await submit_address(
                    conn,
                    self.sessions[session],
                    _address(country, self.given),
                    ENV,
                    self.now,
                )
                await conn.commit()

        self.run(step())

    @rule(session=st.integers(0, 1), country=st.sampled_from(("SG", "TH")))
    def an_address_step_crashes_before_it_commits(
        self, session: int, country: str
    ) -> None:
        async def step() -> None:
            crashing = await connect(TEST_URL)
            try:
                await submit_address(
                    crashing,
                    self.sessions[session],
                    _address(country, 0),
                    ENV,
                    self.now,
                )
            finally:
                await crash(crashing)

        self.run(step())

    @rule(session=st.integers(0, 1))
    def the_buyer_asks_for_the_current_price(self, session: int) -> None:
        async def step() -> None:
            async with await connect(TEST_URL) as conn:
                await refresh_quote(conn, self.sessions[session], ENV, self.now)
                await conn.commit()

        self.run(step())

    @rule(data=st.data())
    def run_a_delivery(self, data: st.DataObject) -> None:
        candidate = self._draw_candidate(data)
        if candidate is None:
            return
        result = self.run(run_one(self.worker, self.http, ENV, candidate, self.now))
        assert not isinstance(result, Failed), result

    @rule()
    def the_worker_catches_up(self) -> None:
        self.run(self._catch_up())

    @rule(data=st.data(), with_call=st.booleans())
    def crash_between_commits(self, data: st.DataObject, with_call: bool) -> None:
        candidate = self._draw_candidate(data)
        if candidate is None or not isinstance(
            consumer_kind(candidate.consumer), Effect
        ):
            return

        self.run(
            crash_around_a_call(candidate, with_call, self.http, self.network, self.now)
        )

    @precondition(lambda self: self.run(self._faults_waiting()) < 2)
    @rule(fault=st.sampled_from(FAULTS))
    def script_a_fault(self, fault: str) -> None:
        response = self.run(
            self.control.post("http://mor/_fake/courier/faults", json={"fault": fault})
        )
        assert response.status_code == 204

    @precondition(lambda self: self.network.refuse_next < 2)
    @rule()
    def refuse_the_next_connection(self) -> None:
        self.network.refuse_next += 1

    @rule(seconds=st.integers(min_value=0, max_value=600))
    def clock_moves(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)

    @rule()
    def clock_passes_a_quotes_hold(self) -> None:
        self.now += QUOTE_VALID_FOR

    @precondition(lambda self: self.now < T0 + SESSION_LIFETIME)
    @rule()
    def clock_passes_the_sessions_life(self) -> None:
        self.now = T0 + SESSION_LIFETIME

    # --- checks after every action ---------------------------------------

    @invariant()
    def each_state_matches_its_address_and_quote(self) -> None:
        cur = self.run(
            self.worker.execute(
                "SELECT s.state::text, s.address_id, s.quote_id, q.state::text,"
                " q.address_id"
                " FROM checkout_session s LEFT JOIN quote q ON q.id = s.quote_id"
            )
        )
        for state, address_id, quote_id, quote_state, quote_address in self.run(
            cur.fetchall()
        ):
            match state:
                case "open":
                    assert address_id is None and quote_id is None
                case "quoting":
                    assert address_id is not None and quote_id is None
                case "quoted" | "refused":
                    assert quote_address == address_id
                    assert quote_state == ("made" if state == "quoted" else "refused")
                case "expired":
                    pass
                case _:
                    raise AssertionError(state)

    @invariant()
    def a_session_points_at_its_newest_address(self) -> None:
        """A late rebuild for an address the buyer has since replaced must
        not bring the old one back."""
        cur = self.run(
            self.worker.execute(
                "SELECT s.id FROM checkout_session s"
                " WHERE s.address_id IS DISTINCT FROM"
                " (SELECT a.id FROM checkout_address a"
                "  WHERE a.session_id = s.id ORDER BY a.id DESC LIMIT 1)"
            )
        )
        assert self.run(cur.fetchall()) == []

    @invariant()
    def a_session_quoting_is_always_moved_on(self) -> None:
        """A missed notification would leave a buyer waiting for ever."""
        cur = self.run(
            self.worker.execute(
                """
                SELECT s.id FROM checkout_session s
                WHERE s.state = 'quoting' AND s.expires_at > %s
                  AND NOT EXISTS (
                    SELECT 1 FROM quote_wait w
                    LEFT JOIN shipping_price_lookup p
                      ON p.id = w.shipping_price_lookup_id
                    LEFT JOIN duty_lookup d ON d.id = w.duty_lookup_id
                    WHERE w.address_id = s.address_id
                      AND (p.state = 'requested' OR d.state = 'requested'))
                  AND NOT EXISTS (
                    SELECT 1 FROM outbox_delivery od
                    WHERE od.handled_at IS NULL
                      AND ((od.consumer = 'quote_build' AND od.aggregate_id = s.id)
                        OR (od.consumer = 'quote_wait_notify'
                            AND od.aggregate_id IN (
                              SELECT coalesce(w.duty_lookup_id,
                                              w.shipping_price_lookup_id)
                              FROM quote_wait w
                              WHERE w.address_id = s.address_id))))
                """,
                (self.now,),
            )
        )
        assert self.run(cur.fetchall()) == []

    @invariant()
    def every_option_is_its_parts_summed(self) -> None:
        cur = self.run(
            self.worker.execute(
                """
                SELECT o.quote_id, o.option_no, o.total_minor,
                  (SELECT coalesce(sum(p.amount_minor), 0) FROM quote_part p
                    WHERE p.quote_id = o.quote_id
                      AND (p.option_no IS NULL OR p.option_no = o.option_no))
                FROM quote_option o
                """
            )
        )
        for quote_id, option_no, total, summed in self.run(cur.fetchall()):
            assert total == summed, (quote_id, option_no, total, summed)

    @invariant()
    def no_session_has_more_addresses_than_allowed(self) -> None:
        cur = self.run(
            self.worker.execute(
                "SELECT count(*) FROM checkout_address GROUP BY session_id"
                " HAVING count(*) > %s",
                (MAX_ADDRESSES,),
            )
        )
        assert self.run(cur.fetchall()) == []

    @invariant()
    def no_advisory_lock_is_left_held(self) -> None:
        cur = self.run(
            self.worker.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
                " AND database = (SELECT oid FROM pg_database"
                " WHERE datname = current_database())"
            )
        )
        row = self.run(cur.fetchone())
        assert row is not None and row[0] == 0

    # --- helpers ---------------------------------------------------------

    async def _set_up(self) -> list[uuid.UUID]:
        async with await connect(TEST_URL) as conn:
            await load_market(conn, live_market(), "fuzz", ACTOR, T0)
            merchant = await create_merchant(
                conn, "Fuzz Shop Co.", "Fuzz Shop", "VN", 100, ACTOR, T0
            )
            await create_courier_account(
                conn,
                "carrier",
                CourierProvider.FAKE,
                ProviderAccountKind.FAKE,
                "http://mor/_fake/courier/acct",
                VENDOR_TIMEOUT,
                timedelta(hours=6),
                PriceExpiry.CARRIER_FIRST,
            )
            sessions: list[uuid.UUID] = []
            for parcel in PARCELS:
                line = SoldLine(
                    name="Linen shirt",
                    quantity=1,
                    unit_price=Money(2500, Currency.SGD),
                    category="apparel",
                    merchant_sku=None,
                    customs=Customs(
                        description="Linen shirt",
                        origin_country="VN",
                        weight_g=300,
                        hs_code="610910",
                    ),
                )
                session = await create_session(
                    conn,
                    merchant.id,
                    SessionDraft(
                        lines=(line,),
                        parcel=parcel,
                        merchant_reference=None,
                        return_url="https://shop.example/thanks",
                        cancel_url="https://shop.example/cart",
                        after_payment=AfterPayment.OUR_PAGE,
                    ),
                    ACTOR,
                    T0,
                )
                sessions.append(session.id)
            await conn.commit()
        return sessions

    async def _faults_waiting(self) -> int:
        cur = await self.worker.execute(
            "SELECT count(*) FROM fake_vendor.courier_fault"
        )
        row = await cur.fetchone()
        assert row is not None
        return row[0]

    async def _catch_up(self) -> None:
        for _ in range(100):
            candidates = await find_candidates(self.worker, self.now, 50)
            if not candidates:
                return
            for candidate in candidates:
                result = await run_one(self.worker, self.http, ENV, candidate, self.now)
                assert not isinstance(result, Failed), result
        raise AssertionError("the worker never caught up")

    def _draw_candidate(self, data: st.DataObject) -> Candidate | None:
        candidates = self.run(find_candidates(self.worker, self.now, 50))
        if not candidates:
            return None
        return data.draw(st.sampled_from(candidates))

    async def _drain_and_check(self) -> None:
        # Faults off; the clock moves only by retry backoffs, so a session
        # still alive must finish quoting before its life ends.
        await self.worker.execute("DELETE FROM fake_vendor.courier_fault")
        self.network.refuse_next = 0
        for _ in range(30):
            self.now += timedelta(minutes=5)
            await self._catch_up()
        cur = await self.worker.execute(
            "SELECT count(*) FROM checkout_session"
            " WHERE state = 'quoting' AND expires_at > %s",
            (self.now,),
        )
        row = await cur.fetchone()
        assert row == (0,), "a session is still quoting after the drain"
        self.now = T0 + SESSION_LIFETIME * 3
        await self._catch_up()
        cur = await self.worker.execute(
            "SELECT count(*) FROM outbox_delivery WHERE handled_at IS NULL"
        )
        row = await cur.fetchone()
        assert row == (0,), "a delivery is still open after the drain"
        cur = await self.worker.execute(
            "SELECT count(*) FROM checkout_session WHERE state <> 'expired'"
        )
        row = await cur.fetchone()
        assert row == (0,), "a session outlived its life"


def _examples() -> int:
    if os.environ.get("HYPOTHESIS_PROFILE", "dev") == "dev":
        return 30
    return settings().max_examples


def test_quotes_survive_faults_crashes_double_submits_and_deadlines(
    migrated_test_database: str,
) -> None:
    run_state_machine_as_test(
        QuoteFlow, settings=settings(max_examples=_examples(), deadline=None)
    )

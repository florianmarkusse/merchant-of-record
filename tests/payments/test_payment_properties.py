"""Pure properties of payments (docs/plan-payments.md §8): the attempt's
transition table answers every pair and only moves forward; the capture
entry balances per currency and pays the merchant exactly its prices; the
fake PSP's amounts round-trip, or are refused when not whole in ours; an
invoice's lines sum to what was paid, as the buyer saw it."""

import uuid
from datetime import UTC, datetime
from fractions import Fraction

from hypothesis import given
from hypothesis import strategies as st

from mor.audit import Actor, ActorKind
from mor.checkout.quotes import MadeQuote, QuoteInputs, StoredOption
from mor.checkout.records import Customs, SoldLine
from mor.invoices.lines import invoice_lines
from mor.invoices.records import InvoiceSection
from mor.ledger.account import AccountKind
from mor.markets.records import Collector, ServiceTier, Standing
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.money.rounding import RoundingMode
from mor.orders.posting import capture_entry
from mor.payments.transitions import AttemptState, Stale, after_event
from mor.psp.fake_client import (
    decode_events,
    from_provider_amount,
    to_provider_amount,
)
from mor.psp.port import (
    PspEventKind,
)
from mor.quote.build import Part
from mor.quote.groups import PartKind
from mor.vendor_failures import UndecodableBody


NOW = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
KINDS = tuple(PspEventKind)
# Where an event can never take an attempt: those are our own steps.
NEVER_BY_EVENT = (
    AttemptState.REQUESTED,
    AttemptState.ACTION_REQUIRED,
    AttemptState.CANCELLING,
)
RANK = {
    AttemptState.REQUESTED: 0,
    AttemptState.ACTION_REQUIRED: 1,
    AttemptState.CANCELLING: 2,
    AttemptState.AUTHORISED: 2,
    AttemptState.CAPTURED: 3,
    AttemptState.DECLINED: 3,
    AttemptState.EXPIRED: 3,
    AttemptState.CANCELLED: 3,
    AttemptState.NEEDS_PERSON: 4,
}


def test_every_state_and_event_has_an_answer_that_only_moves_forward() -> None:
    for state in AttemptState:
        for kind in KINDS:
            answer = after_event(state, kind)
            if isinstance(answer, Stale):
                continue
            assert answer not in NEVER_BY_EVENT, (state, kind)
            assert RANK[answer] > RANK[state], (state, kind, answer)


def test_a_capture_is_never_undone_by_a_later_event() -> None:
    for kind in KINDS:
        assert isinstance(after_event(AttemptState.CAPTURED, kind), Stale)


def _part(
    kind: PartKind, amount: int, line: int | None, collector: Collector | None
) -> Part:
    return Part(
        kind=kind,
        label=kind.value,
        line=line,
        exact=Fraction(amount),
        amount=Money(amount, Currency.SGD),
        mode=RoundingMode.HALF_EVEN,
        collector=collector,
        source="test",
    )


SHIPPING_KINDS = (PartKind.FREIGHT, PartKind.SURCHARGE, PartKind.FUEL)
TAX_KINDS = (
    PartKind.DUTY,
    PartKind.ADDITIONAL_DUTY,
    PartKind.CUSTOMS_FEE,
    PartKind.IMPORT_TAX,
    PartKind.OTHER_TAX,
)


@st.composite
def _paid_quotes(draw: st.DrawFn) -> tuple[MadeQuote, tuple[SoldLine, ...]]:
    """A quote option in SGD whose parts sum to its total, for lines priced
    in SGD or in other currencies."""
    count = draw(st.integers(1, 4))
    lines: list[SoldLine] = []
    goods: list[Part] = []
    for number in range(count):
        currency = draw(st.sampled_from((Currency.SGD, Currency.USD, Currency.VND)))
        price = Money(draw(st.integers(0, 10**7)), currency)
        quantity = draw(st.integers(1, 5))
        lines.append(
            SoldLine(
                name="Item",
                quantity=quantity,
                unit_price=price,
                category="apparel",
                merchant_sku=None,
                customs=Customs(
                    description="Item", origin_country="VN", weight_g=100, hs_code=None
                ),
            )
        )
        goods.append(_part(PartKind.GOODS, draw(st.integers(0, 10**8)), number, None))
    for kind in (PartKind.PSP_FEE, PartKind.FX, PartKind.MOR_MARGIN):
        goods.append(_part(kind, draw(st.integers(0, 10**6)), None, None))
    option_parts: list[Part] = []
    for kind in draw(
        st.lists(
            st.sampled_from(SHIPPING_KINDS + (PartKind.DUTY_PAID_FEE,)), max_size=4
        )
    ):
        option_parts.append(_part(kind, draw(st.integers(0, 10**6)), None, None))
    for kind in draw(st.lists(st.sampled_from(TAX_KINDS), max_size=4)):
        collector = draw(st.sampled_from((Collector.BORDER, Collector.SELLER)))
        option_parts.append(_part(kind, draw(st.integers(0, 10**6)), None, collector))
    total = sum(part.amount.amount_minor for part in goods + option_parts)
    if total == 0:
        option_parts.append(_part(PartKind.FREIGHT, 1, None, None))
        total = 1
    option = StoredOption(
        key="opt",
        service="exp",
        service_name="Express",
        tier=ServiceTier.EXPRESS,
        transit=None,
        total_minor=total,
        standing=Standing.AGREED,
        parts=tuple(option_parts),
    )
    quote = MadeQuote(
        id=uuid.uuid7(),
        session_id=uuid.uuid7(),
        address_id=uuid.uuid7(),
        load_id=uuid.uuid7(),
        made_at=NOW,
        valid_until=NOW,
        currency=Currency.SGD,
        goods=tuple(goods),
        options=(option,),
        placeholders=(),
        not_offered=(),
        inputs=QuoteInputs((), ()),
    )
    return quote, tuple(lines)


@given(_paid_quotes())
def test_the_capture_entry_balances_and_pays_the_merchant_exactly(
    paid: tuple[MadeQuote, tuple[SoldLine, ...]],
) -> None:
    quote, lines = paid
    merchant = uuid.uuid7()
    entry = capture_entry(
        uuid.uuid7(),
        quote,
        0,
        lines,
        merchant,
        uuid.uuid7(),
        "SG",
        "carrier",
        NOW,
        Actor(ActorKind.SYSTEM, "test"),
        NOW,
    )
    # build_entry refuses an unbalanced entry; check per currency anyway.
    totals: dict[Currency, int] = {}
    owed: dict[Currency, int] = {}
    for line in entry.lines:
        currency = line.amount.currency
        totals[currency] = totals.get(currency, 0) + line.amount.amount_minor
        if line.account.kind == AccountKind.MERCHANT_PAYABLE:
            owed[currency] = owed.get(currency, 0) - line.amount.amount_minor
    assert set(totals.values()) <= {0}
    for currency in (Currency.USD, Currency.VND):
        expected = sum(
            line.unit_price.amount_minor * line.quantity
            for line in lines
            if line.unit_price.currency == currency
        )
        assert owed.get(currency, 0) == expected


@given(_paid_quotes())
def test_invoice_lines_sum_to_the_total_and_its_goods_lines_to_the_items(
    paid: tuple[MadeQuote, tuple[SoldLine, ...]],
) -> None:
    quote, lines = paid
    option = quote.options[0]
    invoice = invoice_lines(quote, 0, lines)
    assert sum(line.amount_minor for line in invoice) == option.total_minor
    goods = [line for line in invoice if line.section == InvoiceSection.GOODS]
    assert len(goods) == len(lines)
    assert sum(line.amount_minor for line in goods) == sum(
        part.amount.amount_minor for part in quote.goods
    )
    for line in goods:
        assert line.amount_minor >= 0
    shipping = [line for line in invoice if line.section == InvoiceSection.SHIPPING]
    assert len(shipping) == 1
    # Each duty and tax on its own line, in the section of who collects it.
    for collector, section in (
        (Collector.SELLER, InvoiceSection.TAX),
        (Collector.BORDER, InvoiceSection.BORDER),
    ):
        parts = [part for part in option.parts if part.collector == collector]
        shown = [line for line in invoice if line.section == section]
        assert [line.amount_minor for line in shown] == [
            part.amount.amount_minor for part in parts
        ]


@given(st.integers(0, 10**12), st.sampled_from(list(Currency)))
def test_fake_psp_amounts_round_trip(amount: int, currency: Currency) -> None:
    money = Money(amount, currency)
    assert from_provider_amount(to_provider_amount(money), currency.value) == money


@given(st.integers(0, 10**12).filter(lambda n: n % 100 != 0))
def test_a_fraction_of_a_rupiah_is_refused_not_rounded(amount: int) -> None:
    assert isinstance(from_provider_amount(amount, "IDR"), UndecodableBody)


def test_a_psp_event_time_is_taken_in_utc_and_one_without_an_offset_refused() -> None:
    event = (
        b'{"events": [{"id": "e", "payment_id": "p", "reference": "r",'
        b' "kind": "captured", "amount_minor": 100, "currency": "SGD",'
        b' "occurred_at": "%s"}]}'
    )
    decoded = decode_events(event % b"2026-11-18T16:30:00+07:00")
    assert not isinstance(decoded, UndecodableBody)
    assert decoded[0].occurred_at.tzinfo == UTC
    assert decoded[0].occurred_at == datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
    assert isinstance(decode_events(event % b"2026-11-18T16:30:00"), UndecodableBody)

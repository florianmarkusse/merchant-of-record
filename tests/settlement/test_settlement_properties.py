"""Properties of settlement's pure parts (docs/plan-payments.md §7,
docs/plan-payments.md §7): the line, payout and rounding
entries balance and clear what they should, the payout's check is exact,
the fake's payout rounding stays within half a unit, and the decoders turn
any bytes into a document or a typed error."""

import math
import uuid
from datetime import UTC, datetime
from fractions import Fraction

from hypothesis import given, strategies

from mor.audit import Actor, ActorKind
from mor.fakes.psp import payout_amount
from mor.ids import PspAccountId, PspLineId, PspPayoutId
from mor.ledger.account import (
    Account,
    PayoutInTransit,
    PspClearing,
    PspFeeCollected,
    PspFeeVariance,
    Rounding,
)
from mor.ledger.entry import JournalEntry
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.psp.fake_client import (
    CSV_HEADER,
    decode_payout,
    decode_payout_lines,
    decode_settlement_file,
)
from mor.psp.port import (
    SettlementDocument,
)
from mor.settlement.entries import (
    AddedUp,
    LineAmounts,
    NetDiffers,
    check_net,
    line_entry,
    payout_entry,
    rounding_entry,
    to_minor,
)
from mor.vendor_failures import UndecodableBody


ACCOUNT = PspAccountId(uuid.UUID("01a1fac0-0000-7000-8000-000000000001"))
WHEN = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
ACTOR = Actor(ActorKind.SYSTEM, "test")
CLEARING = PspClearing(ACCOUNT)
PSP_FEE = PspFeeCollected()
FEE_VARIANCE = PspFeeVariance(ACCOUNT)
IN_TRANSIT = PayoutInTransit(ACCOUNT)
ROUNDING = Rounding(ACCOUNT)


def _on(entry: JournalEntry, account: Account) -> int:
    total = 0
    for line in entry.lines:
        if line.account == account:
            total += line.amount.amount_minor
    return total


@given(
    estimate=strategies.integers(0, 10**9),
    fee=strategies.integers(-(10**6), 10**9),
)
def test_a_line_clears_its_estimate_and_books_the_gap_as_ours(
    estimate: int, fee: int
) -> None:
    entry = line_entry(
        ACCOUNT,
        PspLineId("line-1"),
        Money(estimate, Currency.SGD),
        Money(fee, Currency.SGD),
        WHEN,
        ACTOR,
        WHEN,
    )
    if estimate == 0 and fee == 0:
        assert entry is None
        return
    assert entry is not None  # build_entry refuses an unbalanced one
    assert _on(entry, PSP_FEE) == estimate
    assert _on(entry, CLEARING) == -fee
    assert _on(entry, FEE_VARIANCE) == fee - estimate


_lines = strategies.lists(
    strategies.builds(
        LineAmounts,
        strategies.integers(0, 10**10).map(Fraction),
        # A fee a PSP counting in hundredths of our unit may charge.
        strategies.integers(0, 10**8).map(lambda hundredths: Fraction(hundredths, 100)),
    ),
    max_size=8,
)


@given(lines=_lines, up=strategies.booleans())
def test_a_net_rounded_either_way_adds_up_and_clears_what_its_lines_left(
    lines: list[LineAmounts], up: bool
) -> None:
    exact = sum((line.gross - line.fee for line in lines), Fraction(0))
    reported = math.ceil(exact) if up else math.floor(exact)
    check = check_net(Fraction(reported), lines)
    assert isinstance(check, AddedUp)
    left = 0
    for line in lines:
        left += to_minor(line.gross) - to_minor(line.fee)
    assert check.rounding == reported - left
    payout = payout_entry(
        ACCOUNT, PspPayoutId("po-1"), Money(reported, Currency.IDR), WHEN, ACTOR, WHEN
    )
    rounding = rounding_entry(
        ACCOUNT,
        PspPayoutId("po-1"),
        1,
        Money(check.rounding, Currency.IDR),
        WHEN,
        ACTOR,
        WHEN,
    )
    clearing = 0
    in_transit = 0
    rounded = 0
    for entry in (payout, rounding):
        if entry is not None:
            clearing += _on(entry, CLEARING)
            in_transit += _on(entry, IN_TRANSIT)
            rounded += _on(entry, ROUNDING)
    assert in_transit == reported
    # Together, exactly what the booked lines left in psp_clearing goes out.
    assert clearing == -left
    assert rounded == left - reported


@given(change=strategies.integers(-(10**6), 10**6))
def test_a_rounding_change_and_its_reversal_leave_nothing(change: int) -> None:
    there = rounding_entry(
        ACCOUNT, PspPayoutId("po-1"), 1, Money(change, Currency.SGD), WHEN, ACTOR, WHEN
    )
    back = rounding_entry(
        ACCOUNT, PspPayoutId("po-1"), 2, Money(-change, Currency.SGD), WHEN, ACTOR, WHEN
    )
    if change == 0:
        assert there is None and back is None
        return
    assert there is not None and back is not None
    assert there.idempotency_key != back.idempotency_key
    for account in (CLEARING, ROUNDING):
        assert _on(there, account) + _on(back, account) == 0


@given(lines=_lines, off=strategies.integers(1, 10**6), below=strategies.booleans())
def test_a_net_further_off_than_rounding_does_not_add_up(
    lines: list[LineAmounts], off: int, below: bool
) -> None:
    exact = sum((line.gross - line.fee for line in lines), Fraction(0))
    reported = math.floor(exact) - off if below else math.ceil(exact) + off
    assert isinstance(check_net(Fraction(reported), lines), NetDiffers)


@given(net=strategies.integers(-(10**9), 10**9))
def test_a_payout_is_booked_as_reported(net: int) -> None:
    entry = payout_entry(
        ACCOUNT, PspPayoutId("po-1"), Money(net, Currency.SGD), WHEN, ACTOR, WHEN
    )
    if net == 0:
        assert entry is None
        return
    assert entry is not None
    assert _on(entry, IN_TRANSIT) == net
    assert _on(entry, CLEARING) == -net


@given(net=strategies.integers(-(10**12), 10**12))
def test_the_fakes_idr_payout_is_whole_rupiah_within_half_a_rupiah(net: int) -> None:
    paid = payout_amount("IDR", net)
    assert paid % 100 == 0
    assert abs(paid - net) <= 50
    assert payout_amount("SGD", net) == net


@given(body=strategies.binary(max_size=400))
def test_any_bytes_decode_to_a_document_or_a_typed_error(body: bytes) -> None:
    for decoded in (
        decode_settlement_file(body),
        decode_payout(body),
        decode_payout_lines(body, PspPayoutId("po-1")),
    ):
        assert isinstance(decoded, SettlementDocument | UndecodableBody)


_cells = strategies.text(
    alphabet=strategies.characters(codec="utf-8", exclude_categories=("Cs",)),
    max_size=12,
) | strategies.sampled_from(
    [
        "line",
        "merchant_payout",
        "b-1",
        "capture",
        "balance_transfer",
        "SGD",
        "IDR",
        "1250",
        "-7",
        "2026-11-18T09:30:00+00:00",
        "",
    ]
)


@given(
    rows=strategies.lists(strategies.lists(_cells, min_size=0, max_size=12), max_size=5)
)
def test_any_csv_decodes_to_a_report_or_a_typed_error(rows: list[list[str]]) -> None:
    lines = [",".join(CSV_HEADER)]
    for row in rows:
        lines.append(",".join(cell.replace(",", "") for cell in row))
    decoded = decode_settlement_file("\n".join(lines).encode())
    assert isinstance(decoded, SettlementDocument | UndecodableBody)
    if isinstance(decoded, SettlementDocument):
        assert decoded.payout is not None
        for line in decoded.lines:
            assert line.currency in Currency
            assert line.vendor_payout_id == decoded.payout.vendor_payout_id


def test_a_vendors_moment_is_taken_in_utc_and_one_without_an_offset_refused() -> None:
    payout = (
        b'{"id": "po-1", "amount_minor": 0, "currency": "SGD",'
        b' "arrival_date": "%s", "reconciliation_status": "completed"}'
    )
    decoded = decode_payout(payout % b"2026-11-18T16:30:00+07:00")
    assert isinstance(decoded, SettlementDocument)
    assert decoded.payout is not None
    assert decoded.payout.paid_out_at.tzinfo == UTC
    assert decoded.payout.paid_out_at == datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
    assert isinstance(decode_payout(payout % b"2026-11-18T16:30:00"), UndecodableBody)
    page = (
        b'{"data": [{"id": "l-1", "type": "capture", "payment_id": "p",'
        b' "reference": "r", "currency": "SGD", "gross_minor": 1, "fee_minor": 0,'
        b' "created": "2026-11-18T16:30:00"}], "has_more": false}'
    )
    assert isinstance(decode_payout_lines(page, PspPayoutId("po-1")), UndecodableBody)
    report = (
        ",".join(CSV_HEADER)
        + "\nmerchant_payout,b,b-payout,,,,SGD,,,0,2026-11-18T16:30:00\n"
    )
    assert isinstance(decode_settlement_file(report.encode()), UndecodableBody)


def test_a_page_of_lines_belongs_to_the_payout_it_was_asked_for() -> None:
    page = (
        b'{"data": [{"id": "l-1", "type": "balance_transfer", "payment_id": "-",'
        b' "reference": "-", "currency": "SGD", "gross_minor": -300,'
        b' "fee_minor": 0, "created": "2026-11-18T09:30:00Z"}], "has_more": true}'
    )
    decoded = decode_payout_lines(page, PspPayoutId("po-7"))
    assert isinstance(decoded, SettlementDocument)
    assert decoded.payout is None
    assert [line.vendor_payout_id for line in decoded.lines] == ["po-7"]

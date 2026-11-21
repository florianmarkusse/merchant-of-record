"""Properties of settlement's pure parts (docs/plan-payments.md §7.1): the
line and payout entries balance and clear what they should, the payout's
check is exact, the fake's payout rounding stays within half a unit, and
the decoders turn any bytes into a document or a typed error."""

import math
import uuid
from datetime import UTC, datetime
from fractions import Fraction

from hypothesis import given
from hypothesis import strategies as st

from mor.audit import Actor, ActorKind
from mor.fakes.psp import payout_amount
from mor.ledger.account import AccountKind
from mor.ledger.entry import JournalEntry
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.psp.fake_client import decode_settlement_file, decode_settlement_page
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
    to_minor,
)
from mor.vendor_failures import UndecodableBody


ACCOUNT = uuid.UUID("01a1fac0-0000-7000-8000-000000000001")
WHEN = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
ACTOR = Actor(ActorKind.SYSTEM, "test")


def _on(entry: JournalEntry, kind: AccountKind) -> int:
    total = 0
    for line in entry.lines:
        if line.account.kind == kind:
            total += line.amount.amount_minor
    return total


@given(
    estimate=st.integers(0, 10**9),
    fee=st.integers(-(10**6), 10**9),
)
def test_a_line_clears_its_estimate_and_books_the_gap_as_ours(
    estimate: int, fee: int
) -> None:
    entry = line_entry(
        ACCOUNT,
        "line-1",
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
    assert _on(entry, AccountKind.PSP_FEE_COLLECTED) == estimate
    assert _on(entry, AccountKind.PSP_CLEARING) == -fee
    assert _on(entry, AccountKind.PSP_FEE_VARIANCE) == fee - estimate


_lines = st.lists(
    st.builds(
        LineAmounts,
        st.integers(0, 10**10).map(Fraction),
        # A fee a PSP counting in hundredths of our unit may charge.
        st.integers(0, 10**8).map(lambda hundredths: Fraction(hundredths, 100)),
    ),
    max_size=8,
)


@given(lines=_lines, up=st.booleans())
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
    entry = payout_entry(
        ACCOUNT, "batch-1", Currency.IDR, reported, check, WHEN, ACTOR, WHEN
    )
    if entry is None:
        assert reported == 0 and left == 0
        return
    assert _on(entry, AccountKind.PAYOUT_IN_TRANSIT) == reported
    # Exactly what the booked lines left in psp_clearing goes out.
    assert _on(entry, AccountKind.PSP_CLEARING) == -left
    assert _on(entry, AccountKind.ROUNDING) == left - reported


@given(lines=_lines, off=st.integers(1, 10**6), below=st.booleans())
def test_a_net_further_off_than_rounding_is_booked_as_reported_and_named(
    lines: list[LineAmounts], off: int, below: bool
) -> None:
    exact = sum((line.gross - line.fee for line in lines), Fraction(0))
    reported = math.floor(exact) - off if below else math.ceil(exact) + off
    check = check_net(Fraction(reported), lines)
    assert isinstance(check, NetDiffers)
    entry = payout_entry(
        ACCOUNT, "batch-1", Currency.IDR, reported, check, WHEN, ACTOR, WHEN
    )
    if reported == 0:
        assert entry is None
        return
    assert entry is not None
    assert _on(entry, AccountKind.PAYOUT_IN_TRANSIT) == reported
    assert _on(entry, AccountKind.PSP_CLEARING) == -reported
    assert _on(entry, AccountKind.ROUNDING) == 0


@given(net=st.integers(-(10**12), 10**12))
def test_the_fakes_idr_payout_is_whole_rupiah_within_half_a_rupiah(net: int) -> None:
    paid = payout_amount("IDR", net)
    assert paid % 100 == 0
    assert abs(paid - net) <= 50
    assert payout_amount("SGD", net) == net


@given(body=st.binary(max_size=400))
def test_any_bytes_decode_to_a_report_or_a_typed_error(body: bytes) -> None:
    for decoded in (decode_settlement_file(body), decode_settlement_page(body)):
        assert isinstance(decoded, SettlementDocument | UndecodableBody)


_cells = st.text(
    alphabet=st.characters(codec="utf-8", exclude_categories=("Cs",)), max_size=12
) | st.sampled_from(["batch", "line", "capture", "SGD", "IDR", "1250", "-7", ""])


@given(rows=st.lists(st.lists(_cells, min_size=0, max_size=11), max_size=5))
def test_any_csv_decodes_to_a_report_or_a_typed_error(rows: list[list[str]]) -> None:
    header = (
        "record,id,type,payment_id,reference,currency,gross_minor,fee_minor,"
        "net_minor,paid_out_at"
    )
    lines = [header]
    for row in rows:
        lines.append(",".join(cell.replace(",", "") for cell in row))
    decoded = decode_settlement_file("\n".join(lines).encode())
    assert isinstance(decoded, SettlementDocument | UndecodableBody)
    if isinstance(decoded, SettlementDocument):
        for line in decoded.lines:
            assert line.currency in Currency


def test_a_vendors_moment_is_taken_in_utc_and_one_without_an_offset_refused() -> None:
    page = (
        b'{"batch": {"id": "b", "currency": "SGD", "net_minor": 0,'
        b' "paid_out_at": "%s"}, "lines": [], "next_page": null}'
    )
    decoded = decode_settlement_page(page % b"2026-11-18T16:30:00+07:00")
    assert isinstance(decoded, SettlementDocument)
    assert decoded.batch.paid_out_at.tzinfo == UTC
    assert decoded.batch.paid_out_at == datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
    naive = decode_settlement_page(page % b"2026-11-18T16:30:00")
    assert isinstance(naive, UndecodableBody)
    report = (
        "record,id,type,payment_id,reference,currency,gross_minor,fee_minor,"
        "net_minor,paid_out_at\nbatch,b,,,,SGD,,,0,2026-11-18T16:30:00\n"
    )
    assert isinstance(decode_settlement_file(report.encode()), UndecodableBody)

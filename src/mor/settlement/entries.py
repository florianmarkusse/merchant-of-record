"""Settlement's journal entries and the payout's check (docs/plan-payments.md
§7.1, docs/plan-payments.md §7). Pure.

    a settled line, in its currency:
      Dr psp_fee_collected               the estimate the capture booked
      Cr psp_clearing:{account}          the fee the PSP kept, rounded
      Dr/Cr psp_fee_variance:{account}   the gap, ours
    a payout, as reported, whatever of its lines have arrived:
      Dr payout_in_transit:{account}     the net the PSP says it paid
      Cr psp_clearing:{account}          the same
    a change to a payout's rounding, once its lines add up (or no longer):
      Dr/Cr psp_clearing:{account}       the PSP's rounding of the net
      Cr/Dr rounding:{account}           the same

A PSP may count finer than we do (IDR at 2 decimals): a fee is rounded
here, half-even, the one place that rounds it (plan-payments.md §7),
and its exact value is kept on the line. The payout's check is exact,
never a tolerance: the reported net must be the lines' exact net rounded
one way or the other to our units. Only then is the difference rounding;
otherwise the gap stays in `psp_clearing` and the payout stays open.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction

from mor.audit import Actor
from mor.ids import JournalKey, PspAccountId, PspLineId, PspPayoutId
from mor.ledger.account import (
    Account,
    PayoutInTransit,
    PspClearing,
    PspFeeCollected,
    PspFeeVariance,
    Rounding,
)
from mor.ledger.entry import JournalEntry, Line, build_entry, credit, debit
from mor.money.amount import Money
from mor.money.rounding import RoundingMode, round_fraction


ROUNDING = RoundingMode.HALF_EVEN


def to_minor(exact: Fraction) -> int:
    return round_fraction(exact, ROUNDING)


def line_key(account_id: PspAccountId, vendor_line_id: PspLineId) -> JournalKey:
    return JournalKey(f"settlement:{account_id}:{vendor_line_id}")


def payout_key(account_id: PspAccountId, vendor_payout_id: PspPayoutId) -> JournalKey:
    return JournalKey(f"payout:{account_id}:{vendor_payout_id}")


def rounding_key(
    account_id: PspAccountId, vendor_payout_id: PspPayoutId, number: int
) -> JournalKey:
    """The `number`th change to a payout's rounding, counted from 1."""
    return JournalKey(f"payout-rounding:{account_id}:{vendor_payout_id}:{number}")


def _post(lines: list[Line], account: Account, amount: Money) -> None:
    """Debit a positive amount, credit a negative one, skip zero."""
    if amount.amount_minor > 0:
        lines.append(debit(account, amount))
    elif amount.amount_minor < 0:
        lines.append(credit(account, Money(-amount.amount_minor, amount.currency)))


def line_entry(
    account_id: PspAccountId,
    vendor_line_id: PspLineId,
    estimate: Money,
    fee: Money,
    effective_at: datetime,
    actor: Actor,
    now: datetime,
) -> JournalEntry | None:
    """The real fee clears the estimate. None when both are zero: nothing
    moved."""
    currency = fee.currency
    assert estimate.currency == currency
    lines: list[Line] = []
    _post(lines, PspFeeCollected(), estimate)
    _post(
        lines,
        PspClearing(account_id),
        Money(-fee.amount_minor, currency),
    )
    _post(
        lines,
        PspFeeVariance(account_id),
        Money(fee.amount_minor - estimate.amount_minor, currency),
    )
    if not lines:
        return None
    return build_entry(
        line_key(account_id, vendor_line_id),
        f"settled line {vendor_line_id}",
        effective_at,
        actor,
        lines,
        now,
    )


@dataclass(frozen=True)
class AddedUp:
    """The net is what the lines say, up to the PSP's own rounding to our
    units: `rounding` is that difference (reported less booked)."""

    rounding: int


@dataclass(frozen=True)
class NetDiffers:
    expected: Fraction  # the lines' exact net, in our minor units
    reported: Fraction


@dataclass(frozen=True)
class LineAmounts:
    gross: Fraction
    fee: Fraction


def check_net(reported: Fraction, lines: Sequence[LineAmounts]) -> AddedUp | NetDiffers:
    """Does the reported net add up? The exact net is the lines' gross less
    their exact fees; the PSP may round it either way to our units, never
    more. Booked, the lines leave their rounded gross less rounded fees in
    `psp_clearing`; the gap to the reported net is the rounding."""
    exact = Fraction(0)
    booked = 0
    for line in lines:
        exact += line.gross - line.fee
        booked += to_minor(line.gross) - to_minor(line.fee)
    if reported.denominator != 1 or (
        reported != math.floor(exact) and reported != math.ceil(exact)
    ):
        return NetDiffers(exact, reported)
    return AddedUp(int(reported) - booked)


def payout_entry(
    account_id: PspAccountId,
    vendor_payout_id: PspPayoutId,
    net: Money,
    effective_at: datetime,
    actor: Actor,
    now: datetime,
) -> JournalEntry | None:
    """The net leaves `psp_clearing` for the way to our bank, as reported.
    None for a payout of nothing."""
    lines: list[Line] = []
    _post(lines, PayoutInTransit(account_id), net)
    _post(
        lines,
        PspClearing(account_id),
        Money(-net.amount_minor, net.currency),
    )
    if not lines:
        return None
    return build_entry(
        payout_key(account_id, vendor_payout_id),
        f"payout {vendor_payout_id}",
        effective_at,
        actor,
        lines,
        now,
    )


def rounding_entry(
    account_id: PspAccountId,
    vendor_payout_id: PspPayoutId,
    number: int,
    change: Money,
    effective_at: datetime,
    actor: Actor,
    now: datetime,
) -> JournalEntry | None:
    """Move a payout's booked rounding by `change` (reported net less what
    its lines left in `psp_clearing`): a positive one is paid out beyond
    the lines, so `psp_clearing` gets it back from `rounding`. None for no
    change."""
    lines: list[Line] = []
    _post(lines, PspClearing(account_id), change)
    _post(
        lines,
        Rounding(account_id),
        Money(-change.amount_minor, change.currency),
    )
    if not lines:
        return None
    return build_entry(
        rounding_key(account_id, vendor_payout_id, number),
        f"rounding of payout {vendor_payout_id}",
        effective_at,
        actor,
        lines,
        now,
    )

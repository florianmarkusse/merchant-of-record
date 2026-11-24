from dataclasses import dataclass
from enum import StrEnum


class AccountKind(StrEnum):
    """Matches the `account_kind` enum type. The postings that use them are
    in plan-architecture.md §9."""

    PSP_CLEARING = "psp_clearing"
    TAX_PAYABLE = "tax_payable"
    DUTIES_PAYABLE = "duties_payable"
    SHIPPING_PAYABLE = "shipping_payable"
    MERCHANT_PAYABLE = "merchant_payable"
    PSP_FEE_COLLECTED = "psp_fee_collected"
    PSP_FEE_VARIANCE = "psp_fee_variance"
    REVENUE_MOR_MARGIN = "revenue_mor_margin"
    COURIER_PAYABLE = "courier_payable"
    COURIER_VARIANCE = "courier_variance"
    RESERVE = "reserve"
    ROUNDING = "rounding"
    # The buyer's currency on one side, the merchant's on the other: a
    # capture converts through it (docs/plan-payments.md §5).
    FX_POSITION = "fx_position"
    # What a PSP says it paid out and our bank has not shown yet
    # (docs/plan-payments.md §7, question 9).
    PAYOUT_IN_TRANSIT = "payout_in_transit"


@dataclass(frozen=True)
class Account:
    """A ledger account: a kind, narrowed by a scope such as a merchant id, a
    country or a courier (`tax_payable` for `SG`). Empty scope for accounts
    that have one instance."""

    kind: AccountKind
    scope: str = ""

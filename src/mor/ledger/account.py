"""Ledger accounts: one record per kind, holding exactly what narrows it
(the merchant, the country, the courier, the PSP account), so an account
cannot be built with a scope its kind does not have. The postings that use
them are in plan-architecture.md §9.

In the database an account is its kind and a text scope (`journal_line`),
made by `kind_of` and `scope_of`; nothing reads an account back from them.
"""

from dataclasses import dataclass
from enum import StrEnum

from mor.ids import MerchantId, PspAccountId


class AccountKind(StrEnum):
    """Matches the `account_kind` enum type."""

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
    FX_POSITION = "fx_position"
    PAYOUT_IN_TRANSIT = "payout_in_transit"


@dataclass(frozen=True)
class PspClearing:
    """What a PSP holds for us from captures, until it pays out."""

    psp_account_id: PspAccountId


@dataclass(frozen=True)
class TaxPayable:
    """Tax we collect ourselves and owe to `country`."""

    country: str


@dataclass(frozen=True)
class DutiesPayable:
    """Border duties and taxes, paid through the courier."""

    courier: str


@dataclass(frozen=True)
class ShippingPayable:
    """Freight, surcharges and fuel the buyer paid us for the courier."""

    courier: str


@dataclass(frozen=True)
class MerchantPayable:
    merchant_id: MerchantId


@dataclass(frozen=True)
class PspFeeCollected:
    """The estimated PSP fee the buyer paid, until settlement clears it."""


@dataclass(frozen=True)
class PspFeeVariance:
    """The real fee less the estimate, ours."""

    psp_account_id: PspAccountId


@dataclass(frozen=True)
class RevenueMorMargin:
    pass


@dataclass(frozen=True)
class CourierPayable:
    """What the courier bills us (slice 7)."""

    courier: str


@dataclass(frozen=True)
class CourierVariance:
    """A courier bill's difference, ours until a person attributes it."""

    courier: str


@dataclass(frozen=True)
class Reserve:
    """A rolling reserve held back from a merchant's payouts."""

    merchant_id: MerchantId


@dataclass(frozen=True)
class Rounding:
    """A PSP's rounding of a payout's net to our units
    (docs/plan-payments.md §7)."""

    psp_account_id: PspAccountId


@dataclass(frozen=True)
class FxPosition:
    """The buyer's currency on one side, the merchant's on the other: a
    capture converts through it (docs/plan-payments.md §5)."""


@dataclass(frozen=True)
class PayoutInTransit:
    """What a PSP says it paid out and our bank has not shown yet
    (docs/plan-payments.md §7)."""

    psp_account_id: PspAccountId


type Account = (
    PspClearing
    | TaxPayable
    | DutiesPayable
    | ShippingPayable
    | MerchantPayable
    | PspFeeCollected
    | PspFeeVariance
    | RevenueMorMargin
    | CourierPayable
    | CourierVariance
    | Reserve
    | Rounding
    | FxPosition
    | PayoutInTransit
)


def kind_of(account: Account) -> AccountKind:
    match account:
        case PspClearing():
            return AccountKind.PSP_CLEARING
        case TaxPayable():
            return AccountKind.TAX_PAYABLE
        case DutiesPayable():
            return AccountKind.DUTIES_PAYABLE
        case ShippingPayable():
            return AccountKind.SHIPPING_PAYABLE
        case MerchantPayable():
            return AccountKind.MERCHANT_PAYABLE
        case PspFeeCollected():
            return AccountKind.PSP_FEE_COLLECTED
        case PspFeeVariance():
            return AccountKind.PSP_FEE_VARIANCE
        case RevenueMorMargin():
            return AccountKind.REVENUE_MOR_MARGIN
        case CourierPayable():
            return AccountKind.COURIER_PAYABLE
        case CourierVariance():
            return AccountKind.COURIER_VARIANCE
        case Reserve():
            return AccountKind.RESERVE
        case Rounding():
            return AccountKind.ROUNDING
        case FxPosition():
            return AccountKind.FX_POSITION
        case PayoutInTransit():
            return AccountKind.PAYOUT_IN_TRANSIT


def scope_of(account: Account) -> str:
    """The text the `account_scope` column holds; empty for an account of
    which there is one."""
    match account:
        case (
            PspClearing(psp_account_id=psp_account_id)
            | PspFeeVariance(psp_account_id=psp_account_id)
            | Rounding(psp_account_id=psp_account_id)
            | PayoutInTransit(psp_account_id=psp_account_id)
        ):
            return str(psp_account_id)
        case (
            MerchantPayable(merchant_id=merchant_id) | Reserve(merchant_id=merchant_id)
        ):
            return str(merchant_id)
        case TaxPayable(country=country):
            return country
        case (
            DutiesPayable(courier=courier)
            | ShippingPayable(courier=courier)
            | CourierPayable(courier=courier)
            | CourierVariance(courier=courier)
        ):
            return courier
        case PspFeeCollected() | RevenueMorMargin() | FxPosition():
            return ""

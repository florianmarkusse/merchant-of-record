"""Ledger accounts: one record per kind, holding exactly what narrows it
(the merchant, the country, the carrier, the PSP account), so an account
cannot be built with a scope its kind does not have. The postings that use
them are in plan-architecture.md §9.

In the database an account is its kind and a text scope (`journal_line`),
made by `kind_of` and `scope_of`; nothing reads an account back from them.
"""

from dataclasses import dataclass
from enum import StrEnum

from mor.ids import BankAccountId, CarrierCode, CountryCode, MerchantId, PspAccountId


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
    CARRIER_PAYABLE = "carrier_payable"
    CARRIER_VARIANCE = "carrier_variance"
    RESERVE = "reserve"
    ROUNDING = "rounding"
    FX_POSITION = "fx_position"
    PAYOUT_IN_TRANSIT = "payout_in_transit"
    BANK = "bank"
    BANK_SUSPENSE = "bank_suspense"


@dataclass(frozen=True)
class PspClearing:
    """What a PSP holds for us from captures, until it pays out."""

    psp_account_id: PspAccountId


@dataclass(frozen=True)
class TaxPayable:
    """Tax we collect ourselves and owe to `country`."""

    country: CountryCode


@dataclass(frozen=True)
class DutiesPayable:
    """Border duties and taxes, paid through the carrier."""

    carrier: CarrierCode


@dataclass(frozen=True)
class ShippingPayable:
    """Freight, surcharges and fuel the buyer paid us for the carrier."""

    carrier: CarrierCode


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
class CarrierPayable:
    """What the carrier bills us (slice 7)."""

    carrier: CarrierCode


@dataclass(frozen=True)
class CarrierVariance:
    """A carrier bill's difference, ours until a person attributes it."""

    carrier: CarrierCode


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


@dataclass(frozen=True)
class Bank:
    """Money in one of our bank accounts, as its statement shows it: the
    only money merchants are paid from (docs/plan-payments.md §7)."""

    bank_account_id: BankAccountId


@dataclass(frozen=True)
class BankSuspense:
    """What a bank account shows that nothing explains yet: a credit not
    matched to a payout, a debit we did not make."""

    bank_account_id: BankAccountId


type Account = (
    PspClearing
    | TaxPayable
    | DutiesPayable
    | ShippingPayable
    | MerchantPayable
    | PspFeeCollected
    | PspFeeVariance
    | RevenueMorMargin
    | CarrierPayable
    | CarrierVariance
    | Reserve
    | Rounding
    | FxPosition
    | PayoutInTransit
    | Bank
    | BankSuspense
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
        case CarrierPayable():
            return AccountKind.CARRIER_PAYABLE
        case CarrierVariance():
            return AccountKind.CARRIER_VARIANCE
        case Reserve():
            return AccountKind.RESERVE
        case Rounding():
            return AccountKind.ROUNDING
        case FxPosition():
            return AccountKind.FX_POSITION
        case PayoutInTransit():
            return AccountKind.PAYOUT_IN_TRANSIT
        case Bank():
            return AccountKind.BANK
        case BankSuspense():
            return AccountKind.BANK_SUSPENSE


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
        case (
            Bank(bank_account_id=bank_account_id)
            | BankSuspense(bank_account_id=bank_account_id)
        ):
            return str(bank_account_id)
        case TaxPayable(country=country):
            return country
        case (
            DutiesPayable(carrier=carrier)
            | ShippingPayable(carrier=carrier)
            | CarrierPayable(carrier=carrier)
            | CarrierVariance(carrier=carrier)
        ):
            return carrier
        case PspFeeCollected() | RevenueMorMargin() | FxPosition():
            return ""

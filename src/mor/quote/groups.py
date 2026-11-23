"""The four groups a buyer sees a price in (plan-architecture §8): items
(the merchant's price and our fees), shipping, and duties and taxes. The
buyer page, the estimate API, the staff pages and the invoice all group
quote parts by this one table, so the kinds of part live here too."""

from enum import StrEnum
from typing import Literal


class PartKind(StrEnum):
    GOODS = "goods"
    PSP_FEE = "psp_fee"
    FX = "fx"
    MOR_MARGIN = "mor_margin"
    FREIGHT = "freight"
    SURCHARGE = "surcharge"
    FUEL = "fuel"
    DUTY_PAID_FEE = "duty_paid_fee"
    DUTY = "duty"
    ADDITIONAL_DUTY = "additional_duty"
    CUSTOMS_FEE = "customs_fee"
    IMPORT_TAX = "import_tax"
    OTHER_TAX = "other_tax"


type PartGroup = Literal["goods", "fees", "shipping", "duties_and_taxes"]


def group_of(kind: PartKind) -> PartGroup:
    match kind:
        case PartKind.GOODS:
            return "goods"
        case PartKind.PSP_FEE | PartKind.FX | PartKind.MOR_MARGIN:
            return "fees"
        case (
            PartKind.FREIGHT
            | PartKind.SURCHARGE
            | PartKind.FUEL
            | PartKind.DUTY_PAID_FEE
        ):
            return "shipping"
        case (
            PartKind.DUTY
            | PartKind.ADDITIONAL_DUTY
            | PartKind.CUSTOMS_FEE
            | PartKind.IMPORT_TAX
            | PartKind.OTHER_TAX
        ):
            return "duties_and_taxes"

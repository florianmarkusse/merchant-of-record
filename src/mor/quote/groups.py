"""The four groups a buyer sees a price in (plan-architecture §8): items
(the merchant's price and our fees), shipping, and duties and taxes. The
buyer page, the estimate API, the staff pages and the invoice all group
quote parts by this one table."""

from typing import Literal, assert_never

from mor.quote.build import PartKind


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
        case _:
            assert_never(kind)

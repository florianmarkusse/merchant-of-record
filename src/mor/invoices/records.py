"""The invoice: a snapshot of what was sold, issued in our name with the
merchant named, never re-derived and never changed (docs/plan-payments.md
§6, §6.1)."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from mor.money.currency import Currency


class InvoiceSection(StrEnum):
    """Matches the `invoice_section` enum type."""

    GOODS = "goods"  # one per order line, our fee inside
    SHIPPING = "shipping"  # one
    TAX = "tax"  # what we collect and file
    BORDER = "border"  # duty and tax paid at the border on the buyer's behalf


@dataclass(frozen=True, kw_only=True)
class InvoiceLine:
    section: InvoiceSection
    description: str
    quantity: int | None  # goods lines only
    amount_minor: int  # in the invoice's currency
    rule_text: str | None  # taxes: how the quote reached it, rate inside
    source: str | None  # taxes: where the rate came from


@dataclass(frozen=True, kw_only=True)
class BuyerAddress:
    """Copied from the session's address. No email or phone."""

    name: str
    line1: str
    line2: str | None
    city: str
    region: str | None
    postcode: str | None
    country: str


@dataclass(frozen=True, kw_only=True)
class Series:
    """A numbering series, as `invoice_series` holds it."""

    code: str
    entity: str
    prefix: str
    with_year: bool
    pad_width: int


@dataclass(frozen=True, kw_only=True)
class Invoice:
    id: uuid.UUID
    order_id: uuid.UUID
    series: str
    number: int  # gapless within its series
    number_text: str  # as printed, stored whole: `INV-2026-000001`
    issued_at: datetime
    paid_at: datetime
    currency: Currency
    total_minor: int
    seller_name: str
    seller_address: str
    seller_tax_label: str | None
    seller_tax_id: str | None
    merchant_legal_name: str
    buyer: BuyerAddress
    lines: tuple[InvoiceLine, ...]

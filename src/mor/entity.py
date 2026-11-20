"""The selling entity: who the buyer buys from, as the invoice and the
buyer's pages name it (docs/plan-payments.md §6.1). Plain data, in one
place. Every value is a placeholder until the entity is decided
(plan-psp-integration §11) and registered; what a tax invoice must show
per country waits for an adviser (docs/plan-payments.md §9)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TaxRegistration:
    country: str
    label: str  # as the invoice prints it
    number: str


@dataclass(frozen=True)
class SellingEntity:
    code: str  # names its invoice series (`invoice_series.entity`)
    legal_name: str
    address: str
    support_email: str
    registrations: tuple[TaxRegistration, ...]


ENTITY = SellingEntity(
    code="placeholder",
    legal_name="[our company]",
    address="[our registered address]",
    support_email="[our support address]",
    registrations=(
        TaxRegistration("SG", "Tax registration", "[to be registered]"),
        TaxRegistration("MY", "Tax registration", "[to be registered]"),
        TaxRegistration("TH", "Tax registration", "[to be registered]"),
        TaxRegistration("VN", "Tax registration", "[to be registered]"),
        TaxRegistration("ID", "Tax registration", "[to be registered]"),
        TaxRegistration("PH", "Tax registration", "[to be registered]"),
    ),
)


def registration_for(entity: SellingEntity, country: str) -> TaxRegistration | None:
    """The registration an invoice to a buyer in `country` shows, if any."""
    for registration in entity.registrations:
        if registration.country == country:
            return registration
    return None

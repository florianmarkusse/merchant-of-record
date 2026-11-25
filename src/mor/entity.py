"""The selling entity: who the buyer buys from, as the invoice and the
buyer's pages name it (docs/plan-payments.md §6). Plain data, in one
place. Every value is a placeholder until the entity is decided
(plan-psp-integration.md §10) and registered; what a tax invoice must show
per country waits for an adviser (docs/plan-payments.md §9)."""

from dataclasses import dataclass

from mor.ids import CountryCode


@dataclass(frozen=True)
class TaxRegistration:
    country: CountryCode
    label: str  # as the invoice prints it
    number: str


@dataclass(frozen=True, kw_only=True)
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
        TaxRegistration(CountryCode("SG"), "Tax registration", "[to be registered]"),
        TaxRegistration(CountryCode("MY"), "Tax registration", "[to be registered]"),
        TaxRegistration(CountryCode("TH"), "Tax registration", "[to be registered]"),
        TaxRegistration(CountryCode("VN"), "Tax registration", "[to be registered]"),
        TaxRegistration(CountryCode("ID"), "Tax registration", "[to be registered]"),
        TaxRegistration(CountryCode("PH"), "Tax registration", "[to be registered]"),
    ),
)


def registration_for(
    entity: SellingEntity, country: CountryCode
) -> TaxRegistration | None:
    """The registration an invoice to a buyer in `country` shows, if any."""
    for registration in entity.registrations:
        if registration.country == country:
            return registration
    return None

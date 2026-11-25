"""A buyer's delivery address: parsed and checked by format, per country
(docs/plan-quote.md §5, 4d-2, answered 19-Nov-2026). Pure; no lookup
service (none covers the region: Google's Address Validation API has SG
and MY but not TH, ID, PH or VN).

Modelled on fruggals' `src/addresses.py`: one function from form fields to
an address or one error per field, and the phone's dial code kept apart
from its number. Unlike there, the street is two free lines (Thai,
Vietnamese and Indonesian addresses do not split into street and house
number), and each country's postcode and region rule is a row of data.

Personal data: never logged.
"""

import re
from dataclasses import dataclass
from typing import Literal

from mor.ids import CountryCode


@dataclass(frozen=True, kw_only=True)
class AddressRule:
    country: CountryCode
    dial_code: str  # the default shown for the phone
    postcode: re.Pattern[str] | None  # None: the country has none we ask for
    postcode_required: bool
    region_required: bool  # a province or state on the label


# Postcode formats checked 19-Nov-2026 (SingPost via the postal-code
# references for SG; postal-code tables for MY, TH, ID, PH). VN's six
# digits are not confirmed and rarely written, so it is optional there.
# A country is offered only with a row here and open in the market data.
ADDRESS_RULES = (
    AddressRule(
        country=CountryCode("SG"),
        dial_code="+65",
        postcode=re.compile(r"^\d{6}$"),
        postcode_required=True,
        region_required=False,
    ),
    AddressRule(
        country=CountryCode("MY"),
        dial_code="+60",
        postcode=re.compile(r"^\d{5}$"),
        postcode_required=True,
        region_required=True,
    ),
    AddressRule(
        country=CountryCode("TH"),
        dial_code="+66",
        postcode=re.compile(r"^\d{5}$"),
        postcode_required=True,
        region_required=True,
    ),
    AddressRule(
        country=CountryCode("ID"),
        dial_code="+62",
        postcode=re.compile(r"^\d{5}$"),
        postcode_required=True,
        region_required=True,
    ),
    AddressRule(
        country=CountryCode("PH"),
        dial_code="+63",
        postcode=re.compile(r"^\d{4}$"),
        postcode_required=True,
        region_required=True,
    ),
    AddressRule(
        country=CountryCode("VN"),
        dial_code="+84",
        postcode=re.compile(r"^\d{6}$"),
        postcode_required=False,
        region_required=True,
    ),
)


def address_rule(country: CountryCode) -> AddressRule | None:
    for rule in ADDRESS_RULES:
        if rule.country == country:
            return rule
    return None


@dataclass(frozen=True, kw_only=True)
class Address:
    name: str
    email: str
    phone_dial_code: str  # "+66"
    phone_number: str  # digits only, without the trunk 0
    line1: str
    line2: str | None
    city: str
    region: str | None
    postcode: str | None
    country: CountryCode


# Form field names, shared by the page and the parser.
NAME = "name"
EMAIL = "email"
DIAL_CODE = "phone_dial_code"
PHONE = "phone"
LINE1 = "line1"
LINE2 = "line2"
CITY = "city"
REGION = "region"
POSTCODE = "postcode"
COUNTRY = "country"
FIELDS = (NAME, EMAIL, DIAL_CODE, PHONE, LINE1, LINE2, CITY, REGION, POSTCODE, COUNTRY)

type AddressError = Literal[
    "required", "too_long", "email", "phone", "postcode", "country"
]
type AddressErrors = dict[str, AddressError]

MAX_FIELD = 200
_DIAL_CODE = re.compile(r"^\+\d{1,4}$")
_PHONE_DIGITS = re.compile(r"^\d{4,15}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _text(values: dict[str, str], field: str) -> str:
    return " ".join(values.get(field, "").split())


def parse_address(
    values: dict[str, str], countries: frozenset[CountryCode]
) -> Address | AddressErrors:
    """The submitted fields as an address, or one error per bad field.
    `countries`: those this cart may be sent to (open, with a rule)."""
    errors: AddressErrors = {}
    texts: dict[str, str] = {}
    for field in FIELDS:
        text = _text(values, field)
        if len(text) > MAX_FIELD:
            errors[field] = "too_long"
        texts[field] = text
    for field in (NAME, EMAIL, PHONE, LINE1, CITY):
        if not texts[field] and field not in errors:
            errors[field] = "required"

    country = CountryCode(texts[COUNTRY].upper())
    rule = address_rule(country)
    if rule is None or country not in countries:
        errors[COUNTRY] = "country"

    email = texts[EMAIL]
    if email and EMAIL not in errors and (len(email) > 254 or not _EMAIL.match(email)):
        errors[EMAIL] = "email"

    dial_code = texts[DIAL_CODE] or (rule.dial_code if rule is not None else "")
    number = re.sub(r"[\s.()-]", "", texts[PHONE])
    if number.startswith(dial_code) and dial_code:
        number = number.removeprefix(dial_code)
    number = number.removeprefix("0")  # the trunk prefix of a national number
    # Without a country there is no dial code to check against: the
    # country's own error says what to fix first.
    if texts[PHONE] and PHONE not in errors and dial_code:
        if not _DIAL_CODE.match(dial_code) or not _PHONE_DIGITS.match(number):
            errors[PHONE] = "phone"

    postcode = texts[POSTCODE].replace(" ", "") or None
    region = texts[REGION] or None
    if rule is not None:
        if postcode is None and rule.postcode_required:
            errors[POSTCODE] = "required"
        elif postcode is not None and (
            rule.postcode is None or not rule.postcode.match(postcode)
        ):
            errors[POSTCODE] = "postcode"
        if region is None and rule.region_required:
            errors[REGION] = "required"

    if errors:
        return errors
    return Address(
        name=texts[NAME],
        email=email,
        phone_dial_code=dial_code,
        phone_number=number,
        line1=texts[LINE1],
        line2=texts[LINE2] or None,
        city=texts[CITY],
        region=region,
        postcode=postcode,
        country=country,
    )

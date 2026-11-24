"""The buyer's address, checked by format per country (docs/plan-quote.md
§5, 4d-2). Every address here is made up."""

from hypothesis import given, strategies

from mor.checkout import addresses
from mor.checkout.addresses import Address, parse_address


OFFERED = frozenset({"SG", "TH"})
SG = {
    addresses.NAME: "  Tan  Mei Ling ",
    addresses.EMAIL: "mei@example.test",
    addresses.PHONE: "9123 4567",
    addresses.LINE1: "1 Example Road, #02-03",
    addresses.CITY: "Singapore",
    addresses.POSTCODE: "123456",
    addresses.COUNTRY: "sg",
}
TH = {
    addresses.NAME: "Somchai Example",
    addresses.EMAIL: "somchai@example.test",
    addresses.DIAL_CODE: "+66",
    addresses.PHONE: "081-234-5678",
    addresses.LINE1: "99/1 Soi Example, Sukhumvit Road",
    addresses.CITY: "Khlong Toei",
    addresses.REGION: "Bangkok",
    addresses.POSTCODE: "10110",
    addresses.COUNTRY: "TH",
}


def test_an_address_is_tidied_and_its_phone_split_from_its_dial_code() -> None:
    assert parse_address(SG, OFFERED) == Address(
        name="Tan Mei Ling",
        email="mei@example.test",
        phone_dial_code="+65",
        phone_number="91234567",
        line1="1 Example Road, #02-03",
        line2=None,
        city="Singapore",
        region=None,
        postcode="123456",
        country="SG",
    )
    parsed = parse_address(TH, OFFERED)
    assert isinstance(parsed, Address)
    # The trunk 0 of a national number goes; the dial code carries it.
    assert (parsed.phone_dial_code, parsed.phone_number) == ("+66", "812345678")
    with_code = parse_address(TH | {addresses.PHONE: "+66 81 234 5678"}, OFFERED)
    assert isinstance(with_code, Address) and with_code.phone_number == "812345678"


def test_each_bad_field_is_named() -> None:
    assert parse_address({}, OFFERED) == {
        addresses.NAME: "required",
        addresses.EMAIL: "required",
        addresses.PHONE: "required",
        addresses.LINE1: "required",
        addresses.CITY: "required",
        addresses.COUNTRY: "country",
    }
    assert parse_address(TH | {addresses.POSTCODE: "1011"}, OFFERED) == {
        addresses.POSTCODE: "postcode"
    }
    assert parse_address(TH | {addresses.REGION: ""}, OFFERED) == {
        addresses.REGION: "required"
    }
    assert parse_address(SG | {addresses.EMAIL: "mei@"}, OFFERED) == {
        addresses.EMAIL: "email"
    }
    assert parse_address(SG | {addresses.PHONE: "call me"}, OFFERED) == {
        addresses.PHONE: "phone"
    }
    assert parse_address(SG | {addresses.NAME: "x" * 201}, OFFERED) == {
        addresses.NAME: "too_long"
    }


def test_only_a_country_offered_for_this_cart_may_be_chosen() -> None:
    assert parse_address(SG, frozenset({"TH"})) == {addresses.COUNTRY: "country"}
    # Offered by the market but with no address rule: refused too.
    assert parse_address(SG | {addresses.COUNTRY: "DE"}, frozenset({"DE"})) == {
        addresses.COUNTRY: "country"
    }


form_values = strategies.dictionaries(
    strategies.sampled_from(addresses.FIELDS + ("other",)),
    strategies.text(max_size=40)
    | strategies.sampled_from(("SG", "TH", "+66", "123456", "a@b.co")),
    max_size=12,
)


@given(values=form_values)
def test_any_form_gives_an_address_or_errors_by_field(values: dict[str, str]) -> None:
    parsed = parse_address(values, OFFERED)
    if isinstance(parsed, Address):
        assert parsed.country in OFFERED
        assert parsed.phone_number.isdigit()
    else:
        assert parsed
        for field in parsed:
            assert field in addresses.FIELDS

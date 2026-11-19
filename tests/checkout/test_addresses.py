"""The buyer's address, checked by format per country (docs/plan-quote.md
§5, 4d-2). Every address here is made up."""

from hypothesis import given
from hypothesis import strategies as st

from mor.checkout import addresses as f
from mor.checkout.addresses import Address, parse_address


OFFERED = frozenset({"SG", "TH"})
SG = {
    f.NAME: "  Tan  Mei Ling ",
    f.EMAIL: "mei@example.test",
    f.PHONE: "9123 4567",
    f.LINE1: "1 Example Road, #02-03",
    f.CITY: "Singapore",
    f.POSTCODE: "123456",
    f.COUNTRY: "sg",
}
TH = {
    f.NAME: "Somchai Example",
    f.EMAIL: "somchai@example.test",
    f.DIAL_CODE: "+66",
    f.PHONE: "081-234-5678",
    f.LINE1: "99/1 Soi Example, Sukhumvit Road",
    f.CITY: "Khlong Toei",
    f.REGION: "Bangkok",
    f.POSTCODE: "10110",
    f.COUNTRY: "TH",
}


def test_an_address_is_tidied_and_its_phone_split_from_its_dial_code() -> None:
    assert parse_address(SG, OFFERED) == Address(
        "Tan Mei Ling",
        "mei@example.test",
        "+65",
        "91234567",
        "1 Example Road, #02-03",
        None,
        "Singapore",
        None,
        "123456",
        "SG",
    )
    parsed = parse_address(TH, OFFERED)
    assert isinstance(parsed, Address)
    # The trunk 0 of a national number goes; the dial code carries it.
    assert (parsed.phone_dial_code, parsed.phone_number) == ("+66", "812345678")
    with_code = parse_address(TH | {f.PHONE: "+66 81 234 5678"}, OFFERED)
    assert isinstance(with_code, Address) and with_code.phone_number == "812345678"


def test_each_bad_field_is_named() -> None:
    assert parse_address({}, OFFERED) == {
        f.NAME: "required",
        f.EMAIL: "required",
        f.PHONE: "required",
        f.LINE1: "required",
        f.CITY: "required",
        f.COUNTRY: "country",
    }
    assert parse_address(TH | {f.POSTCODE: "1011"}, OFFERED) == {f.POSTCODE: "postcode"}
    assert parse_address(TH | {f.REGION: ""}, OFFERED) == {f.REGION: "required"}
    assert parse_address(SG | {f.EMAIL: "mei@"}, OFFERED) == {f.EMAIL: "email"}
    assert parse_address(SG | {f.PHONE: "call me"}, OFFERED) == {f.PHONE: "phone"}
    assert parse_address(SG | {f.NAME: "x" * 201}, OFFERED) == {f.NAME: "too_long"}


def test_only_a_country_offered_for_this_cart_may_be_chosen() -> None:
    assert parse_address(SG, frozenset({"TH"})) == {f.COUNTRY: "country"}
    # Offered by the market but with no address rule: refused too.
    assert parse_address(SG | {f.COUNTRY: "DE"}, frozenset({"DE"})) == {
        f.COUNTRY: "country"
    }


form_values = st.dictionaries(
    st.sampled_from(f.FIELDS + ("other",)),
    st.text(max_size=40) | st.sampled_from(("SG", "TH", "+66", "123456", "a@b.co")),
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
            assert field in f.FIELDS

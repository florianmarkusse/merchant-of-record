"""Properties of the merchant API's edge and estimates (docs/plan-quote.md
§5, 4d-1):

- any bytes decode to a request record or an `invalid_request` error,
  never an exception, for every body the API takes;
- any JSON value does too, so the decoder is also checked beyond bytes
  that are not even JSON;
- every estimate made from a quote lists, per option, parts that sum to
  its total in the quote's currency, with the goods and fees of the
  quote in every option; in prod, a quote on a placeholder is refused
  as ours to fix, or its placeholder services are left out.
"""

from datetime import UTC, datetime

import msgspec
from hypothesis import given, strategies

from mor.config import Environment
from mor.couriers.records import Parcel
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.quote.build import Quote, QuoteLine, QuoteRequest, build_quote
from mor.quote.refusals import Refusal, offered_in
from mor.web.api_estimates import estimate_out
from mor.web.api_wire import ApiError, ErrorType, EstimateIn, SessionIn, decode
from tests.quote.fixtures import quote_market


SYNTHETIC = quote_market()
NOW = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)

json_values = strategies.recursive(
    strategies.none()
    | strategies.booleans()
    | strategies.integers(min_value=-(2**70), max_value=2**70)
    | strategies.floats(allow_nan=False)
    | strategies.text(max_size=20),
    lambda inner: (
        strategies.lists(inner, max_size=4)
        | strategies.dictionaries(
            strategies.sampled_from(
                (
                    "lines",
                    "parcel",
                    "country",
                    "return_url",
                    "cancel_url",
                    "name",
                    "quantity",
                    "unit_price",
                    "amount_minor",
                    "currency",
                    "category",
                    "customs",
                    "weight_g",
                    "x",
                )
            ),
            inner,
            max_size=5,
        )
    ),
    max_leaves=20,
)


def _decodes_or_refuses(body: bytes) -> None:
    for record in (SessionIn, EstimateIn):
        decoded = decode(body, record)
        if isinstance(decoded, ApiError):
            assert decoded.status == 400
            assert decoded.type == ErrorType.INVALID_REQUEST
        else:
            assert isinstance(decoded, record)


@given(body=strategies.binary(max_size=300))
def test_any_bytes_decode_to_a_request_or_a_typed_error(body: bytes) -> None:
    _decodes_or_refuses(body)


@given(value=json_values)
def test_any_json_decodes_to_a_request_or_a_typed_error(value: object) -> None:
    _decodes_or_refuses(msgspec.json.encode(value))


lines = strategies.lists(
    strategies.builds(
        QuoteLine,
        category=strategies.just("apparel"),
        hs_code=strategies.sampled_from((None, "610910")),
        origin=strategies.just("VN"),
        quantity=strategies.integers(min_value=1, max_value=20),
        unit_price=strategies.builds(
            Money,
            amount_minor=strategies.integers(min_value=0, max_value=50_000_000),
            currency=strategies.sampled_from(
                (Currency.SGD, Currency.USD, Currency.VND)
            ),
        ),
    ),
    min_size=1,
    max_size=4,
)
parcels = strategies.builds(
    Parcel,
    weight_g=strategies.integers(min_value=1, max_value=40_000),
    length_mm=strategies.integers(min_value=1, max_value=800),
    width_mm=strategies.integers(min_value=1, max_value=800),
    height_mm=strategies.integers(min_value=1, max_value=800),
)


@given(
    destination=strategies.sampled_from(("SG", "TH")),
    order=lines,
    parcel=parcels,
    prod=strategies.booleans(),
)
def test_every_estimate_option_is_its_parts_summed(
    destination: str, order: list[QuoteLine], parcel: Parcel, prod: bool
) -> None:
    request = QuoteRequest(
        destination=destination,
        lines=tuple(order),
        parcel=parcel,
        on=NOW.date(),
        now=NOW,
    )
    result = build_quote(SYNTHETIC, request, {}, {})
    if not isinstance(result, Quote):
        return
    environment = Environment.PROD if prod else Environment.TEST
    offered = offered_in(result, environment)
    if isinstance(offered, Refusal):
        assert prod and offered.code in ("pricing_unavailable", "no_service_for_lane")
        return
    estimate = msgspec.json.decode(estimate_out(offered))
    assert estimate["currency"] == result.currency.value
    goods_total = 0
    for part in result.goods:
        goods_total += part.amount.amount_minor
    for option in estimate["options"]:
        total = 0
        of_goods_and_fees = 0
        for part in option["parts"]:
            assert part["amount"]["currency"] == result.currency.value
            total += part["amount"]["amount_minor"]
            if part["group"] in ("goods", "fees"):
                of_goods_and_fees += part["amount"]["amount_minor"]
        assert total == option["total"]["amount_minor"]
        assert of_goods_and_fees == goods_total

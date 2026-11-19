"""`POST /v1/estimates` answered from a quote (docs/plan-quote.md §5,
4d-1): the landed price per shipping option with its build-up, or the
refusal as an API error. Pure: the handler gathers the inputs.

Every option lists all its parts (the merchant's price per line, each fee
component, shipping, and each duty and tax with who collects it), and
they sum to its total.
"""

from typing import Literal, assert_never

import msgspec

from mor.config import Environment
from mor.markets.fx import FxMissing
from mor.markets.records import Collector, Standing
from mor.markets.rules import NotListed, NotOpen, RatesMissing, RulesNotConfirmed
from mor.quote.build import (
    CountryClosed,
    LineRateRefused,
    NoCurrency,
    NoFeeRule,
    NoServiceForLane,
    Part,
    PartKind,
    Quote,
    QuoteRefusal,
)
from mor.tax.line_rates import (
    RateNeeded,
    RateNotConfirmed,
    VendorNeedsPerson,
    VendorUnknownCode,
)
from mor.web.api_wire import ApiError, ErrorType, MoneyOut, money_out


type Group = Literal["goods", "fees", "shipping", "duties_and_taxes"]


def group_of(kind: PartKind) -> Group:
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


def collected_by(collector: Collector | None) -> str | None:
    """`customs`: paid at the border (through the carrier, duty paid);
    `us`: we collect it and file it ourselves."""
    match collector:
        case None:
            return None
        case Collector.BORDER:
            return "customs"
        case Collector.SELLER:
            return "us"
        case _:
            assert_never(collector)


class PartOut(msgspec.Struct, frozen=True):
    group: str
    kind: str
    line: int | None
    amount: MoneyOut
    collected_by: str | None


class TransitOut(msgspec.Struct, frozen=True):
    min_days: int
    max_days: int


class OptionOut(msgspec.Struct, frozen=True):
    key: str
    service: str
    name: str
    tier: str
    transit: TransitOut | None
    total: MoneyOut
    parts: list[PartOut]


class NotOfferedOut(msgspec.Struct, frozen=True):
    key: str
    reason: str


class EstimateOut(msgspec.Struct, frozen=True):
    object: str
    country: str
    currency: str
    options: list[OptionOut]
    not_offered: list[NotOfferedOut]
    placeholders: list[str]


def _part(part: Part) -> PartOut:
    return PartOut(
        group_of(part.kind),
        part.kind.value,
        part.line,
        money_out(part.amount),
        collected_by(part.collector),
    )


def estimate_out(quote: Quote, environment: Environment) -> bytes | ApiError:
    """The quote as an estimate. In prod a quote standing on a placeholder
    is not answered: a placeholder fee rule refuses it, and a placeholder
    service is left out (decided 17-Nov-2026, plan-quote Q5)."""
    prod = environment == Environment.PROD
    if prod and quote.placeholders:
        return ApiError(
            503,
            ErrorType.UNAVAILABLE,
            "pricing_unavailable",
            "pricing for this country is not set up yet",
        )
    goods: list[PartOut] = []
    for part in quote.goods:
        goods.append(_part(part))
    options: list[OptionOut] = []
    not_offered: list[NotOfferedOut] = []
    for option in quote.options:
        if prod and option.standing == Standing.PLACEHOLDER:
            not_offered.append(NotOfferedOut(option.key, "placeholder"))
            continue
        parts = list(goods)
        for part in option.parts:
            parts.append(_part(part))
        transit: TransitOut | None = None
        if option.transit_days is not None:
            transit = TransitOut(option.transit_days[0], option.transit_days[1])
        options.append(
            OptionOut(
                option.key,
                option.service,
                option.service_name,
                option.tier.value,
                transit,
                money_out(option.total),
                parts,
            )
        )
    for refused in quote.not_offered:
        not_offered.append(NotOfferedOut(refused.key, refused.reason))
    if not options:
        return _no_service()
    placeholders: list[str] = []
    if not prod:
        placeholders = list(quote.placeholders)
    out = EstimateOut(
        "estimate",
        quote.destination,
        quote.currency.value,
        options,
        not_offered,
        placeholders,
    )
    return msgspec.json.encode(out)


def _no_service() -> ApiError:
    return ApiError(
        422,
        ErrorType.QUOTE_REFUSED,
        "no_service_for_lane",
        "no shipping service carries this order to this country",
    )


def _refused(
    code: Literal["country_closed", "duty_rate_unavailable"],
    message: str,
    param: str | None = None,
) -> ApiError:
    return ApiError(422, ErrorType.QUOTE_REFUSED, code, message, param)


def _unavailable(message: str) -> ApiError:
    return ApiError(503, ErrorType.UNAVAILABLE, "pricing_unavailable", message)


def refusal_error(refusal: QuoteRefusal, country: str) -> ApiError:
    match refusal:
        case CountryClosed(category=category, closed=closed):
            match closed:
                case NotListed():
                    return ApiError(
                        422,
                        ErrorType.QUOTE_REFUSED,
                        "country_not_supported",
                        f"we do not sell into {country}",
                        "country",
                    )
                case NotOpen(note=note):
                    return _refused(
                        "country_closed", f"{country} is not open: {note}", "country"
                    )
                case RulesNotConfirmed() | RatesMissing():
                    return _refused(
                        "country_closed",
                        f"{category} into {country} is not open yet",
                        "country",
                    )
                case _:
                    assert_never(closed)
        case NoCurrency():
            return ApiError(
                422,
                ErrorType.QUOTE_REFUSED,
                "country_not_supported",
                f"we cannot charge in {country}'s currency",
                "country",
            )
        case FxMissing(base=base, quote=quote):
            return _unavailable(f"no exchange rate from {base} to {quote}")
        case NoFeeRule(component=component):
            return _unavailable(f"no {component.value} rule for {country}")
        case LineRateRefused(line=line, charge=charge, problem=problem):
            match problem:
                case RateNeeded(reason="no_hs_code"):
                    return ApiError(
                        422,
                        ErrorType.QUOTE_REFUSED,
                        "classification_needed",
                        f"{country} needs an HS code to price {charge.value}",
                        f"lines[{line}].customs.hs_code",
                    )
                case RateNeeded() | RateNotConfirmed():
                    return _refused(
                        "duty_rate_unavailable",
                        f"no confirmed {charge.value} rate for this line in {country}",
                        f"lines[{line}]",
                    )
                case VendorUnknownCode():
                    return _refused(
                        "duty_rate_unavailable",
                        f"the HS code is not known for {country}",
                        f"lines[{line}].customs.hs_code",
                    )
                case VendorNeedsPerson():
                    return _refused(
                        "duty_rate_unavailable",
                        f"the {charge.value} rate for this line needs checking",
                        f"lines[{line}]",
                    )
                case _:
                    assert_never(problem)
        case NoServiceForLane():
            return _no_service()
        case _:
            assert_never(refusal)

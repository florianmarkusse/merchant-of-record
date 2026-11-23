"""`POST /v1/estimates` answered from a quote (docs/plan-quote.md §5,
4d-1): the landed price per shipping option with its build-up, or the
refusal as an API error. Pure: the handler gathers the inputs.

Every option lists all its parts (the merchant's price per line, each fee
component, shipping, and each duty and tax with who collects it), and
they sum to its total.
"""

from typing import Literal

import msgspec

from mor.markets.records import Collector, ServiceTier
from mor.money.currency import Currency
from mor.quote.build import Part, Quote
from mor.quote.groups import PartGroup, PartKind, group_of
from mor.quote.refusals import Refusal
from mor.quote.shipping import NotOfferedReason
from mor.web.api_wire import ApiError, ErrorType, MoneyOut, money_out


def collected_by(collector: Collector | None) -> Literal["customs", "us"] | None:
    """`customs`: paid at the border (through the carrier, duty paid);
    `us`: we collect it and file it ourselves."""
    match collector:
        case None:
            return None
        case Collector.BORDER:
            return "customs"
        case Collector.SELLER:
            return "us"


class PartOut(msgspec.Struct, frozen=True):
    group: PartGroup
    kind: PartKind
    line: int | None
    amount: MoneyOut
    collected_by: Literal["customs", "us"] | None


class TransitOut(msgspec.Struct, frozen=True):
    min_days: int
    max_days: int


class OptionOut(msgspec.Struct, frozen=True):
    key: str
    service: str
    name: str
    tier: ServiceTier
    transit: TransitOut | None
    total: MoneyOut
    parts: list[PartOut]


class NotOfferedOut(msgspec.Struct, frozen=True):
    key: str
    reason: NotOfferedReason


class EstimateOut(msgspec.Struct, frozen=True):
    object: str
    country: str
    currency: Currency
    options: list[OptionOut]
    not_offered: list[NotOfferedOut]
    placeholders: list[str]


def _part(part: Part) -> PartOut:
    return PartOut(
        group=group_of(part.kind),
        kind=part.kind,
        line=part.line,
        amount=money_out(part.amount),
        collected_by=collected_by(part.collector),
    )


def estimate_out(quote: Quote) -> bytes:
    """A quote as an estimate: `offered_in` has already applied what the
    environment refuses."""
    goods: list[PartOut] = []
    for part in quote.goods:
        goods.append(_part(part))
    options: list[OptionOut] = []
    for option in quote.options:
        parts = list(goods)
        for part in option.parts:
            parts.append(_part(part))
        transit: TransitOut | None = None
        if option.transit_days is not None:
            transit = TransitOut(option.transit_days[0], option.transit_days[1])
        options.append(
            OptionOut(
                key=option.key,
                service=option.service,
                name=option.service_name,
                tier=option.tier,
                transit=transit,
                total=money_out(option.total),
                parts=parts,
            )
        )
    not_offered: list[NotOfferedOut] = []
    for refused in quote.not_offered:
        not_offered.append(NotOfferedOut(refused.key, refused.reason))
    out = EstimateOut(
        object="estimate",
        country=quote.destination,
        currency=quote.currency,
        options=options,
        not_offered=not_offered,
        placeholders=list(quote.placeholders),
    )
    return msgspec.json.encode(out)


def refusal_error(refusal: Refusal) -> ApiError:
    if refusal.ours:
        return ApiError(
            status=503,
            type=ErrorType.UNAVAILABLE,
            code=refusal.code,
            message=refusal.message,
            param=refusal.param,
        )
    return ApiError(
        status=422,
        type=ErrorType.QUOTE_REFUSED,
        code=refusal.code,
        message=refusal.message,
        param=refusal.param,
    )

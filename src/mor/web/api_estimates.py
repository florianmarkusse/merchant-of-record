"""`POST /v1/estimates` answered from a quote (docs/plan-quote.md §5,
4d-1): the landed price per shipping option with its build-up, or the
refusal as an API error. Pure: the handler gathers the inputs.

Every option lists all its parts (the merchant's price per line, each fee
component, shipping, and each duty and tax with who collects it), and
they sum to its total.
"""

from typing import assert_never

import msgspec

from mor.markets.records import Collector
from mor.quote.build import Part, Quote
from mor.quote.groups import group_of
from mor.quote.refusals import Refusal
from mor.web.api_wire import ApiError, ErrorType, MoneyOut, money_out


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
                option.key,
                option.service,
                option.service_name,
                option.tier.value,
                transit,
                money_out(option.total),
                parts,
            )
        )
    not_offered: list[NotOfferedOut] = []
    for refused in quote.not_offered:
        not_offered.append(NotOfferedOut(refused.key, refused.reason))
    out = EstimateOut(
        "estimate",
        quote.destination,
        quote.currency.value,
        options,
        not_offered,
        list(quote.placeholders),
    )
    return msgspec.json.encode(out)


def refusal_error(refusal: Refusal) -> ApiError:
    if refusal.ours:
        return ApiError(
            503, ErrorType.UNAVAILABLE, refusal.code, refusal.message, refusal.param
        )
    return ApiError(
        422, ErrorType.QUOTE_REFUSED, refusal.code, refusal.message, refusal.param
    )

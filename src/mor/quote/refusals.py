"""Why a quote is not given, as a code and words, and what prod refuses
(docs/plan-quote.md §5, 4d-1 and 4d-2). Pure. The API's estimates and the
stored quote both use it, so a merchant and a buyer are told the same.

`ours` says whose side the problem is on: True for something we must set
up (a fee rule, an exchange rate, a vendor account, a placeholder in
prod), False for something about the order (a closed country, a line
without the HS code a country needs).
"""

from dataclasses import dataclass, replace
from typing import Literal, assert_never

from mor.config import Environment
from mor.markets.fx import FxMissing
from mor.markets.records import Standing
from mor.markets.rules import NotListed, NotOpen, RatesMissing, RulesNotConfirmed
from mor.quote.build import (
    CountryClosed,
    LineRateRefused,
    NoCurrency,
    NoFeeRule,
    NoServiceForLane,
    Quote,
    QuoteRefusal,
    ShippingOption,
)
from mor.quote.shipping import NotOffered
from mor.tax.line_rates import (
    RateNeeded,
    RateNotConfirmed,
    VendorNeedsPerson,
    VendorUnknownCode,
)


type RefusalCode = Literal[
    "country_not_supported",
    "country_closed",
    "classification_needed",
    "duty_rate_unavailable",
    "no_service_for_lane",
    "pricing_unavailable",
]


@dataclass(frozen=True)
class Refusal:
    code: RefusalCode
    message: str
    param: str | None
    ours: bool


def _order(code: RefusalCode, message: str, param: str | None = None) -> Refusal:
    return Refusal(code, message, param, False)


def _ours(message: str) -> Refusal:
    return Refusal("pricing_unavailable", message, None, True)


NO_SERVICE = _order(
    "no_service_for_lane", "no shipping service carries this order to this country"
)


def refusal_of(refusal: QuoteRefusal, country: str) -> Refusal:
    match refusal:
        case CountryClosed(category=category, closed=closed):
            match closed:
                case NotListed():
                    return _order(
                        "country_not_supported",
                        f"we do not sell into {country}",
                        "country",
                    )
                case NotOpen(note=note):
                    return _order(
                        "country_closed", f"{country} is not open: {note}", "country"
                    )
                case RulesNotConfirmed() | RatesMissing():
                    return _order(
                        "country_closed",
                        f"{category} into {country} is not open yet",
                        "country",
                    )
                case _:
                    assert_never(closed)
        case NoCurrency():
            return _order(
                "country_not_supported",
                f"we cannot charge in {country}'s currency",
                "country",
            )
        case FxMissing(base=base, quote=quote):
            return _ours(f"no exchange rate from {base} to {quote}")
        case NoFeeRule(component=component):
            return _ours(f"no {component.value} rule for {country}")
        case LineRateRefused(line=line, charge=charge, problem=problem):
            match problem:
                case RateNeeded(reason="no_hs_code"):
                    return _order(
                        "classification_needed",
                        f"{country} needs an HS code to price {charge.value}",
                        f"lines[{line}].customs.hs_code",
                    )
                case RateNeeded() | RateNotConfirmed():
                    return _order(
                        "duty_rate_unavailable",
                        f"no confirmed {charge.value} rate for this line in {country}",
                        f"lines[{line}]",
                    )
                case VendorUnknownCode():
                    return _order(
                        "duty_rate_unavailable",
                        f"the HS code is not known for {country}",
                        f"lines[{line}].customs.hs_code",
                    )
                case VendorNeedsPerson():
                    return _order(
                        "duty_rate_unavailable",
                        f"the {charge.value} rate for this line needs checking",
                        f"lines[{line}]",
                    )
                case _:
                    assert_never(problem)
        case NoServiceForLane():
            return NO_SERVICE
        case _:
            assert_never(refusal)


def offered_in(quote: Quote, environment: Environment) -> Quote | Refusal:
    """The quote as this environment may give it. Prod never quotes on a
    placeholder (decided 17-Nov-2026, plan-quote Q5): a placeholder fee
    rule refuses the whole quote, and a placeholder service is left out,
    listed as not offered. Elsewhere the quote is given as built, its
    placeholders named."""
    if environment != Environment.PROD:
        return quote
    if quote.placeholders:
        return _ours("pricing for this country is not set up yet")
    options: list[ShippingOption] = []
    not_offered = list(quote.not_offered)
    for option in quote.options:
        if option.standing == Standing.PLACEHOLDER:
            not_offered.append(NotOffered(option.key, "placeholder", ""))
            continue
        options.append(option)
    if not options:
        return NO_SERVICE
    return replace(quote, options=tuple(options), not_offered=tuple(not_offered))

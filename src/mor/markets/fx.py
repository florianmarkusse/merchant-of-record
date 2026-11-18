"""Exact conversion between currencies with a load's FX rates
(docs/plan-quote.md §2-3). Pure. Nothing is rounded here: the result is a
Fraction of the target's minor unit, for comparing with a limit or for a
caller that names its rounding mode.

A rate is used only if its research status is usable (Q1). A pair is found
through the shortest chain of rates (the ECB publishes per EUR, the State
Bank of Vietnam per USD)."""

from dataclasses import dataclass
from fractions import Fraction

from mor.markets.records import USABLE_STATUSES, MarketSet
from mor.money.amount import Money
from mor.money.currency import Currency, exponent


@dataclass(frozen=True)
class FxMissing:
    """No confirmed rate links the two currencies."""

    base: Currency
    quote: Currency


def rate(market: MarketSet, base: Currency, quote: Currency) -> Fraction | FxMissing:
    """Units of `quote` per unit of `base`, in major units: the shortest
    chain of confirmed rates, each used directly or inverted (VND to SGD
    runs VND-USD-EUR-SGD). Exact, so every chain gives the same value only
    if the published rates agree; the shortest is taken, ties by currency
    order, so the answer is the same every time."""
    if base == quote:
        return Fraction(1)
    edges = _edges(market)
    reached: dict[Currency, Fraction] = {base: Fraction(1)}
    frontier: list[Currency] = [base]
    while frontier:
        following: list[Currency] = []
        for currency in frontier:
            for neighbour, factor in edges.get(currency, []):
                if neighbour in reached:
                    continue
                reached[neighbour] = reached[currency] * factor
                if neighbour == quote:
                    return reached[neighbour]
                following.append(neighbour)
        frontier = following
    return FxMissing(base, quote)


def _edges(market: MarketSet) -> dict[Currency, list[tuple[Currency, Fraction]]]:
    """Each confirmed rate as two edges, sorted by currency for a stable
    search order."""
    edges: dict[Currency, list[tuple[Currency, Fraction]]] = {}
    for fx in market.fx_rates:
        if fx.status not in USABLE_STATUSES:
            continue
        edges.setdefault(fx.base, []).append((fx.quote, fx.rate))
        edges.setdefault(fx.quote, []).append((fx.base, 1 / fx.rate))
    for neighbours in edges.values():
        neighbours.sort(key=lambda edge: edge[0].value)
    return edges


def convert_minor(
    market: MarketSet, money: Money, to: Currency
) -> Fraction | FxMissing:
    """`money` in `to`'s minor units, exact."""
    per_unit = rate(market, money.currency, to)
    if isinstance(per_unit, FxMissing):
        return per_unit
    major = Fraction(money.amount_minor, 10 ** exponent(money.currency))
    return major * per_unit * 10 ** exponent(to)

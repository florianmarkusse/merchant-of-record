import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from mor.money.amount import Money


class RoundingMode(StrEnum):
    HALF_EVEN = "half_even"
    HALF_AWAY_FROM_ZERO = "half_away_from_zero"
    TOWARD_ZERO = "toward_zero"
    AWAY_FROM_ZERO = "away_from_zero"
    FLOOR = "floor"
    CEILING = "ceiling"


def round_fraction(value: Fraction, mode: RoundingMode) -> int:
    """Round an exact value to a whole number of minor units."""
    match mode:
        case RoundingMode.HALF_EVEN:
            # Fraction.__round__ without digits rounds half to even.
            return round(value)
        case RoundingMode.HALF_AWAY_FROM_ZERO:
            magnitude = math.floor(abs(value) + Fraction(1, 2))
            return magnitude if value >= 0 else -magnitude
        case RoundingMode.TOWARD_ZERO:
            return math.trunc(value)
        case RoundingMode.AWAY_FROM_ZERO:
            return math.ceil(value) if value >= 0 else math.floor(value)
        case RoundingMode.FLOOR:
            return math.floor(value)
        case RoundingMode.CEILING:
            return math.ceil(value)


@dataclass(frozen=True)
class Divided:
    """`result` plus `remainder` is the exact value; the remainder is in minor
    units and is what the caller books (AGENTS.md: never dropped)."""

    result: Money
    remainder: Fraction


def divide(money: Money, ratio: Fraction, mode: RoundingMode) -> Divided:
    """Multiply by an exact ratio (a tax rate, an FX rate, a fee percentage)
    and round with a named mode. The currency stays the same; converting
    between currencies is FX, a ledger event of its own (a later slice)."""
    exact = money.amount_minor * ratio
    rounded = round_fraction(exact, mode)
    return Divided(Money(rounded, money.currency), exact - rounded)


def allocate(total: Money, weights: Sequence[int]) -> list[Money]:
    """Split a total by integer weights with the largest-remainder method.
    The parts always sum to the total; leftover units go to the largest
    remainders, ties to the earliest weight, so the result is deterministic.
    A negative total is split like its magnitude and negated."""
    if len(weights) == 0:
        raise ValueError("allocate needs at least one weight")
    for weight in weights:
        if weight < 0:
            raise ValueError(f"negative weight: {weight}")
    weight_sum = sum(weights)
    if weight_sum == 0:
        raise ValueError("weights sum to zero")

    magnitude = abs(total.amount_minor)
    shares: list[int] = []
    remainders: list[int] = []
    for weight in weights:
        share, remainder = divmod(magnitude * weight, weight_sum)
        shares.append(share)
        remainders.append(remainder)

    leftover = magnitude - sum(shares)
    # Indexes by descending remainder; sorted() is stable, so ties keep
    # their original order and the earliest weight wins.
    order = sorted(range(len(weights)), key=lambda i: -remainders[i])
    for i in order[:leftover]:
        shares[i] += 1

    sign = -1 if total.amount_minor < 0 else 1
    parts: list[Money] = []
    for share in shares:
        parts.append(Money(sign * share, total.currency))
    return parts

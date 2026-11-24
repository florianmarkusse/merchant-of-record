"""`numeric` read as a whole `int`, never as `Decimal`.

We use `numeric` only for whole numbers that can outgrow `bigint` (an
exact quote part's numerator and denominator, `quote_part`), and a CHECK
keeps their scale 0 (`migrations/0027_whole_numeric.sql`). psycopg would
read them as `Decimal`, which our code never uses (money is `int` minor
units, rates are `Fraction`; ruff bans `decimal.Decimal`).

Subclassing `Loader` is psycopg's own extension point for reading a type,
not a class hierarchy of ours.
"""

from psycopg.abc import Buffer
from psycopg.adapt import Loader


class WholeNumericLoader(Loader):
    """Text format only: no cursor of ours asks for binary results."""

    def load(self, data: Buffer) -> int:
        text = bytes(data).decode("ascii")
        if not text.removeprefix("-").isdigit():
            # A CHECK keeps every numeric column whole: this is a bug.
            raise ValueError(f"numeric {text!r} is not a whole number")
        return int(text)

"""What we hold of a vendor lookup, as plain records the pure tax rule
reads (docs/plan-quote.md §3, 4b-2)."""

import uuid
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from mor.markets.records import Charge


class LookupState(StrEnum):
    """Matches `duty_lookup_state`."""

    REQUESTED = "requested"
    ANSWERED = "answered"
    UNKNOWN_CODE = "unknown_code"
    NEEDS_PERSON = "needs_person"


@dataclass(frozen=True, kw_only=True)
class StoredAnswer:
    """The vendor's answer for one charge, and what our own rows said when
    it came (None: we had no row)."""

    charge: Charge
    formula: str
    rate: Fraction | None  # None: the formula is not a plain percentage
    note: str | None
    hs_code_used: str
    own_rate: Fraction | None
    own_line_no: int | None


@dataclass(frozen=True, kw_only=True)
class VendorLookup:
    id: uuid.UUID
    vendor: str
    state: LookupState
    calls: int
    detail: str | None
    answers: tuple[StoredAnswer, ...]  # empty unless answered

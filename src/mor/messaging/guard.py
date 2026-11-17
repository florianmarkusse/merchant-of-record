from enum import StrEnum


class DuplicateGuard(StrEnum):
    """What a vendor promises about a request sent twice, per provider and
    operation (plan-architecture.md §5). It decides what to do after a
    request was marked `sent` and no answer was recorded.

    - VENDOR_CONSTRAINT: the vendor refuses or replays a second request with
      our key, for good. Send again with the same key.
    - COMPENSABLE: nothing, but a duplicate can be undone. Send again, then
      find by our reference and undo the extras. Never for money.
    - NONE: nothing. Never send again; look it up, and hand a "not found"
      to a person.

    A key the vendor honours only for a window counts as NONE."""

    VENDOR_CONSTRAINT = "vendor_constraint"
    COMPENSABLE = "compensable"
    NONE = "none"

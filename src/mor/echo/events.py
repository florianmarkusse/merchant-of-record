"""What the vendor's events mean for a job: pure, no database, no clock.

Transitional with the echo flow (docs/plan-messaging.md §12); it rehearses
shipment tracking (plan-architecture.md §9).
"""

from collections.abc import Iterable
from enum import StrEnum

from mor.echo.port import EventKind


class VendorStatus(StrEnum):
    """Matches `echo_vendor_status`, in the order a job passes through."""

    NONE = "none"
    CONFIRMED = "confirmed"
    ARCHIVED = "archived"


def rank(status: VendorStatus) -> int:
    match status:
        case VendorStatus.NONE:
            return 0
        case VendorStatus.CONFIRMED:
            return 1
        case VendorStatus.ARCHIVED:
            return 2


def derive_status(kinds: Iterable[EventKind]) -> VendorStatus:
    """The status from the whole set of events a job has, never from the
    order they arrived in: duplicates and late or early events give the
    same answer. An `archived` without its `confirmed` is `archived`."""
    status = VendorStatus.NONE
    for kind in kinds:
        match kind:
            case EventKind.CONFIRMED:
                reached = VendorStatus.CONFIRMED
            case EventKind.ARCHIVED:
                reached = VendorStatus.ARCHIVED
        if rank(reached) > rank(status):
            status = reached
    return status

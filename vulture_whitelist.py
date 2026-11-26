"""Names vulture reports that are used in ways it cannot see. Reviewed
23-Nov-2026; add a name only after checking it is really used, with the
reason. Run `uv run vulture` (paths and settings are in pyproject.toml).

Attribute access on `_` is vulture's convention: it marks the name used.
vulture only parses this file, so `_` is a plain stand-in that type-checks.
"""

from typing import Any


_: Any = None

# StrEnum members that mirror a database enum: rows read through
# `mor.db.enums` arrive as these members, and every label must exist for
# `tests/db/test_enums.py`, even one no code names yet.
_.CARRIER_PAYABLE  # ledger AccountKind: the carrier invoice slice
_.CARRIER_VARIANCE
_.RESERVE
_.CONDITIONAL  # markets.records enums
_.CLOSED
_.UNDECIDED
_.ESTIMATE
_.UNVERIFIED
_.TO_FETCH
_.AWAITING_ANSWER
_.NOT_APPLICABLE
_.LEGAL
_.ORIGIN_STATEMENT
_.CERTIFICATE_OF_ORIGIN
_.EXPORTER_REGISTRATION
_.DIRECT_SHIPMENT
_.NOT_UNDER_IOSS
_.EXPRESS
_.DAY_DEFINITE
_.ECONOMY
_.POSTAL
_.RESTRICTED
_.PROHIBITED
_.AGREED
_.SUSPENDED  # merchants.records KybState
_.REDIRECT  # psp.port ActionKind: the PSP sends it, the buyer page shows a link
_.REVOKED  # merchants.records KeyState
_.SALE  # psp.port CaptureMode
_.AUTHORISE_CAPTURE
_.SUCCEEDED  # messaging.guard VendorRequestState
_.PICKED_UP  # shipments.records TrackingStatus: derived from events in 6b
_.IN_TRANSIT
_.HELD_AT_CUSTOMS
_.DELIVERED
_.RETURNING
_.RETURNED
_.LOST

# Fields of msgspec wire records: msgspec encodes them into the merchant
# API's JSON, which vulture does not see as a read.
_.min_days  # web.api_estimates
_.max_days
_.transit_days_min  # web.api_wire
_.transit_days_max
_.phone
_.checkout_session
_.ship_to
_.order_url

# Read by callers that match on the result; kept so a caller can name the
# journal entry.
_.entry_id  # ledger.store Recorded, AlreadyRecorded

# Named by string: uvicorn's factory "mor.web.serve:create_app".
_.create_app

# Used only by tests: setup and readers for the test database, and money
# primitives the rules require and the tests pin down.
_.set_price_rule  # carriers.prices
_.echo_job_state  # echo.flow
_.subtract  # money.amount
_.divide  # money.rounding

# A fact names what happened: bank_entry_received names its entry, though
# bank_entry_match runs over the whole account (docs/plan-payments.md §7,
# the bank statement).
_.bank_entry_id

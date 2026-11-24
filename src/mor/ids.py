"""Our ids, one type each, so pyright refuses one where another belongs: a
`PspAccountId` passed as a `MerchantId`, an order's id shown as a merchant's.

A `NewType` is its base type at runtime and costs nothing. An id gets its
type where it comes into being: `MerchantId(uuid.uuid7())` where it is
made, the record's field annotation where `class_row` or msgspec reads it,
the parse at an edge (a path parameter, `mor.public_ids`). Anywhere else it
only passes through. A bare `uuid.UUID` is left to the generic messaging
code (an aggregate, a message, an inbox row of any kind) and to the echo
flow; the `typed-ids` ast-grep rule refuses it elsewhere.

Each id is named after its table, whatever a local calls it.
"""

import uuid
from typing import NewType


# Merchants
MerchantId = NewType("MerchantId", uuid.UUID)

# Provider accounts, one kind per vendor port
PspAccountId = NewType("PspAccountId", uuid.UUID)
PspRouteId = NewType("PspRouteId", uuid.UUID)
CourierAccountId = NewType("CourierAccountId", uuid.UUID)
DutyVendorAccountId = NewType("DutyVendorAccountId", uuid.UUID)

# Market data
MarketLoadId = NewType("MarketLoadId", uuid.UUID)

# Checkout and quotes
CheckoutSessionId = NewType("CheckoutSessionId", uuid.UUID)
CheckoutAddressId = NewType("CheckoutAddressId", uuid.UUID)
QuoteId = NewType("QuoteId", uuid.UUID)
DutyLookupId = NewType("DutyLookupId", uuid.UUID)
ShippingPriceLookupId = NewType("ShippingPriceLookupId", uuid.UUID)

# Payments, orders, invoices
PaymentAttemptId = NewType("PaymentAttemptId", uuid.UUID)
PspInboxId = NewType("PspInboxId", uuid.UUID)
PspRequestId = NewType("PspRequestId", uuid.UUID)
OrderId = NewType("OrderId", uuid.UUID)
InvoiceId = NewType("InvoiceId", uuid.UUID)

# Settlement
SettlementLineId = NewType("SettlementLineId", uuid.UUID)
SettlementPayoutId = NewType("SettlementPayoutId", uuid.UUID)
SettlementExceptionId = NewType("SettlementExceptionId", uuid.UUID)

# Ledger
JournalEntryId = NewType("JournalEntryId", uuid.UUID)

# The probe (`python -m mor.probe`)
ProbeId = NewType("ProbeId", uuid.UUID)

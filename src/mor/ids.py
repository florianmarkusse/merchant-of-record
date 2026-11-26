"""Our ids and codes, one type each, so pyright refuses one where another
belongs: a `PspAccountId` passed as a `MerchantId`, a carrier's code where
a country's goes, a ledger key sent to the PSP.

A `NewType` is its base type at runtime and costs nothing. A value gets its
type where it comes into being: `MerchantId(uuid.uuid7())` where it is
made, the record's field annotation where `class_row` or msgspec reads it,
the parse at an edge (a path parameter, `mor.public_ids`, a market data
file). Anywhere else it only passes through.

A bare `uuid.UUID` is left to the generic messaging code (an aggregate or a
message of any kind) and to the echo flow; the `typed-ids` ast-grep rule
refuses it elsewhere. A string id or code has no such check, since nothing
marks a `str` as one: name it here and annotate it. The fakes stay untyped,
as the vendors they stand for speak plain strings.

Each uuid id is named after its table, whatever a local calls it.
"""

import uuid
from typing import NewType


# Merchants
MerchantId = NewType("MerchantId", uuid.UUID)
MerchantShipFromId = NewType("MerchantShipFromId", uuid.UUID)
SkuClassificationId = NewType("SkuClassificationId", uuid.UUID)

# Provider accounts, one kind per vendor port
PspAccountId = NewType("PspAccountId", uuid.UUID)
PspRouteId = NewType("PspRouteId", uuid.UUID)
CarrierAccountId = NewType("CarrierAccountId", uuid.UUID)
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

# The bank
BankAccountId = NewType("BankAccountId", uuid.UUID)
BankInboxId = NewType("BankInboxId", uuid.UUID)
BankEntryId = NewType("BankEntryId", uuid.UUID)

# Ledger
JournalEntryId = NewType("JournalEntryId", uuid.UUID)

# The probe (`python -m mor.probe`)
ProbeId = NewType("ProbeId", uuid.UUID)

# Codes, from market data, merchants and buyers
CountryCode = NewType("CountryCode", str)  # ISO 3166-1 alpha-2: `SG`
GroupCode = NewType("GroupCode", str)  # a group of countries: `EU`, `ASEAN`
CarrierCode = NewType("CarrierCode", str)  # `dhl_express`
ServiceCode = NewType("ServiceCode", str)  # a carrier's service
HsCode = NewType("HsCode", str)  # digits only, 2 to 10: a code or a prefix
MerchantSku = NewType("MerchantSku", str)  # the merchant's own product code

# The PSP's ids for what it reports
PspPaymentId = NewType("PspPaymentId", str)
PspPayoutId = NewType("PspPayoutId", str)
PspLineId = NewType("PspLineId", str)
PspEventId = NewType("PspEventId", str)

# The bank's id for an entry it shows
BankTransactionId = NewType("BankTransactionId", str)

# Idempotency keys, one type per space they are unique in
JournalKey = NewType("JournalKey", str)  # journal_entry.idempotency_key
ApiIdempotencyKey = NewType("ApiIdempotencyKey", str)  # a merchant's header
PspIdempotencyKey = NewType("PspIdempotencyKey", str)  # what we send the PSP

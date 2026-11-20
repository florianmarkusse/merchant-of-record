"""The PSP port: our records and result unions, no provider words
(docs/plan-payments.md §3; plan-psp-integration §4, §5).

A provider module translates these to and from its API, converts amounts
to and from its own minor units, and never touches our database. Every
call ends in a value: the answer, a documented refusal, `Unavailable`
(provably never left), `OutcomeUnknown` (may have done the work) or
`UndecodableBody` (an answer its API does not document).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from fractions import Fraction
from typing import Literal

from mor.messaging.guard import DuplicateGuard
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.provider_accounts import ProviderAccountKind


class PspProvider(StrEnum):
    """The module that speaks to a PSP; matches `psp_provider`."""

    FAKE = "fake"


class StatusMode(StrEnum):
    """How a payment's news reaches us; matches `psp_status_mode`."""

    PUSH_FAT = "push_fat"  # a signed webhook carrying the event
    PUSH_THIN = "push_thin"  # a signed webhook saying only "news": we fetch
    POLL = "poll"  # we ask, on a schedule


class SettlementMode(StrEnum):
    """How settlement reaches us; matches `psp_settlement_mode`
    (docs/plan-payments.md §7.1). Both are reads."""

    FILE = "file"  # a report file per batch
    API = "api"  # a batch read page by page


class InboundMode(StrEnum):
    """What stored bytes are; matches `psp_inbound_mode`. The first three
    are the status modes' own."""

    PUSH_FAT = "push_fat"
    PUSH_THIN = "push_thin"
    POLL = "poll"
    SETTLEMENT_FILE = "settlement_file"
    SETTLEMENT_PAGE = "settlement_page"


class CaptureMode(StrEnum):
    """Matches `psp_capture_mode`. On `sale` the buyer's approval takes the
    money; on `authorise_capture` we capture at once after (answer 3 of
    19-Nov-2026)."""

    SALE = "sale"
    AUTHORISE_CAPTURE = "authorise_capture"


class PaymentMethod(StrEnum):
    """Matches `payment_method`."""

    CARD = "card"  # through the PSP's hosted page
    QR = "qr"


@dataclass(frozen=True)
class PspAccount:
    id: uuid.UUID
    name: str
    provider: PspProvider
    kind: ProviderAccountKind
    base_url: str
    timeout: timedelta
    status_mode: StatusMode
    capture_mode: CaptureMode
    create_guard: DuplicateGuard
    capture_guard: DuplicateGuard
    methods: tuple[PaymentMethod, ...]
    poll_interval: timedelta
    webhook_secret: str
    settlement_mode: SettlementMode
    settlement_interval: timedelta


@dataclass(frozen=True)
class PaymentRequest:
    reference: str  # our attempt id: lookups and settlement match on it
    amount: Money
    method: PaymentMethod
    return_url: str  # where the PSP's page sends the buyer back
    idempotency_key: str


class PaymentStatus(StrEnum):
    REQUIRES_ACTION = "requires_action"
    AUTHORISED = "authorised"
    CAPTURED = "captured"
    DECLINED = "declined"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


type ActionKind = Literal["redirect", "qr"]


@dataclass(frozen=True)
class BuyerAction:
    kind: ActionKind
    url: str  # the PSP's page; a QR holds what the buyer's app opens
    expires_at: datetime | None


@dataclass(frozen=True)
class ProviderPayment:
    provider_payment_id: str
    reference: str
    amount: Money
    status: PaymentStatus
    action: BuyerAction | None


@dataclass(frozen=True)
class Declined:
    """A documented refusal: the PSP made no charge."""

    payment: ProviderPayment
    reason: str


@dataclass(frozen=True)
class Found:
    payments: tuple[ProviderPayment, ...]


@dataclass(frozen=True)
class NotCapturable:
    """The PSP refused the capture; the payment as it stands."""

    payment: ProviderPayment


@dataclass(frozen=True)
class AlreadyFinal:
    """Nothing left to cancel; the payment as it ended."""

    payment: ProviderPayment


@dataclass(frozen=True)
class Page:
    """A fetched page of a payment's events, stored raw like a webhook."""

    body: bytes


@dataclass(frozen=True)
class Unavailable:
    """The request provably never reached the PSP (connection refused)."""

    detail: str


@dataclass(frozen=True)
class OutcomeUnknown:
    """No answer (timeout, dropped connection, a 5xx): it may have done the
    work. The operation's duplicate guard decides what follows."""

    detail: str


@dataclass(frozen=True)
class UndecodableBody:
    """An answer the PSP's API does not document, or an amount that is not
    a whole number in our units."""

    detail: str


type Failure = Unavailable | OutcomeUnknown | UndecodableBody
type CreateResult = ProviderPayment | Declined | Failure
type FindResult = Found | Failure
type CaptureResult = ProviderPayment | NotCapturable | Failure
type CancelResult = ProviderPayment | AlreadyFinal | Failure
type FetchResult = Page | Unavailable | OutcomeUnknown


type EventKind = Literal["authorised", "captured", "declined", "expired", "cancelled"]


@dataclass(frozen=True)
class PspEvent:
    vendor_event_id: str
    provider_payment_id: str
    reference: str
    kind: EventKind
    amount: Money
    occurred_at: datetime


@dataclass(frozen=True)
class ThinNotice:
    """A thin webhook: which of our payments has news."""

    reference: str


# --- settlement (docs/plan-payments.md §7.1) ---------------------------------


@dataclass(frozen=True)
class SettlementList:
    """Reports (file) or batches (api) after the one named, oldest first."""

    names: tuple[str, ...]


@dataclass(frozen=True)
class SettlementPage:
    """A report, or one page of a batch, raw: stored before decoding.
    `next_page` is the batch's next page, None when this was its last."""

    body: bytes
    next_page: int | None


type SettlementListResult = SettlementList | Unavailable | OutcomeUnknown
type SettlementPageResult = SettlementPage | Unavailable | OutcomeUnknown


class LineType(StrEnum):
    """Matches `settlement_line_type`. A type we do not book yet (refunds and
    chargebacks come in slice 8, a PSP's own charges) is `unknown`, with the
    PSP's word kept beside it."""

    CAPTURE = "capture"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SettlementBatch:
    """One payout. Amounts are exact in our minor units: a PSP may count
    finer than we do (IDR at 2 decimals)."""

    vendor_batch_id: str
    currency: Currency
    net: Fraction
    paid_out_at: datetime


@dataclass(frozen=True)
class SettledLine:
    vendor_line_id: str
    line_type: LineType
    vendor_type: str
    provider_payment_id: str
    reference: str
    currency: Currency
    gross: Fraction
    fee: Fraction


@dataclass(frozen=True)
class SettlementDocument:
    """A decoded report, or a decoded page of a batch. `last` says the
    batch's lines are all read with this one."""

    batch: SettlementBatch
    lines: tuple[SettledLine, ...]
    last: bool

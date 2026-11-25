"""Client for the fake PSP's API (mor.fakes.psp), shaped like any real
provider module: our records in, our result unions out, never our
database.

Amounts: the fake counts in ISO 4217 minor units, where IDR has 2 and we
use 0 (AGENTS.md). Outbound is exact; an inbound amount that is not a
whole number in our units is not rounded here: it is `UndecodableBody`,
for a person (plan-payments.md §3).

How a call ends:
- connection refused or no such host: `Unavailable`, it never left;
- a timeout (bounded by the account's timeout), a dropped connection or a
  5xx: `OutcomeUnknown`;
- a documented refusal (402 declined, 409 not capturable or already
  final): its record;
- anything else it does not document: `UndecodableBody`.
"""

import asyncio
import csv
import hashlib
import hmac
import io
from fractions import Fraction
from typing import Literal

import httpx2
import msgspec

from mor.dates import AwareDatetime, parse_instant, to_utc
from mor.ids import PspEventId, PspIdempotencyKey, PspLineId, PspPaymentId, PspPayoutId
from mor.money.amount import Money
from mor.money.currency import Currency, exponent
from mor.psp.port import (
    ActionKind,
    AlreadyFinal,
    BuyerAction,
    CancelResult,
    CaptureResult,
    CreateResult,
    Declined,
    FetchResult,
    FindResult,
    Found,
    LinesPage,
    LinesResult,
    LineType,
    NotCapturable,
    Page,
    PaymentRequest,
    PaymentStatus,
    PayoutObject,
    PayoutResult,
    ProviderPayment,
    PspAccount,
    PspEvent,
    PspEventKind,
    SettledLine,
    SettlementDocument,
    SettlementFile,
    SettlementFileResult,
    SettlementList,
    SettlementListResult,
    SettlementMode,
    SettlementPayout,
    ThinNotice,
)
from mor.vendor_failures import OutcomeUnknown, Unavailable, UndecodableBody


SIGNATURE_HEADER = "Psp-Signature"


class _Action(msgspec.Struct, frozen=True):
    kind: ActionKind
    url: str
    expires_at: AwareDatetime | None


class _Payment(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    reference: str
    amount_minor: int
    currency: str
    method: str
    status: Literal[
        "requires_action", "authorised", "captured", "declined", "expired", "cancelled"
    ]
    action: _Action | None


class _PaymentAnswer(msgspec.Struct, frozen=True):
    payment: _Payment


class _Payments(msgspec.Struct, frozen=True):
    payments: list[_Payment]


class _Refusal(msgspec.Struct, frozen=True):
    error: str
    payment: _Payment | None = None


class _Event(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    payment_id: PspPaymentId
    reference: str
    kind: PspEventKind
    amount_minor: int
    currency: str
    occurred_at: AwareDatetime


class _Events(msgspec.Struct, frozen=True):
    events: list[_Event]


class _Thin(msgspec.Struct, frozen=True):
    id: str
    payment_id: PspPaymentId
    reference: str


def _fake_exponent(currency: Currency) -> int:
    """The fake's own table: ISO 4217's."""
    if currency == Currency.IDR:
        return 2
    return exponent(currency)


def to_provider_amount(money: Money) -> int:
    """Always exact: the fake never has fewer decimals than we do."""
    return money.amount_minor * 10 ** (
        _fake_exponent(money.currency) - exponent(money.currency)
    )


def _currency(code: str) -> Currency | UndecodableBody:
    for known in Currency:
        if known.value == code:
            return known
    return UndecodableBody(f"currency {code!r}")


def exact_amount(amount: int, currency: Currency) -> Fraction:
    """A fake amount in our minor units, exact: settlement keeps what we
    cannot hold whole (an IDR fee of 1,234.56) and rounds it itself."""
    return Fraction(amount, 10 ** (_fake_exponent(currency) - exponent(currency)))


def from_provider_amount(amount: int, code: str) -> Money | UndecodableBody:
    currency = _currency(code)
    if isinstance(currency, UndecodableBody):
        return currency
    scale = 10 ** (_fake_exponent(currency) - exponent(currency))
    if amount % scale != 0:
        return UndecodableBody(f"{amount} {code} is not representable in our units")
    return Money(amount // scale, currency)


def _payment(raw: _Payment) -> ProviderPayment | UndecodableBody:
    amount = from_provider_amount(raw.amount_minor, raw.currency)
    if isinstance(amount, UndecodableBody):
        return amount
    action: BuyerAction | None = None
    if raw.action is not None:
        expires_at = raw.action.expires_at
        action = BuyerAction(
            raw.action.kind,
            raw.action.url,
            None if expires_at is None else to_utc(expires_at),
        )
    return ProviderPayment(
        provider_payment_id=PspPaymentId(raw.id),
        reference=raw.reference,
        amount=amount,
        status=PaymentStatus(raw.status),
        action=action,
    )


async def _send(
    http: httpx2.AsyncClient,
    account: PspAccount,
    method: Literal["GET", "POST"],
    path: str,
    body: object | None = None,
    key: PspIdempotencyKey | None = None,
    params: dict[str, str] | None = None,
) -> httpx2.Response | Unavailable | OutcomeUnknown:
    headers: dict[str, str] = {}
    if key is not None:
        headers["Idempotency-Key"] = key
    try:
        async with asyncio.timeout(account.timeout.total_seconds()):
            if method == "GET":
                return await http.get(account.base_url + path, params=params)
            return await http.post(
                account.base_url + path,
                content=msgspec.json.encode(body),
                headers={**headers, "Content-Type": "application/json"},
            )
    except httpx2.ConnectError as error:
        return Unavailable(repr(error))
    except (TimeoutError, httpx2.TransportError) as error:
        return OutcomeUnknown(repr(error))


def _one(response: httpx2.Response) -> ProviderPayment | UndecodableBody:
    try:
        answer = msgspec.json.decode(response.content, type=_PaymentAnswer)
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    return _payment(answer.payment)


def _refusal(response: httpx2.Response, code: str) -> ProviderPayment | UndecodableBody:
    try:
        refusal = msgspec.json.decode(response.content, type=_Refusal)
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    if refusal.error != code or refusal.payment is None:
        return UndecodableBody(f"{response.status_code} {refusal.error}")
    return _payment(refusal.payment)


async def create_payment(
    http: httpx2.AsyncClient, account: PspAccount, request: PaymentRequest
) -> CreateResult:
    sent = await _send(
        http,
        account,
        "POST",
        "/payments",
        {
            "reference": request.reference,
            "amount_minor": to_provider_amount(request.amount),
            "currency": request.amount.currency.value,
            "method": request.method.value,
            "return_url": request.return_url,
        },
        key=request.idempotency_key,
    )
    if not isinstance(sent, httpx2.Response):
        return sent
    if sent.status_code >= 500:
        return OutcomeUnknown(f"status {sent.status_code}")
    if sent.status_code == 402:
        refused = _refusal(sent, "declined")
        if isinstance(refused, UndecodableBody):
            return refused
        return Declined(refused, "declined by the issuer")
    if sent.status_code not in (200, 201):
        return UndecodableBody(f"status {sent.status_code}")
    return _one(sent)


async def find_payments(
    http: httpx2.AsyncClient, account: PspAccount, reference: str
) -> FindResult:
    sent = await _send(
        http, account, "GET", "/payments", params={"reference": reference}
    )
    if not isinstance(sent, httpx2.Response):
        return sent
    if sent.status_code >= 500:
        return OutcomeUnknown(f"status {sent.status_code}")
    if sent.status_code != 200:
        return UndecodableBody(f"status {sent.status_code}")
    try:
        answer = msgspec.json.decode(sent.content, type=_Payments)
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    found: list[ProviderPayment] = []
    for raw in answer.payments:
        psp_payment = _payment(raw)
        if isinstance(psp_payment, UndecodableBody):
            return psp_payment
        found.append(psp_payment)
    return Found(tuple(found))


async def capture(
    http: httpx2.AsyncClient,
    account: PspAccount,
    provider_payment_id: PspPaymentId,
    amount: Money,
    key: PspIdempotencyKey,
) -> CaptureResult:
    sent = await _send(
        http,
        account,
        "POST",
        f"/payments/{provider_payment_id}/capture",
        {"amount_minor": to_provider_amount(amount)},
        key=key,
    )
    if not isinstance(sent, httpx2.Response):
        return sent
    if sent.status_code >= 500:
        return OutcomeUnknown(f"status {sent.status_code}")
    if sent.status_code == 409:
        refused = _refusal(sent, "not_capturable")
        if isinstance(refused, UndecodableBody):
            return refused
        return NotCapturable(refused)
    if sent.status_code != 200:
        return UndecodableBody(f"status {sent.status_code}")
    return _one(sent)


async def cancel(
    http: httpx2.AsyncClient, account: PspAccount, provider_payment_id: PspPaymentId
) -> CancelResult:
    sent = await _send(
        http, account, "POST", f"/payments/{provider_payment_id}/cancel", {}
    )
    if not isinstance(sent, httpx2.Response):
        return sent
    if sent.status_code >= 500:
        return OutcomeUnknown(f"status {sent.status_code}")
    if sent.status_code == 409:
        refused = _refusal(sent, "already_final")
        if isinstance(refused, UndecodableBody):
            return refused
        return AlreadyFinal(refused)
    if sent.status_code != 200:
        return UndecodableBody(f"status {sent.status_code}")
    return _one(sent)


async def fetch_events(
    http: httpx2.AsyncClient, account: PspAccount, provider_payment_id: PspPaymentId
) -> FetchResult:
    """One payment with all its events, raw: stored like a webhook and
    decoded on the same path. A read, so asking again is always safe."""
    sent = await _send(http, account, "GET", f"/payments/{provider_payment_id}")
    if not isinstance(sent, httpx2.Response):
        return sent
    if sent.status_code != 200:
        return OutcomeUnknown(f"status {sent.status_code}")
    return Page(sent.content)


def verify_webhook(account: PspAccount, body: bytes, signature: str | None) -> bool:
    if signature is None:
        return False
    expected = hmac.new(
        account.webhook_secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def decode_events(body: bytes) -> tuple[PspEvent, ...] | UndecodableBody:
    """A fat webhook and a fetched page both carry `events`."""
    try:
        decoded = msgspec.json.decode(body, type=_Events)
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    events: list[PspEvent] = []
    for raw in decoded.events:
        amount = from_provider_amount(raw.amount_minor, raw.currency)
        if isinstance(amount, UndecodableBody):
            return amount
        events.append(
            PspEvent(
                vendor_event_id=PspEventId(raw.id),
                provider_payment_id=raw.payment_id,
                reference=raw.reference,
                kind=raw.kind,
                amount=amount,
                occurred_at=to_utc(raw.occurred_at),
            )
        )
    return tuple(events)


def decode_thin_webhook(body: bytes) -> ThinNotice | UndecodableBody:
    try:
        thin = msgspec.json.decode(body, type=_Thin)
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    return ThinNotice(thin.reference)


# --- settlement (docs/plan-payments.md §7) ----------------------


class _Files(msgspec.Struct, frozen=True):
    files: list[str]


class _Payouts(msgspec.Struct, frozen=True):
    payouts: list[str]


class _Payout(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    amount_minor: int
    currency: str
    arrival_date: AwareDatetime
    reconciliation_status: Literal["in_progress", "completed"]


class _Line(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    type: str
    payment_id: PspPaymentId
    reference: str
    currency: str
    gross_minor: int
    fee_minor: int
    created: AwareDatetime


class _Lines(msgspec.Struct, frozen=True):
    data: list[_Line]
    has_more: bool


class _LineId(msgspec.Struct, frozen=True):
    id: str


class _LineIds(msgspec.Struct, frozen=True):
    """Only what the reader needs from a page: its cursor."""

    data: list[_LineId]
    has_more: bool


class _Readiness(msgspec.Struct, frozen=True):
    reconciliation_status: Literal["in_progress", "completed"]


async def _get(
    http: httpx2.AsyncClient,
    account: PspAccount,
    path: str,
    params: dict[str, str] | None = None,
) -> httpx2.Response | Unavailable | OutcomeUnknown | UndecodableBody:
    """A read: a 5xx is an outcome unknown, any other status but 200 an
    answer we do not understand."""
    sent = await _send(http, account, "GET", path, params=params)
    if not isinstance(sent, httpx2.Response):
        return sent
    if sent.status_code >= 500:
        return OutcomeUnknown(f"status {sent.status_code}")
    if sent.status_code != 200:
        return UndecodableBody(f"status {sent.status_code}")
    return sent


async def list_settlements(
    http: httpx2.AsyncClient, account: PspAccount, after: str
) -> SettlementListResult | UndecodableBody:
    """Reports (file) or payouts (api) after `after`, oldest first."""
    match account.settlement_mode:
        case SettlementMode.FILE:
            path = "/settlement-files"
        case SettlementMode.API:
            path = "/payouts"
    sent = await _get(http, account, path, {"after": after})
    if not isinstance(sent, httpx2.Response):
        return sent
    try:
        match account.settlement_mode:
            case SettlementMode.FILE:
                names = msgspec.json.decode(sent.content, type=_Files).files
            case SettlementMode.API:
                names = msgspec.json.decode(sent.content, type=_Payouts).payouts
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    return SettlementList(tuple(names))


async def fetch_settlement_file(
    http: httpx2.AsyncClient, account: PspAccount, name: str
) -> SettlementFileResult | UndecodableBody:
    sent = await _get(http, account, f"/settlement-files/{name}")
    if not isinstance(sent, httpx2.Response):
        return sent
    return SettlementFile(sent.content)


async def fetch_payout(
    http: httpx2.AsyncClient, account: PspAccount, payout_id: PspPayoutId
) -> PayoutResult | UndecodableBody:
    """The payout object, raw; only its readiness is read here."""
    sent = await _get(http, account, f"/payouts/{payout_id}")
    if not isinstance(sent, httpx2.Response):
        return sent
    try:
        readiness = msgspec.json.decode(sent.content, type=_Readiness)
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    return PayoutObject(sent.content, readiness.reconciliation_status == "completed")


async def fetch_payout_lines(
    http: httpx2.AsyncClient,
    account: PspAccount,
    payout_id: PspPayoutId,
    after: str | None,
) -> LinesResult | UndecodableBody:
    """A page of the payout's lines after the cursor, raw; only the cursor
    is read here."""
    params: dict[str, str] = {}
    if after is not None:
        params["starting_after"] = after
    sent = await _get(http, account, f"/payouts/{payout_id}/lines", params)
    if not isinstance(sent, httpx2.Response):
        return sent
    try:
        page = msgspec.json.decode(sent.content, type=_LineIds)
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    last = page.data[-1].id if page.data else None
    return LinesPage(sent.content, last, page.has_more)


def _int(text: str) -> int | UndecodableBody:
    try:
        return int(text)
    except ValueError:
        return UndecodableBody(f"not a whole number: {text!r}")


def _line_type(vendor_type: str) -> LineType:
    match vendor_type:
        case "capture":
            return LineType.CAPTURE
        case "balance_transfer":
            return LineType.BALANCE_TRANSFER
        case _:
            return LineType.UNKNOWN


CSV_HEADER = [
    "record",
    "batch",
    "id",
    "type",
    "payment_id",
    "reference",
    "currency",
    "gross_minor",
    "fee_minor",
    "net_minor",
    "date",
]


def decode_settlement_file(body: bytes) -> SettlementDocument | UndecodableBody:
    """A report, as Adyen's: a header row, one `line` row each, and one
    `merchant_payout` row, every row naming the batch."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as error:
        return UndecodableBody(str(error))
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as error:
        return UndecodableBody(str(error))
    if not rows or rows[0] != CSV_HEADER:
        return UndecodableBody("no header row")
    payout: SettlementPayout | None = None
    batch_id: str | None = None
    lines: list[SettledLine] = []
    for row in rows[1:]:
        if len(row) != len(CSV_HEADER):
            return UndecodableBody(f"a row of {len(row)} fields")
        record, batch, row_id, vendor_type, payment_id, reference, code = row[:7]
        if batch_id is None:
            batch_id = batch
        elif batch != batch_id:
            return UndecodableBody(f"rows of batches {batch_id!r} and {batch!r}")
        currency = _currency(code)
        if isinstance(currency, UndecodableBody):
            return currency
        moment = parse_instant(row[10])
        if moment is None:
            return UndecodableBody(f"date {row[10]!r} is no moment with an offset")
        match record:
            case "merchant_payout":
                if payout is not None:
                    return UndecodableBody("two payout rows")
                net = _int(row[9])
                if isinstance(net, UndecodableBody):
                    return net
                payout = SettlementPayout(
                    vendor_payout_id=PspPayoutId(batch),
                    currency=currency,
                    net=exact_amount(net, currency),
                    paid_out_at=moment,
                )
            case "line":
                gross = _int(row[7])
                if isinstance(gross, UndecodableBody):
                    return gross
                fee = _int(row[8])
                if isinstance(fee, UndecodableBody):
                    return fee
                lines.append(
                    SettledLine(
                        vendor_line_id=PspLineId(row_id),
                        vendor_payout_id=PspPayoutId(batch),
                        line_type=_line_type(vendor_type),
                        vendor_type=vendor_type,
                        provider_payment_id=PspPaymentId(payment_id),
                        reference=reference,
                        currency=currency,
                        gross=exact_amount(gross, currency),
                        fee=exact_amount(fee, currency),
                        settled_at=moment,
                    )
                )
            case _:
                return UndecodableBody(f"record {record!r}")
    if payout is None:
        return UndecodableBody("no payout row")
    return SettlementDocument(payout, tuple(lines))


def decode_payout(body: bytes) -> SettlementDocument | UndecodableBody:
    """A payout object, as Stripe's: the payout alone."""
    try:
        raw = msgspec.json.decode(body, type=_Payout)
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    currency = _currency(raw.currency)
    if isinstance(currency, UndecodableBody):
        return currency
    payout = SettlementPayout(
        vendor_payout_id=PspPayoutId(raw.id),
        currency=currency,
        net=exact_amount(raw.amount_minor, currency),
        paid_out_at=to_utc(raw.arrival_date),
    )
    return SettlementDocument(payout, ())


def decode_payout_lines(
    body: bytes, vendor_payout_id: PspPayoutId
) -> SettlementDocument | UndecodableBody:
    """A page of a payout's lines. The page does not name its payout: the
    request did, and the caller says which it was."""
    try:
        page = msgspec.json.decode(body, type=_Lines)
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    lines: list[SettledLine] = []
    for raw in page.data:
        currency = _currency(raw.currency)
        if isinstance(currency, UndecodableBody):
            return currency
        lines.append(
            SettledLine(
                vendor_line_id=PspLineId(raw.id),
                vendor_payout_id=vendor_payout_id,
                line_type=_line_type(raw.type),
                vendor_type=raw.type,
                provider_payment_id=raw.payment_id,
                reference=raw.reference,
                currency=currency,
                gross=exact_amount(raw.gross_minor, currency),
                fee=exact_amount(raw.fee_minor, currency),
                settled_at=to_utc(raw.created),
            )
        )
    return SettlementDocument(None, tuple(lines))

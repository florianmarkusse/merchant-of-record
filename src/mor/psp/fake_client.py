"""Client for the fake PSP's API (mor.fakes.psp), shaped like any real
provider module: our records in, our result unions out, never our
database.

Amounts: the fake counts in ISO 4217 minor units, where IDR has 2 and we
use 0 (AGENTS.md). Outbound is exact; an inbound amount that is not a
whole number in our units is not rounded here: it is `UndecodableBody`,
for a person (plan-psp-integration §4).

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
from mor.money.amount import Money
from mor.money.currency import Currency, exponent
from mor.psp.port import (
    AlreadyFinal,
    BuyerAction,
    CancelResult,
    CaptureResult,
    CreateResult,
    Declined,
    FetchResult,
    FindResult,
    Found,
    LineType,
    NotCapturable,
    Page,
    PaymentRequest,
    PaymentStatus,
    ProviderPayment,
    PspAccount,
    PspEvent,
    SettledLine,
    SettlementBatch,
    SettlementDocument,
    SettlementList,
    SettlementListResult,
    SettlementMode,
    SettlementPage,
    SettlementPageResult,
    ThinNotice,
)
from mor.vendor_failures import OutcomeUnknown, Unavailable, UndecodableBody


SIGNATURE_HEADER = "Psp-Signature"


class _Action(msgspec.Struct, frozen=True):
    kind: Literal["redirect", "qr"]
    url: str
    expires_at: AwareDatetime | None


class _Payment(msgspec.Struct, frozen=True):
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


class _Event(msgspec.Struct, frozen=True):
    id: str
    payment_id: str
    reference: str
    kind: Literal["authorised", "captured", "declined", "expired", "cancelled"]
    amount_minor: int
    currency: str
    occurred_at: AwareDatetime


class _Events(msgspec.Struct, frozen=True):
    events: list[_Event]


class _Thin(msgspec.Struct, frozen=True):
    id: str
    payment_id: str
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
        provider_payment_id=raw.id,
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
    key: str | None = None,
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
        payment = _payment(raw)
        if isinstance(payment, UndecodableBody):
            return payment
        found.append(payment)
    return Found(tuple(found))


async def capture(
    http: httpx2.AsyncClient,
    account: PspAccount,
    provider_payment_id: str,
    amount: Money,
    key: str,
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
    http: httpx2.AsyncClient, account: PspAccount, provider_payment_id: str
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
    http: httpx2.AsyncClient, account: PspAccount, provider_payment_id: str
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
                vendor_event_id=raw.id,
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


# --- settlement (docs/plan-payments.md §7.1) ---------------------------------


class _Files(msgspec.Struct, frozen=True):
    files: list[str]


class _Batches(msgspec.Struct, frozen=True):
    batches: list[str]


class _PageBatch(msgspec.Struct, frozen=True):
    id: str
    currency: str
    net_minor: int
    paid_out_at: AwareDatetime


class _PageLine(msgspec.Struct, frozen=True):
    id: str
    type: str
    payment_id: str
    reference: str
    gross_minor: int
    fee_minor: int


class _BatchPage(msgspec.Struct, frozen=True):
    batch: _PageBatch
    lines: list[_PageLine]
    next_page: int | None


class _NextPage(msgspec.Struct, frozen=True):
    next_page: int | None


async def list_settlements(
    http: httpx2.AsyncClient, account: PspAccount, after: str
) -> SettlementListResult | UndecodableBody:
    """Reports (file) or batches (api) after `after`, oldest first."""
    match account.settlement_mode:
        case SettlementMode.FILE:
            path = "/settlement-files"
        case SettlementMode.API:
            path = "/settlements"
    sent = await _send(http, account, "GET", path, params={"after": after})
    if not isinstance(sent, httpx2.Response):
        return sent
    if sent.status_code >= 500:
        return OutcomeUnknown(f"status {sent.status_code}")
    if sent.status_code != 200:
        return UndecodableBody(f"status {sent.status_code}")
    try:
        match account.settlement_mode:
            case SettlementMode.FILE:
                names = msgspec.json.decode(sent.content, type=_Files).files
            case SettlementMode.API:
                names = msgspec.json.decode(sent.content, type=_Batches).batches
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    return SettlementList(tuple(names))


async def fetch_settlement(
    http: httpx2.AsyncClient, account: PspAccount, name: str, page: int
) -> SettlementPageResult | UndecodableBody:
    """A report whole (file), or one page of a batch (api). The bytes are
    kept raw; only the next page's number is read here."""
    match account.settlement_mode:
        case SettlementMode.FILE:
            sent = await _send(http, account, "GET", f"/settlement-files/{name}")
        case SettlementMode.API:
            sent = await _send(
                http, account, "GET", f"/settlements/{name}", params={"page": str(page)}
            )
    if not isinstance(sent, httpx2.Response):
        return sent
    if sent.status_code >= 500:
        return OutcomeUnknown(f"status {sent.status_code}")
    if sent.status_code != 200:
        return UndecodableBody(f"status {sent.status_code}")
    match account.settlement_mode:
        case SettlementMode.FILE:
            return SettlementPage(sent.content, None)
        case SettlementMode.API:
            try:
                next_page = msgspec.json.decode(sent.content, type=_NextPage)
            except msgspec.DecodeError as error:
                return UndecodableBody(str(error))
            return SettlementPage(sent.content, next_page.next_page)


def _int(text: str) -> int | UndecodableBody:
    try:
        return int(text)
    except ValueError:
        return UndecodableBody(f"not a whole number: {text!r}")


def decode_settlement_file(body: bytes) -> SettlementDocument | UndecodableBody:
    """A report: a header row, one `batch` row, then one `line` row each.

    record,id,type,payment_id,reference,currency,gross_minor,fee_minor,
        net_minor,paid_out_at
    """
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as error:
        return UndecodableBody(str(error))
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as error:
        return UndecodableBody(str(error))
    header = [
        "record",
        "id",
        "type",
        "payment_id",
        "reference",
        "currency",
        "gross_minor",
        "fee_minor",
        "net_minor",
        "paid_out_at",
    ]
    if not rows or rows[0] != header:
        return UndecodableBody("no header row")
    batch: SettlementBatch | None = None
    lines: list[SettledLine] = []
    for row in rows[1:]:
        if len(row) != len(header):
            return UndecodableBody(f"a row of {len(row)} fields")
        record, row_id, line_type, payment_id, reference, code = row[:6]
        currency = _currency(code)
        if isinstance(currency, UndecodableBody):
            return currency
        if record == "batch":
            if batch is not None or lines:
                return UndecodableBody("the batch row is not the first")
            net = _int(row[8])
            if isinstance(net, UndecodableBody):
                return net
            paid_out_at = parse_instant(row[9])
            if paid_out_at is None:
                return UndecodableBody(
                    f"paid_out_at {row[9]!r} is no moment with an offset"
                )
            batch = SettlementBatch(
                vendor_batch_id=row_id,
                currency=currency,
                net=exact_amount(net, currency),
                paid_out_at=paid_out_at,
            )
        elif record == "line":
            if batch is None:
                return UndecodableBody("a line before the batch row")
            gross = _int(row[6])
            if isinstance(gross, UndecodableBody):
                return gross
            fee = _int(row[7])
            if isinstance(fee, UndecodableBody):
                return fee
            lines.append(
                SettledLine(
                    vendor_line_id=row_id,
                    line_type=LineType.CAPTURE
                    if line_type == "capture"
                    else LineType.UNKNOWN,
                    vendor_type=line_type,
                    provider_payment_id=payment_id,
                    reference=reference,
                    currency=currency,
                    gross=exact_amount(gross, currency),
                    fee=exact_amount(fee, currency),
                )
            )
        else:
            return UndecodableBody(f"record {record!r}")
    if batch is None:
        return UndecodableBody("no batch row")
    return SettlementDocument(batch, tuple(lines), True)


def decode_settlement_page(body: bytes) -> SettlementDocument | UndecodableBody:
    """One page of a batch; its lines are in the batch's currency."""
    try:
        page = msgspec.json.decode(body, type=_BatchPage)
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    currency = _currency(page.batch.currency)
    if isinstance(currency, UndecodableBody):
        return currency
    batch = SettlementBatch(
        vendor_batch_id=page.batch.id,
        currency=currency,
        net=exact_amount(page.batch.net_minor, currency),
        paid_out_at=to_utc(page.batch.paid_out_at),
    )
    lines: list[SettledLine] = []
    for raw in page.lines:
        lines.append(
            SettledLine(
                vendor_line_id=raw.id,
                line_type=LineType.CAPTURE
                if raw.type == "capture"
                else LineType.UNKNOWN,
                vendor_type=raw.type,
                provider_payment_id=raw.payment_id,
                reference=raw.reference,
                currency=currency,
                gross=exact_amount(raw.gross_minor, currency),
                fee=exact_amount(raw.fee_minor, currency),
            )
        )
    return SettlementDocument(batch, tuple(lines), page.next_page is None)

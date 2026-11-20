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
import hashlib
import hmac
from datetime import datetime
from typing import Literal

import httpx2
import msgspec

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
    NotCapturable,
    OutcomeUnknown,
    Page,
    PaymentRequest,
    PaymentStatus,
    ProviderPayment,
    PspAccount,
    PspEvent,
    ThinNotice,
    Unavailable,
    UndecodableBody,
)


SIGNATURE_HEADER = "Psp-Signature"


class _Action(msgspec.Struct, frozen=True):
    kind: Literal["redirect", "qr"]
    url: str
    expires_at: datetime | None


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
    occurred_at: datetime


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


def from_provider_amount(amount: int, code: str) -> Money | UndecodableBody:
    currency: Currency | None = None
    for known in Currency:
        if known.value == code:
            currency = known
    if currency is None:
        return UndecodableBody(f"currency {code!r}")
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
        action = BuyerAction(raw.action.kind, raw.action.url, raw.action.expires_at)
    return ProviderPayment(
        raw.id, raw.reference, amount, PaymentStatus(raw.status), action
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
                raw.id, raw.payment_id, raw.reference, raw.kind, amount, raw.occurred_at
            )
        )
    return tuple(events)


def decode_thin_webhook(body: bytes) -> ThinNotice | UndecodableBody:
    try:
        thin = msgspec.json.decode(body, type=_Thin)
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    return ThinNotice(thin.reference)

"""The fake PSP: takes a payment through a hosted page or a QR, captures
it, cancels it, and reports what happens (docs/plan-payments.md §2).

A made-up acquirer API, with PSP-side accounts as a path segment
(`A` = /_fake/psp/{account}):

    POST A/setup                 {"capture": sale|authorise_capture, "keyed",
                                  "webhook_url"?, "webhook_secret"?,
                                  "webhook_payload"?: full|thin}
                                 what a merchant sets on the PSP's dashboard
    POST A/payments              {"reference", "amount_minor", "currency",
                                  "method": card|qr, "return_url"}
         Idempotency-Key: on a keyed account a key seen before replays the
         first answer, for good
         201 {"payment"}         status requires_action, with its action
         402 {"error": "declined", "payment"}   after a scripted `decline`
    GET  A/payments?reference=…  {"payments": [...]} (lookup)
    GET  A/payments/{id}         {"payment", "events": [...]} (poll, thin)
    POST A/payments/{id}/capture {"amount_minor"}, Idempotency-Key
         200 {"payment"} | 409 {"error": "not_capturable", "payment"}
    POST A/payments/{id}/cancel  200 {"payment"} | 409 {"error":
                                 "already_final", "payment"}
    POST A/payments/{id}/release {"outcome": paid|declined|expired,
                                  "push"?: once|twice|bad_signature}
                                 what the buyer does on the hosted page or
                                 the phone does with the QR
    POST A/events/redeliver      {"all"?}: push failed webhooks again, or
                                 every event again (old ones after new)
    GET  /_fake/psp/pay/{id}     the hosted page: approve, decline, or leave
    POST /_fake/psp/pay/{id}     the buyer's choice; then back to return_url
    POST /_fake/psp/faults       {"account", "operation", "fault"}: script
                                 the next call of an operation to fail, or
                                 the next create to be declined
    POST A/settlements           {"faults"?: [...]}: cut a batch now, one per
                                 currency, of every captured payment not
                                 settled yet (docs/plan-payments.md §7.1)
    GET  A/settlement-files?after=      {"files": [...]}, oldest first
    GET  A/settlement-files/{name}      a report: CSV
    GET  A/settlements?after=           {"batches": [...]}, oldest first
    GET  A/settlements/{id}?page=n      {"batch", "lines", "next_page"}

Amounts are in the PSP's own minor units, ISO 4217's (IDR has 2 here),
so our client converts. Events are released by a call, never by a timer.
Its state is its own (schema `fake_vendor`, its own connections), as a
third party's would be; `psp_fake_charge` counts the money it moved.
"""

import csv
import hashlib
import hmac
import io
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, assert_never
from urllib.parse import parse_qsl

import htpy
import httpx2
import msgspec
import psycopg
from psycopg.rows import TupleRow
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from mor.db.connect import SESSION_OPTIONS
from mor.fakes.hang import Hang


type Capture = Literal["sale", "authorise_capture"]
type Status = Literal[
    "requires_action", "authorised", "captured", "declined", "expired", "cancelled"
]
type EventKind = Literal["authorised", "captured", "declined", "expired", "cancelled"]
type Operation = Literal["create", "capture", "cancel", "get", "settlements"]
type Fault = Literal["fail_500", "fail_500_after_work", "timeout_after_work", "decline"]
type Outcome = Literal["paid", "declined", "expired"]
type PushHow = Literal["once", "twice", "bad_signature"]
type Push = Literal["not_subscribed", "delivered", "failed"]

# What a cut can be scripted to get wrong, each once, in its first batch:
# a line for a payment nobody made, a gross that is not what was captured,
# a payment settled again under a new line id, a line of a type no client
# books (an adjustment), and a net that is not what the lines add up to.
type CutFault = Literal[
    "unknown_reference", "gross_differs", "settled_again", "unknown_type", "net_differs"
]
CUT_FAULTS: tuple[CutFault, ...] = (
    "unknown_reference",
    "gross_differs",
    "settled_again",
    "unknown_type",
    "net_differs",
)
SETTLEMENT_PAGE_SIZE = 2
CSV_HEADER = (
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
)
FAULTS: tuple[Fault, ...] = (
    "fail_500",
    "fail_500_after_work",
    "timeout_after_work",
    "decline",
)
SIGNATURE_HEADER = "Psp-Signature"
QR_LIFETIME = timedelta(minutes=15)


class SetupBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    capture: Capture
    keyed: bool
    webhook_url: str | None = None
    webhook_secret: str | None = None
    webhook_payload: Literal["full", "thin"] | None = None


class PaymentBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    reference: str
    amount_minor: int
    currency: str
    method: Literal["card", "qr"]
    return_url: str


class CaptureBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    amount_minor: int


class ReleaseBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    outcome: Outcome
    push: PushHow = "once"


class RedeliverBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    all: bool = False


class CutBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    faults: list[CutFault] = []


class FaultBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    account: str
    operation: Operation
    fault: Fault


class Action(msgspec.Struct, frozen=True):
    kind: Literal["redirect", "qr"]
    url: str  # the hosted page; a QR holds the same link
    expires_at: datetime | None


class Payment(msgspec.Struct, frozen=True):
    id: str
    reference: str
    amount_minor: int
    currency: str
    method: str
    status: Status
    action: Action | None


class Event(msgspec.Struct, frozen=True):
    id: str
    payment_id: str
    reference: str
    kind: EventKind
    amount_minor: int
    currency: str
    occurred_at: datetime


class FullWebhook(msgspec.Struct, frozen=True):
    events: list[Event]


class ThinWebhook(msgspec.Struct, frozen=True):
    """Only "this payment has news", with the id of the event behind it, so
    two notices are never the same bytes while a resend of one is."""

    id: str
    payment_id: str
    reference: str


type FakeConnection = psycopg.AsyncConnection[TupleRow]


def sign(secret: str, body: bytes) -> str:
    """HMAC-SHA256 over the raw body, hex."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def _connect(database_url: str) -> FakeConnection:
    return await psycopg.AsyncConnection.connect(
        database_url, options=f"-c search_path=fake_vendor {SESSION_OPTIONS}"
    )


def _error(status: int, code: str, payment: Payment | None = None) -> JSONResponse:
    body: dict[str, object] = {"error": code}
    if payment is not None:
        body["payment"] = msgspec.to_builtins(payment)
    return JSONResponse(body, status_code=status)


def _json(body: object, status: int = 200) -> Response:
    return Response(
        msgspec.json.encode(body), status_code=status, media_type="application/json"
    )


async def _take_fault(
    conn: FakeConnection, account: str, operation: Operation
) -> Fault | None:
    cur = await conn.execute(
        """
        DELETE FROM psp_fake_fault_next
        WHERE seq = (
          SELECT min(seq) FROM psp_fake_fault_next
          WHERE account = %s AND operation = %s
        )
        RETURNING fault::text
        """,
        (account, operation),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    for fault in FAULTS:
        if row[0] == fault:
            return fault
    raise AssertionError(f"unknown fault {row[0]!r}")


async def _account(conn: FakeConnection, name: str) -> tuple[Capture, bool] | None:
    cur = await conn.execute(
        "SELECT capture::text, keyed FROM psp_fake_account WHERE name = %s", (name,)
    )
    row = await cur.fetchone()
    if row is None:
        return None
    capture, keyed = row
    assert capture == "sale" or capture == "authorise_capture"
    assert isinstance(keyed, bool)
    return capture, keyed


def _payment(row: tuple[object, ...], base: str) -> Payment:
    payment_id, reference, amount, currency, method, status, expires = row
    assert isinstance(payment_id, str) and isinstance(reference, str)
    assert isinstance(amount, int) and isinstance(currency, str)
    assert isinstance(method, str) and isinstance(status, str)
    assert expires is None or isinstance(expires, datetime)
    known: Status | None = None
    for candidate in (
        "requires_action",
        "authorised",
        "captured",
        "declined",
        "expired",
        "cancelled",
    ):
        if status == candidate:
            known = candidate
    assert known is not None, status
    action: Action | None = None
    if known == "requires_action":
        url = f"{base}/_fake/psp/pay/{payment_id}"
        action = Action("qr" if method == "qr" else "redirect", url, expires)
    return Payment(payment_id, reference, amount, currency, method, known, action)


async def _read_payment(
    conn: FakeConnection, account: str, payment_id: str, base: str
) -> Payment | None:
    cur = await conn.execute(
        """
        SELECT id, reference, amount_minor, currency, method, status::text,
               qr_expires_at
        FROM psp_fake_payment WHERE account = %s AND id = %s
        """,
        (account, payment_id),
    )
    row = await cur.fetchone()
    return None if row is None else _payment(row, base)


async def _lock_payment(
    conn: FakeConnection, account: str, payment_id: str, base: str
) -> Payment | None:
    cur = await conn.execute(
        """
        SELECT id, reference, amount_minor, currency, method, status::text,
               qr_expires_at
        FROM psp_fake_payment WHERE account = %s AND id = %s FOR UPDATE
        """,
        (account, payment_id),
    )
    row = await cur.fetchone()
    return None if row is None else _payment(row, base)


async def _events(conn: FakeConnection, payment_id: str) -> list[Event]:
    cur = await conn.execute(
        """
        SELECT v.id, v.payment_id, p.reference, v.kind::text, v.amount_minor,
               p.currency, v.occurred_at
        FROM psp_fake_event v JOIN psp_fake_payment p ON p.id = v.payment_id
        WHERE v.payment_id = %s ORDER BY v.seq
        """,
        (payment_id,),
    )
    found: list[Event] = []
    for row in await cur.fetchall():
        found.append(_event(row))
    return found


def _event(row: tuple[object, ...]) -> Event:
    event_id, payment_id, reference, kind, amount, currency, occurred_at = row
    assert isinstance(event_id, str) and isinstance(payment_id, str)
    assert isinstance(reference, str) and isinstance(amount, int)
    assert isinstance(currency, str) and isinstance(occurred_at, datetime)
    for candidate in ("authorised", "captured", "declined", "expired", "cancelled"):
        if kind == candidate:
            return Event(
                event_id,
                payment_id,
                reference,
                candidate,
                amount,
                currency,
                occurred_at,
            )
    raise AssertionError(f"unknown event kind {kind!r}")


def fee_of(method: str, gross_minor: int) -> int:
    """The fake's own made-up price list, in its minor units: a card 2.9%
    plus 0.37 of a unit, a QR 0.7%. An IDR fee then has sub-rupiah parts,
    as a PSP counting IDR at 2 decimals would charge."""
    if method == "qr":
        return gross_minor * 7 // 1000
    return gross_minor * 29 // 1000 + 37


def payout_amount(currency: str, net_minor: int) -> int:
    """What the bank transfer carries: IDR in whole rupiah (half-even), so
    the payout differs from its lines by the PSP's rounding."""
    if currency != "IDR":
        return net_minor
    units, rest = divmod(net_minor, 100)
    if rest * 2 > 100 or (rest * 2 == 100 and units % 2 == 1):
        units += 1
    return units * 100


@dataclass(frozen=True)
class _CutLine:
    type: str
    payment_id: str
    reference: str
    gross_minor: int
    fee_minor: int
    fault: CutFault | None
    settles: bool  # a payment settled here for the first time


def _base(request: Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc}"


def make_app(
    database_url: str,
    outgoing: httpx2.AsyncClient,
    clock: Callable[[], datetime],
    hang: Hang,
) -> Starlette:
    """`outgoing` carries the webhooks: a real client in dev, our app
    in-process in tests. `clock` is the PSP's own clock (a QR's expiry);
    tests share theirs with it."""

    async def _push(
        conn: FakeConnection, account: str, event: Event, how: PushHow
    ) -> Push:
        cur = await conn.execute(
            """
            SELECT webhook_url, webhook_secret, webhook_payload::text
            FROM psp_fake_account WHERE name = %s
            """,
            (account,),
        )
        row = await cur.fetchone()
        if row is None or row[0] is None:
            return "not_subscribed"
        url, secret, payload = row
        assert isinstance(url, str) and isinstance(secret, str)
        if payload == "thin":
            body = msgspec.json.encode(
                ThinWebhook(event.id, event.payment_id, event.reference)
            )
        else:
            body = msgspec.json.encode(FullWebhook([event]))
        signature = sign(secret, body)
        if how == "bad_signature":
            signature = sign(secret + "-wrong", body)
        delivered = True
        for _ in range(2 if how == "twice" else 1):
            try:
                response = await outgoing.post(
                    url,
                    content=body,
                    headers={
                        SIGNATURE_HEADER: signature,
                        "Content-Type": "application/json",
                    },
                )
                if response.status_code != 200:
                    delivered = False
            except httpx2.TransportError:
                delivered = False
        return "delivered" if delivered else "failed"

    async def _record_event(
        conn: FakeConnection,
        account: str,
        payment: Payment,
        kind: EventKind,
        how: PushHow,
    ) -> Event:
        event = Event(
            f"evt_{uuid.uuid4().hex}",
            payment.id,
            payment.reference,
            kind,
            payment.amount_minor,
            payment.currency,
            clock(),
        )
        push = await _push(conn, account, event, how)
        await conn.execute(
            """
            INSERT INTO psp_fake_event
              (id, payment_id, kind, amount_minor, occurred_at, push)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (event.id, payment.id, kind, event.amount_minor, event.occurred_at, push),
        )
        return event

    async def _set_status(
        conn: FakeConnection, payment: Payment, status: Status, base: str
    ) -> Payment:
        cur = await conn.execute(
            """
            UPDATE psp_fake_payment SET status = %s WHERE id = %s
            RETURNING id, reference, amount_minor, currency, method, status::text,
                      qr_expires_at
            """,
            (status, payment.id),
        )
        row = await cur.fetchone()
        assert row is not None
        return _payment(row, base)

    async def _charge(conn: FakeConnection, payment: Payment) -> None:
        await conn.execute(
            """
            INSERT INTO psp_fake_charge (payment_id, reference, amount_minor, currency)
            VALUES (%s, %s, %s, %s)
            """,
            (payment.id, payment.reference, payment.amount_minor, payment.currency),
        )

    async def setup(request: Request) -> Response:
        try:
            body = msgspec.json.decode(await request.body(), type=SetupBody)
        except msgspec.DecodeError:
            return _error(400, "invalid_body")
        async with await _connect(database_url) as conn:
            await conn.execute(
                """
                INSERT INTO psp_fake_account
                  (name, capture, keyed, webhook_url, webhook_secret,
                   webhook_payload)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE
                SET capture = excluded.capture, keyed = excluded.keyed,
                    webhook_url = excluded.webhook_url,
                    webhook_secret = excluded.webhook_secret,
                    webhook_payload = excluded.webhook_payload
                """,
                (
                    request.path_params["account"],
                    body.capture,
                    body.keyed,
                    body.webhook_url,
                    body.webhook_secret,
                    body.webhook_payload,
                ),
            )
            await conn.commit()
        return Response(status_code=204)

    async def create(request: Request) -> Response:
        account = request.path_params["account"]
        try:
            body = msgspec.json.decode(await request.body(), type=PaymentBody)
        except msgspec.DecodeError:
            return _error(400, "invalid_body")
        key = request.headers.get("idempotency-key")
        base = _base(request)
        async with await _connect(database_url) as conn:
            known = await _account(conn, account)
            if known is None:
                return _error(404, "unknown_account")
            _, keyed = known
            fault = await _take_fault(conn, account, "create")
            if fault == "fail_500":
                await conn.commit()
                return _error(500, "internal_error")
            declined = fault == "decline"
            expires = clock() + QR_LIFETIME if body.method == "qr" else None
            # Insert first; on a keyed account a conflict on the key means
            # it was seen before, and the first payment is the answer.
            cur = await conn.execute(
                """
                INSERT INTO psp_fake_payment
                  (id, account, reference, idempotency_key, amount_minor,
                   currency, method, status, return_url, qr_expires_at,
                   created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (account, idempotency_key) DO NOTHING
                RETURNING id, reference, amount_minor, currency, method, status::text,
                          qr_expires_at
                """,
                (
                    f"pay_{uuid.uuid4().hex}",
                    account,
                    body.reference,
                    key if keyed else None,
                    body.amount_minor,
                    body.currency,
                    body.method,
                    "declined" if declined else "requires_action",
                    body.return_url,
                    expires,
                    clock(),
                ),
            )
            row = await cur.fetchone()
            status = 201
            if row is None:
                cur = await conn.execute(
                    """
                    SELECT id, reference, amount_minor, currency, method, status::text,
                           qr_expires_at
                    FROM psp_fake_payment
                    WHERE account = %s AND idempotency_key = %s
                    """,
                    (account, key),
                )
                row = await cur.fetchone()
                status = 200
            assert row is not None
            payment = _payment(row, base)
            await conn.commit()
        if fault == "fail_500_after_work":
            return _error(500, "internal_error")
        if fault == "timeout_after_work":
            await hang()
        if payment.status == "declined":
            return _error(402, "declined", payment)
        return _json({"payment": payment}, status)

    async def find(request: Request) -> Response:
        reference = request.query_params.get("reference")
        if reference is None:
            return _error(400, "reference_required")
        base = _base(request)
        async with await _connect(database_url) as conn:
            fault = await _take_fault(conn, request.path_params["account"], "get")
            await conn.commit()
            if fault in ("fail_500", "fail_500_after_work"):
                return _error(500, "internal_error")
            cur = await conn.execute(
                """
                SELECT id, reference, amount_minor, currency, method, status::text,
                       qr_expires_at
                FROM psp_fake_payment
                WHERE account = %s AND reference = %s ORDER BY created_at, id
                """,
                (request.path_params["account"], reference),
            )
            rows = await cur.fetchall()
        if fault == "timeout_after_work":
            await hang()
        payments: list[Payment] = []
        for row in rows:
            payments.append(_payment(row, base))
        return _json({"payments": payments})

    async def get(request: Request) -> Response:
        account = request.path_params["account"]
        async with await _connect(database_url) as conn:
            fault = await _take_fault(conn, account, "get")
            await conn.commit()
            if fault in ("fail_500", "fail_500_after_work"):
                return _error(500, "internal_error")
            payment = await _read_payment(
                conn, account, request.path_params["payment_id"], _base(request)
            )
            if payment is None:
                return _error(404, "unknown_payment")
            events = await _events(conn, payment.id)
        if fault == "timeout_after_work":
            await hang()
        return _json({"payment": payment, "events": events})

    async def capture(request: Request) -> Response:
        account = request.path_params["account"]
        try:
            body = msgspec.json.decode(await request.body(), type=CaptureBody)
        except msgspec.DecodeError:
            return _error(400, "invalid_body")
        key = request.headers.get("idempotency-key")
        base = _base(request)
        async with await _connect(database_url) as conn:
            known = await _account(conn, account)
            if known is None:
                return _error(404, "unknown_account")
            _, keyed = known
            fault = await _take_fault(conn, account, "capture")
            if fault == "fail_500":
                await conn.commit()
                return _error(500, "internal_error")
            payment = await _lock_payment(
                conn, account, request.path_params["payment_id"], base
            )
            if payment is None:
                return _error(404, "unknown_payment")
            replay = False
            if keyed and key is not None:
                cur = await conn.execute(
                    """
                    INSERT INTO psp_fake_capture_key
                      (account, idempotency_key, payment_id)
                    VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING 1
                    """,
                    (account, key, payment.id),
                )
                replay = await cur.fetchone() is None
            if replay:
                pass  # the first answer stands: the payment as it is now
            elif payment.status != "authorised" or body.amount_minor != (
                payment.amount_minor
            ):
                await conn.commit()
                return _error(409, "not_capturable", payment)
            else:
                payment = await _set_status(conn, payment, "captured", base)
                await _charge(conn, payment)
                await _record_event(conn, account, payment, "captured", "once")
            await conn.commit()
        if fault == "fail_500_after_work":
            return _error(500, "internal_error")
        if fault == "timeout_after_work":
            await hang()
        return _json({"payment": payment})

    async def cancel(request: Request) -> Response:
        account = request.path_params["account"]
        base = _base(request)
        async with await _connect(database_url) as conn:
            fault = await _take_fault(conn, account, "cancel")
            if fault == "fail_500":
                await conn.commit()
                return _error(500, "internal_error")
            payment = await _lock_payment(
                conn, account, request.path_params["payment_id"], base
            )
            if payment is None:
                return _error(404, "unknown_payment")
            if payment.status not in ("requires_action", "authorised"):
                await conn.commit()
                return _error(409, "already_final", payment)
            payment = await _set_status(conn, payment, "cancelled", base)
            await _record_event(conn, account, payment, "cancelled", "once")
            await conn.commit()
        if fault == "fail_500_after_work":
            return _error(500, "internal_error")
        if fault == "timeout_after_work":
            await hang()
        return _json({"payment": payment})

    async def _release(
        conn: FakeConnection,
        account: str,
        payment: Payment,
        outcome: Outcome,
        how: PushHow,
        base: str,
    ) -> Event | None:
        """The buyer's (or the phone's) answer, once, while it is wanted."""
        if payment.status != "requires_action":
            return None
        known = await _account(conn, account)
        assert known is not None
        capture_mode, _ = known
        match outcome:
            case "paid":
                if capture_mode == "sale":
                    payment = await _set_status(conn, payment, "captured", base)
                    await _charge(conn, payment)
                    return await _record_event(conn, account, payment, "captured", how)
                payment = await _set_status(conn, payment, "authorised", base)
                return await _record_event(conn, account, payment, "authorised", how)
            case "declined":
                payment = await _set_status(conn, payment, "declined", base)
                return await _record_event(conn, account, payment, "declined", how)
            case "expired":
                payment = await _set_status(conn, payment, "expired", base)
                return await _record_event(conn, account, payment, "expired", how)
            case _:
                assert_never(outcome)

    async def release(request: Request) -> Response:
        account = request.path_params["account"]
        raw = await request.body()
        try:
            body = msgspec.json.decode(raw, type=ReleaseBody)
        except msgspec.DecodeError:
            return _error(400, "invalid_body")
        base = _base(request)
        async with await _connect(database_url) as conn:
            payment = await _lock_payment(
                conn, account, request.path_params["payment_id"], base
            )
            if payment is None:
                return _error(404, "unknown_payment")
            event = await _release(
                conn, account, payment, body.outcome, body.push, base
            )
            await conn.commit()
        if event is None:
            return _error(409, "not_waiting", payment)
        return _json(event)

    async def redeliver(request: Request) -> Response:
        account = request.path_params["account"]
        raw = await request.body()
        try:
            body = msgspec.json.decode(raw or b"{}", type=RedeliverBody)
        except msgspec.DecodeError:
            return _error(400, "invalid_body")
        async with await _connect(database_url) as conn:
            cur = await conn.execute(
                """
                SELECT v.id, v.payment_id, p.reference, v.kind::text,
                       v.amount_minor, p.currency, v.occurred_at
                FROM psp_fake_event v JOIN psp_fake_payment p ON p.id = v.payment_id
                WHERE p.account = %s AND (%s OR v.push = 'failed')
                ORDER BY v.seq DESC
                """,
                (account, body.all),
            )
            rows = await cur.fetchall()
            delivered = 0
            # Newest first: an older event arrives after a newer one.
            for row in rows:
                event = _event(row)
                push = await _push(conn, account, event, "once")
                await conn.execute(
                    "UPDATE psp_fake_event SET push = %s WHERE id = %s",
                    (push, event.id),
                )
                if push == "delivered":
                    delivered += 1
            await conn.commit()
        return JSONResponse({"redelivered": delivered})

    async def hosted_page(request: Request) -> Response:
        payment_id = request.path_params["payment_id"]
        async with await _connect(database_url) as conn:
            cur = await conn.execute(
                "SELECT account, amount_minor, currency, method, status::text"
                " FROM psp_fake_payment WHERE id = %s",
                (payment_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return HTMLResponse("No such payment.", status_code=404)
        _, amount, currency, method, status = row
        choices: htpy.Node = htpy.p[f"This payment is {status}."]
        if status == "requires_action":
            choices = htpy.form(method="post")[
                htpy.button(type="submit", name="outcome", value="paid")[
                    "Approve" if method == "card" else "Pay from the app"
                ],
                " ",
                htpy.button(type="submit", name="outcome", value="declined")["Decline"],
                " ",
                htpy.button(type="submit", name="outcome", value="expired")[
                    "Let it expire"
                ],
            ]
        page = htpy.html(lang="en")[
            htpy.head[
                htpy.meta(charset="utf-8"),
                htpy.meta(
                    name="viewport", content="width=device-width, initial-scale=1"
                ),
                htpy.link(rel="icon", href="data:,"),
                htpy.title["Fake PSP"],
            ],
            htpy.body[
                htpy.h1["Fake PSP"],
                htpy.p[f"Pay {amount} {currency} (minor units) by {method}."],
                choices,
            ],
        ]
        return HTMLResponse(str(page))

    async def hosted_answer(request: Request) -> Response:
        payment_id = request.path_params["payment_id"]
        try:
            form = dict(parse_qsl((await request.body()).decode("utf-8")))
        except UnicodeDecodeError:
            form = {}
        outcome: Outcome | None = None
        for candidate in ("paid", "declined", "expired"):
            if form.get("outcome") == candidate:
                outcome = candidate
        if outcome is None:
            return HTMLResponse("Pick an outcome.", status_code=400)
        base = _base(request)
        async with await _connect(database_url) as conn:
            cur = await conn.execute(
                "SELECT account, return_url FROM psp_fake_payment WHERE id = %s",
                (payment_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return HTMLResponse("No such payment.", status_code=404)
            account, return_url = row
            assert isinstance(account, str) and isinstance(return_url, str)
            payment = await _lock_payment(conn, account, payment_id, base)
            assert payment is not None
            await _release(conn, account, payment, outcome, "once", base)
            await conn.commit()
        return RedirectResponse(return_url, status_code=303)

    async def cut_settlement(request: Request) -> Response:
        account = request.path_params["account"]
        try:
            body = msgspec.json.decode(await request.body() or b"{}", type=CutBody)
        except msgspec.DecodeError:
            return _error(400, "invalid_body")
        faults = set(body.faults)
        async with await _connect(database_url) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM psp_fake_account WHERE name = %s FOR UPDATE", (account,)
            )
            if await cur.fetchone() is None:
                return _error(404, "unknown_account")
            cur = await conn.execute(
                """
                SELECT p.id, p.reference, p.amount_minor, p.currency, p.method
                FROM psp_fake_payment p
                WHERE p.account = %s AND p.status = 'captured'
                  AND NOT EXISTS (SELECT 1 FROM psp_fake_settled s
                                  WHERE s.payment_id = p.id)
                ORDER BY p.created_at, p.id
                """,
                (account,),
            )
            groups: dict[str, list[_CutLine]] = {}
            for payment_id, reference, amount, currency, method in await cur.fetchall():
                groups.setdefault(currency, []).append(
                    _CutLine(
                        "capture",
                        payment_id,
                        reference,
                        amount,
                        fee_of(method, amount),
                        None,
                        True,
                    )
                )
            # Settled again: a payment whose first settlement was clean.
            if "settled_again" in faults:
                cur = await conn.execute(
                    """
                    SELECT p.id, p.reference, p.amount_minor, p.currency, p.method
                    FROM psp_fake_settled s
                    JOIN psp_fake_payment p ON p.id = s.payment_id
                    JOIN psp_fake_settlement_line l
                      ON l.payment_id = p.id AND l.fault IS NULL
                    WHERE p.account = %s ORDER BY p.created_at, p.id LIMIT 1
                    """,
                    (account,),
                )
                again = await cur.fetchone()
                if again is not None:
                    payment_id, reference, amount, currency, method = again
                    groups.setdefault(currency, []).append(
                        _CutLine(
                            "capture",
                            payment_id,
                            reference,
                            amount,
                            fee_of(method, amount),
                            "settled_again",
                            False,
                        )
                    )
            made: list[str] = []
            for currency in sorted(groups):
                lines = groups[currency]
                first = not made
                if first and "gross_differs" in faults:
                    for index, line in enumerate(lines):
                        if line.settles:
                            lines[index] = _CutLine(
                                line.type,
                                line.payment_id,
                                line.reference,
                                line.gross_minor + 100,
                                line.fee_minor,
                                "gross_differs",
                                True,
                            )
                            break
                if first and "unknown_reference" in faults:
                    lines.append(
                        _CutLine(
                            "capture",
                            f"pay_{uuid.uuid4().hex[:12]}",
                            str(uuid.uuid4()),
                            1000,
                            fee_of("card", 1000),
                            "unknown_reference",
                            False,
                        )
                    )
                if first and "unknown_type" in faults:
                    lines.append(
                        _CutLine("adjustment", "-", "-", -500, 0, "unknown_type", False)
                    )
                net = 0
                for line in lines:
                    net += line.gross_minor - line.fee_minor
                net = payout_amount(currency, net)
                batch_fault: CutFault | None = None
                if first and "net_differs" in faults:
                    net -= 100
                    batch_fault = "net_differs"
                batch_id = f"stl_{uuid.uuid4().hex[:12]}"
                await conn.execute(
                    """
                    INSERT INTO psp_fake_settlement_batch
                      (id, account, currency, net_minor, paid_out_at, fault)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (batch_id, account, currency, net, clock(), batch_fault),
                )
                for number, line in enumerate(lines, start=1):
                    await conn.execute(
                        """
                        INSERT INTO psp_fake_settlement_line
                          (id, batch_id, type, payment_id, reference, gross_minor,
                           fee_minor, fault)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            f"{batch_id}-{number}",
                            batch_id,
                            line.type,
                            line.payment_id,
                            line.reference,
                            line.gross_minor,
                            line.fee_minor,
                            line.fault,
                        ),
                    )
                    if line.settles:
                        await conn.execute(
                            "INSERT INTO psp_fake_settled (payment_id, batch_id)"
                            " VALUES (%s, %s)",
                            (line.payment_id, batch_id),
                        )
                made.append(batch_id)
            await conn.commit()
        return _json({"batches": made})

    async def _batches_after(
        conn: FakeConnection, account: str, after: str
    ) -> list[str]:
        cur = await conn.execute(
            """
            SELECT id FROM psp_fake_settlement_batch
            WHERE account = %(account)s
              AND seq > coalesce((SELECT seq FROM psp_fake_settlement_batch
                                  WHERE account = %(account)s AND id = %(after)s), 0)
            ORDER BY seq
            """,
            {"account": account, "after": after},
        )
        found: list[str] = []
        for row in await cur.fetchall():
            found.append(row[0])
        return found

    async def _batch(
        conn: FakeConnection, account: str, batch_id: str
    ) -> tuple[str, int, datetime] | None:
        cur = await conn.execute(
            """
            SELECT currency, net_minor, paid_out_at FROM psp_fake_settlement_batch
            WHERE account = %s AND id = %s
            """,
            (account, batch_id),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        currency, net, paid_out_at = row
        assert isinstance(currency, str) and isinstance(net, int)
        assert isinstance(paid_out_at, datetime)
        return currency, net, paid_out_at

    async def _batch_lines(
        conn: FakeConnection, batch_id: str
    ) -> list[tuple[str, str, str, str, int, int]]:
        cur = await conn.execute(
            """
            SELECT id, type, payment_id, reference, gross_minor, fee_minor
            FROM psp_fake_settlement_line WHERE batch_id = %s ORDER BY seq
            """,
            (batch_id,),
        )
        found: list[tuple[str, str, str, str, int, int]] = []
        for row in await cur.fetchall():
            line_id, line_type, payment_id, reference, gross, fee = row
            found.append((line_id, line_type, payment_id, reference, gross, fee))
        return found

    async def _read_fault(conn: FakeConnection, account: str) -> Fault | None:
        fault = await _take_fault(conn, account, "settlements")
        await conn.commit()
        return fault

    async def list_files(request: Request) -> Response:
        account = request.path_params["account"]
        async with await _connect(database_url) as conn:
            fault = await _read_fault(conn, account)
            if fault in ("fail_500", "fail_500_after_work"):
                return _error(500, "internal_error")
            names: list[str] = []
            for batch_id in await _batches_after(
                conn,
                account,
                request.query_params.get("after", "").removesuffix(".csv"),
            ):
                names.append(f"{batch_id}.csv")
        if fault == "timeout_after_work":
            await hang()
        return _json({"files": names})

    async def get_file(request: Request) -> Response:
        account = request.path_params["account"]
        batch_id = str(request.path_params["name"]).removesuffix(".csv")
        async with await _connect(database_url) as conn:
            fault = await _read_fault(conn, account)
            if fault in ("fail_500", "fail_500_after_work"):
                return _error(500, "internal_error")
            batch = await _batch(conn, account, batch_id)
            if batch is None:
                return _error(404, "unknown_file")
            lines = await _batch_lines(conn, batch_id)
        currency, net, paid_out_at = batch
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(CSV_HEADER)
        writer.writerow(
            [
                "batch",
                batch_id,
                "",
                "",
                "",
                currency,
                "",
                "",
                net,
                paid_out_at.isoformat(),
            ]
        )
        for line_id, line_type, payment_id, reference, gross, fee in lines:
            writer.writerow(
                [
                    "line",
                    line_id,
                    line_type,
                    payment_id,
                    reference,
                    currency,
                    gross,
                    fee,
                    gross - fee,
                    "",
                ]
            )
        if fault == "timeout_after_work":
            await hang()
        return Response(out.getvalue().encode("utf-8"), media_type="text/csv")

    async def list_batches(request: Request) -> Response:
        account = request.path_params["account"]
        async with await _connect(database_url) as conn:
            fault = await _read_fault(conn, account)
            if fault in ("fail_500", "fail_500_after_work"):
                return _error(500, "internal_error")
            found = await _batches_after(
                conn, account, request.query_params.get("after", "")
            )
        if fault == "timeout_after_work":
            await hang()
        return _json({"batches": found})

    async def get_batch_page(request: Request) -> Response:
        account = request.path_params["account"]
        batch_id = request.path_params["batch_id"]
        try:
            page = int(request.query_params.get("page", "1"))
        except ValueError:
            return _error(400, "invalid_page")
        if page < 1:
            return _error(400, "invalid_page")
        async with await _connect(database_url) as conn:
            fault = await _read_fault(conn, account)
            if fault in ("fail_500", "fail_500_after_work"):
                return _error(500, "internal_error")
            batch = await _batch(conn, account, batch_id)
            if batch is None:
                return _error(404, "unknown_batch")
            lines = await _batch_lines(conn, batch_id)
        currency, net, paid_out_at = batch
        start = (page - 1) * SETTLEMENT_PAGE_SIZE
        shown = lines[start : start + SETTLEMENT_PAGE_SIZE]
        more = start + SETTLEMENT_PAGE_SIZE < len(lines)
        page_lines: list[dict[str, object]] = []
        for line_id, line_type, payment_id, reference, gross, fee in shown:
            page_lines.append(
                {
                    "id": line_id,
                    "type": line_type,
                    "payment_id": payment_id,
                    "reference": reference,
                    "gross_minor": gross,
                    "fee_minor": fee,
                }
            )
        if fault == "timeout_after_work":
            await hang()
        return _json(
            {
                "batch": {
                    "id": batch_id,
                    "currency": currency,
                    "net_minor": net,
                    "paid_out_at": paid_out_at,
                },
                "lines": page_lines,
                "next_page": page + 1 if more else None,
            }
        )

    async def script_fault(request: Request) -> Response:
        try:
            body = msgspec.json.decode(await request.body(), type=FaultBody)
        except msgspec.DecodeError:
            return _error(400, "invalid_body")
        async with await _connect(database_url) as conn:
            await conn.execute(
                "INSERT INTO psp_fake_fault_next (account, operation, fault)"
                " VALUES (%s, %s, %s)",
                (body.account, body.operation, body.fault),
            )
            await conn.commit()
        return Response(status_code=204)

    account_path = "/_fake/psp/{account}"
    return Starlette(
        routes=[
            Route("/_fake/psp/faults", script_fault, methods=["POST"]),
            Route("/_fake/psp/pay/{payment_id}", hosted_page, methods=["GET"]),
            Route("/_fake/psp/pay/{payment_id}", hosted_answer, methods=["POST"]),
            Route(f"{account_path}/setup", setup, methods=["POST"]),
            Route(f"{account_path}/payments", create, methods=["POST"]),
            Route(f"{account_path}/payments", find, methods=["GET"]),
            Route(f"{account_path}/payments/{{payment_id}}", get, methods=["GET"]),
            Route(
                f"{account_path}/payments/{{payment_id}}/capture",
                capture,
                methods=["POST"],
            ),
            Route(
                f"{account_path}/payments/{{payment_id}}/cancel",
                cancel,
                methods=["POST"],
            ),
            Route(
                f"{account_path}/payments/{{payment_id}}/release",
                release,
                methods=["POST"],
            ),
            Route(f"{account_path}/events/redeliver", redeliver, methods=["POST"]),
            Route(f"{account_path}/settlements", cut_settlement, methods=["POST"]),
            Route(f"{account_path}/settlements", list_batches, methods=["GET"]),
            Route(
                f"{account_path}/settlements/{{batch_id}}",
                get_batch_page,
                methods=["GET"],
            ),
            Route(f"{account_path}/settlement-files", list_files, methods=["GET"]),
            Route(
                f"{account_path}/settlement-files/{{name}}", get_file, methods=["GET"]
            ),
        ]
    )

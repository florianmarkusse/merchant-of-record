"""Client for the fake echo vendor's API (mor.fakes.echo), shaped like any
real provider module: our records in, our result union out, never our
database. Transitional with the echo flow (docs/plan-messaging.md §10).

How a call ends:
- connection refused or no such host: `Unavailable`, it never left;
- a timeout (the whole call is bounded by the account's timeout), a
  dropped connection or a 5xx: `OutcomeUnknown`, the vendor may have done
  the work;
- a documented 4xx error: `Refused`.
"""

import asyncio
import csv
import hashlib
import hmac
import io
from typing import Literal

import httpx2
import msgspec

from mor.dates import AwareDatetime, parse_instant, to_utc
from mor.echo.port import (
    Cancelled,
    CancelResult,
    EchoAccount,
    EventKind,
    FindResult,
    Found,
    OutcomeUnknown,
    Page,
    Refused,
    ReportList,
    SendRequest,
    SendResult,
    Sent,
    Unavailable,
    UndecodableBody,
    VendorEcho,
    VendorEvent,
)


class _Echo(msgspec.Struct, frozen=True):
    id: str
    reference: str
    state: Literal["live", "cancelled"]


class _Echoes(msgspec.Struct, frozen=True):
    echoes: list[_Echo]


class _Error(msgspec.Struct, frozen=True):
    error: str


type _Answer = httpx2.Response | Unavailable | OutcomeUnknown


async def _request(
    http: httpx2.AsyncClient,
    account: EchoAccount,
    method: Literal["GET", "POST"],
    path: str,
    json: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> _Answer:
    try:
        async with asyncio.timeout(account.timeout.total_seconds()):
            return await http.request(
                method,
                account.base_url + path,
                json=json,
                headers=headers,
                params=params,
            )
    except httpx2.ConnectError as error:
        return Unavailable(repr(error))
    except (TimeoutError, httpx2.TransportError) as error:
        return OutcomeUnknown(repr(error))


def _refusal_or_unknown(response: httpx2.Response) -> Refused | OutcomeUnknown:
    if 400 <= response.status_code < 500:
        try:
            body = msgspec.json.decode(response.content, type=_Error)
        except msgspec.DecodeError:
            return OutcomeUnknown(f"undocumented {response.status_code}")
        return Refused(body.error)
    return OutcomeUnknown(f"status {response.status_code}")


def _decode_echo(response: httpx2.Response) -> _Echo | OutcomeUnknown:
    try:
        return msgspec.json.decode(response.content, type=_Echo)
    except msgspec.DecodeError as error:
        return OutcomeUnknown(f"unreadable answer: {error}")


async def send(
    http: httpx2.AsyncClient, account: EchoAccount, request: SendRequest
) -> SendResult:
    answer = await _request(
        http,
        account,
        "POST",
        "/echoes",
        json={"reference": request.reference, "message": request.message},
        headers={"Idempotency-Key": request.idempotency_key},
    )
    if not isinstance(answer, httpx2.Response):
        return answer
    if answer.status_code not in (200, 201):
        return _refusal_or_unknown(answer)
    echo = _decode_echo(answer)
    if isinstance(echo, OutcomeUnknown):
        return echo
    return Sent(echo.id)


async def find_by_reference(
    http: httpx2.AsyncClient, account: EchoAccount, reference: str
) -> FindResult:
    answer = await _request(
        http, account, "GET", "/echoes", params={"reference": reference}
    )
    if not isinstance(answer, httpx2.Response):
        return answer
    if answer.status_code != 200:
        return OutcomeUnknown(f"status {answer.status_code}")
    try:
        body = msgspec.json.decode(answer.content, type=_Echoes)
    except msgspec.DecodeError as error:
        return OutcomeUnknown(f"unreadable answer: {error}")
    echoes: list[VendorEcho] = []
    for echo in body.echoes:
        echoes.append(VendorEcho(echo.id, echo.reference, echo.state))
    return Found(tuple(echoes))


async def cancel(
    http: httpx2.AsyncClient, account: EchoAccount, echo_id: str
) -> CancelResult:
    answer = await _request(http, account, "POST", f"/echoes/{echo_id}/cancel")
    if not isinstance(answer, httpx2.Response):
        return answer
    if answer.status_code != 200:
        return _refusal_or_unknown(answer)
    echo = _decode_echo(answer)
    if isinstance(echo, OutcomeUnknown):
        return echo
    return Cancelled(echo.id)


SIGNATURE_HEADER = "Echo-Signature"


class _WebhookEvent(msgspec.Struct, frozen=True):
    id: str
    echo_id: str
    reference: str
    kind: Literal["confirmed", "archived"]
    occurred_at: AwareDatetime


class _FullWebhook(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    events: list[_WebhookEvent]


def verify_webhook(secret: str, body: bytes, signature: str | None) -> bool:
    """The vendor signs the raw body with HMAC-SHA256 (hex). Compared in
    constant time."""
    if signature is None or secret == "":
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def decode_full_webhook(body: bytes) -> tuple[VendorEvent, ...] | UndecodableBody:
    try:
        webhook = msgspec.json.decode(body, type=_FullWebhook)
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    events: list[VendorEvent] = []
    for event in webhook.events:
        events.append(
            VendorEvent(
                event.id,
                event.reference,
                EventKind(event.kind),
                to_utc(event.occurred_at),
            )
        )
    return tuple(events)


class _ThinWebhook(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    id: str  # the event behind the notice; the fetch brings the event itself
    echo_id: str
    reference: str


def decode_thin_webhook(body: bytes) -> str | UndecodableBody:
    """A thin webhook names an event id, the echo and our reference, not
    what happened: that comes from the fetch it starts."""
    try:
        webhook = msgspec.json.decode(body, type=_ThinWebhook)
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    return webhook.reference


async def fetch_events(
    http: httpx2.AsyncClient, account: EchoAccount, reference: str
) -> Page | Unavailable | OutcomeUnknown:
    """One page of events for our reference, as raw bytes: decoding happens
    after they are stored, like a webhook's."""
    answer = await _request(
        http, account, "GET", "/events", params={"reference": reference}
    )
    if not isinstance(answer, httpx2.Response):
        return answer
    if answer.status_code != 200:
        return OutcomeUnknown(f"status {answer.status_code}")
    return Page(answer.content)


class _ReportNames(msgspec.Struct, frozen=True):
    reports: list[str]


async def list_reports(
    http: httpx2.AsyncClient, account: EchoAccount, after: str
) -> ReportList | Unavailable | OutcomeUnknown:
    answer = await _request(http, account, "GET", "/reports", params={"after": after})
    if not isinstance(answer, httpx2.Response):
        return answer
    if answer.status_code != 200:
        return OutcomeUnknown(f"status {answer.status_code}")
    try:
        body = msgspec.json.decode(answer.content, type=_ReportNames)
    except msgspec.DecodeError as error:
        return OutcomeUnknown(f"unreadable answer: {error}")
    return ReportList(tuple(body.reports))


async def fetch_report(
    http: httpx2.AsyncClient, account: EchoAccount, name: str
) -> Page | Unavailable | OutcomeUnknown:
    answer = await _request(http, account, "GET", f"/reports/{name}")
    if not isinstance(answer, httpx2.Response):
        return answer
    if answer.status_code != 200:
        return OutcomeUnknown(f"status {answer.status_code}")
    return Page(answer.content)


REPORT_HEADER = ["event_id", "echo_id", "reference", "kind", "occurred_at"]


def decode_report(body: bytes) -> tuple[VendorEvent, ...] | UndecodableBody:
    """A report file (CSV with a header row) into our event records."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as error:
        return UndecodableBody(str(error))
    try:
        rows = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as error:
        return UndecodableBody(f"not CSV: {error}")
    if not rows or rows[0] != REPORT_HEADER:
        return UndecodableBody("missing or wrong header row")
    events: list[VendorEvent] = []
    for number, row in enumerate(rows[1:], start=2):
        if len(row) != len(REPORT_HEADER):
            return UndecodableBody(f"row {number}: {len(row)} fields")
        event_id, _, reference, kind, occurred_at = row
        if kind not in ("confirmed", "archived"):
            return UndecodableBody(f"row {number}: unknown kind {kind!r}")
        moment = parse_instant(occurred_at)
        if moment is None:
            return UndecodableBody(f"row {number}: no time with a zone {occurred_at!r}")
        events.append(VendorEvent(event_id, reference, EventKind(kind), moment))
    return tuple(events)

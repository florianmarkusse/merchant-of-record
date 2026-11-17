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
from typing import Literal

import httpx2
import msgspec

from mor.echo.port import (
    Cancelled,
    CancelResult,
    EchoAccount,
    FindResult,
    Found,
    OutcomeUnknown,
    Refused,
    SendRequest,
    SendResult,
    Sent,
    Unavailable,
    VendorEcho,
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

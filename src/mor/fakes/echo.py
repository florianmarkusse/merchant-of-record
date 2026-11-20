"""The fake echo vendor: stores a message, returns an id, and later reports
what happens to it (docs/plan-messaging.md §10 and §12).

A made-up vendor API in three flavours, one per duplicate guard, with
vendor-side accounts as a path segment (`A` = /_fake/echo/{flavour}/{account}):

    POST A/echoes                    {"reference", "message"}; keyed: an
         Idempotency-Key it has seen returns the first echo, for good
    GET  A/echoes?reference=…        every echo for a reference
    POST A/echoes/{id}/cancel        cancellable only
    POST A/webhooks                  {"url", "secret", "payload": full|thin}
    POST A/echoes/{id}/events/release   {"kind"?, "push"?: once|twice|bad_signature}
         releases the echo's next event (or the named one) and, if the
         account is subscribed, pushes a signed webhook at once
    POST A/events/redeliver          pushes again every webhook that failed
    GET  A/events?reference=…        every released event for a reference
         (the poll page, and what a thin webhook points at)
    POST A/reports/cut               cut a report of the events released
         since the account's previous one (CSV)
    GET  A/reports?after=…           report names after a name, in order
    GET  A/reports/{name}            one report
    POST /_fake/echo/faults          {"flavour", "fault"}: script the next
         send of a flavour to fail

Events are released by a call, never by a timer. Its state is its own
(schema `fake_vendor`, its own connections), as a third party's would be.
Transitional with the echo flow: removed once the fake PSP exercises the
same machinery (slice 5), unless it serves something else by then.
"""

import hashlib
import hmac
import uuid
from datetime import UTC, datetime
from typing import Literal

import httpx2
import msgspec
import psycopg
from psycopg.rows import TupleRow
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from mor.fakes.hang import Hang


type Flavour = Literal["keyed", "cancellable", "plain"]
type Fault = Literal["fail_500", "fail_500_after_work", "timeout_after_work", "refuse"]
type EventKind = Literal["confirmed", "archived"]
type PushHow = Literal["once", "twice", "bad_signature"]
FLAVOURS: tuple[Flavour, ...] = ("keyed", "cancellable", "plain")
SIGNATURE_HEADER = "Echo-Signature"


class SendBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    reference: str
    message: str


class FaultBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    flavour: Flavour
    fault: Fault
    operation: Literal["send", "events"] = "send"


class WebhookBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    url: str
    secret: str
    payload: Literal["full", "thin"]


class ReleaseBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    kind: EventKind | None = None
    push: PushHow = "once"


class _Event(msgspec.Struct, frozen=True):
    id: str
    echo_id: str
    reference: str
    kind: EventKind
    occurred_at: datetime


class _FullWebhook(msgspec.Struct, frozen=True):
    events: list[_Event]


class _ThinWebhook(msgspec.Struct, frozen=True):
    """Only "this echo has news", but with the id of the event behind it,
    as real thin webhooks carry one: two notices are never the same bytes,
    while a resend of one notice is."""

    id: str
    echo_id: str
    reference: str


type FakeConnection = psycopg.AsyncConnection[TupleRow]


def sign(secret: str, body: bytes) -> str:
    """The signature the fake sends, HMAC-SHA256 over the raw body, hex."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def _connect(database_url: str) -> FakeConnection:
    return await psycopg.AsyncConnection.connect(
        database_url, options="-c search_path=fake_vendor"
    )


def _flavour(request: Request) -> Flavour | None:
    raw = request.path_params["flavour"]
    for flavour in FLAVOURS:
        if raw == flavour:
            return flavour
    return None


def _echo_json(row: tuple[object, ...]) -> dict[str, object]:
    echo_id, reference, message, state = row
    return {"id": echo_id, "reference": reference, "message": message, "state": state}


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


async def _take_fault(
    conn: FakeConnection, flavour: Flavour, operation: Literal["send", "events"]
) -> Fault | None:
    cur = await conn.execute(
        """
        DELETE FROM echo_fault
        WHERE seq = (
          SELECT min(seq) FROM echo_fault WHERE flavour = %s AND operation = %s
        )
        RETURNING fault::text
        """,
        (flavour, operation),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    fault = row[0]
    assert fault in ("fail_500", "fail_500_after_work", "timeout_after_work", "refuse")
    return fault


async def _known_key(conn: FakeConnection, flavour: Flavour, key: str | None) -> bool:
    if flavour != "keyed" or key is None:
        return False
    cur = await conn.execute(
        "SELECT 1 FROM echo WHERE flavour = 'keyed' AND idempotency_key = %s", (key,)
    )
    return await cur.fetchone() is not None


def make_app(database_url: str, outgoing: httpx2.AsyncClient, hang: Hang) -> Starlette:
    """`outgoing` carries the fake's webhooks: a real client in dev, our app
    in-process in tests."""

    async def send(request: Request) -> Response:
        flavour = _flavour(request)
        if flavour is None:
            return _error(404, "unknown_flavour")
        account = request.path_params["account"]
        try:
            body = msgspec.json.decode(await request.body(), type=SendBody)
        except msgspec.DecodeError:
            return _error(400, "invalid_body")
        key = request.headers.get("idempotency-key")

        async with await _connect(database_url) as conn:
            fault = await _take_fault(conn, flavour, "send")
            if fault == "fail_500":
                await conn.commit()
                return _error(500, "internal_error")
            if fault == "refuse" and not await _known_key(conn, flavour, key):
                # A known key is replayed, never refused: that is the keyed
                # flavour's promise.
                await conn.commit()
                return _error(422, "message_rejected")

            # Insert first; for `keyed`, a conflict on the key means it was
            # seen before, and the first echo is the answer.
            cur = await conn.execute(
                """
                INSERT INTO echo
                  (id, flavour, vendor_account, reference, idempotency_key,
                   message, state, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'live', %s)
                ON CONFLICT (idempotency_key) WHERE flavour = 'keyed' DO NOTHING
                RETURNING id, reference, message, state::text
                """,
                (
                    f"echo_{uuid.uuid4().hex}",
                    flavour,
                    account,
                    body.reference,
                    key if flavour == "keyed" else None,
                    body.message,
                    datetime.now(UTC),
                ),
            )
            row = await cur.fetchone()
            status = 201
            if row is None:
                cur = await conn.execute(
                    """
                    SELECT id, reference, message, state::text FROM echo
                    WHERE flavour = 'keyed' AND idempotency_key = %s
                    """,
                    (key,),
                )
                row = await cur.fetchone()
                status = 200
            assert row is not None
            await conn.commit()

        if fault == "fail_500_after_work":
            return _error(500, "internal_error")
        if fault == "timeout_after_work":
            await hang()
        return JSONResponse(_echo_json(row), status_code=status)

    async def find(request: Request) -> Response:
        flavour = _flavour(request)
        if flavour is None:
            return _error(404, "unknown_flavour")
        reference = request.query_params.get("reference")
        if reference is None:
            return _error(400, "reference_required")
        async with await _connect(database_url) as conn:
            cur = await conn.execute(
                """
                SELECT id, reference, message, state::text FROM echo
                WHERE flavour = %s AND vendor_account = %s AND reference = %s
                ORDER BY created_at, id
                """,
                (flavour, request.path_params["account"], reference),
            )
            rows = await cur.fetchall()
        echoes: list[dict[str, object]] = []
        for row in rows:
            echoes.append(_echo_json(row))
        return JSONResponse({"echoes": echoes})

    async def cancel(request: Request) -> Response:
        flavour = _flavour(request)
        if flavour != "cancellable":
            return _error(404, "not_supported")
        async with await _connect(database_url) as conn:
            cur = await conn.execute(
                """
                UPDATE echo SET state = 'cancelled'
                WHERE flavour = 'cancellable' AND vendor_account = %s AND id = %s
                RETURNING id, reference, message, state::text
                """,
                (request.path_params["account"], request.path_params["echo_id"]),
            )
            row = await cur.fetchone()
            await conn.commit()
        if row is None:
            return _error(404, "unknown_echo")
        return JSONResponse(_echo_json(row))

    async def subscribe(request: Request) -> Response:
        flavour = _flavour(request)
        if flavour is None:
            return _error(404, "unknown_flavour")
        try:
            body = msgspec.json.decode(await request.body(), type=WebhookBody)
        except msgspec.DecodeError:
            return _error(400, "invalid_body")
        async with await _connect(database_url) as conn:
            await conn.execute(
                """
                INSERT INTO echo_webhook (flavour, vendor_account, url, secret, payload)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (flavour, vendor_account) DO UPDATE
                SET url = excluded.url, secret = excluded.secret,
                    payload = excluded.payload
                """,
                (
                    flavour,
                    request.path_params["account"],
                    body.url,
                    body.secret,
                    body.payload,
                ),
            )
            await conn.commit()
        return Response(status_code=204)

    async def _push(
        conn: FakeConnection,
        flavour: Flavour,
        account: str,
        event: _Event,
        how: PushHow,
    ) -> Literal["not_subscribed", "delivered", "failed"]:
        cur = await conn.execute(
            """
            SELECT url, secret, payload::text FROM echo_webhook
            WHERE flavour = %s AND vendor_account = %s
            """,
            (flavour, account),
        )
        row = await cur.fetchone()
        if row is None:
            return "not_subscribed"
        url, secret, payload = row
        assert isinstance(url, str) and isinstance(secret, str)
        if payload == "thin":
            body = msgspec.json.encode(
                _ThinWebhook(
                    id=event.id, echo_id=event.echo_id, reference=event.reference
                )
            )
        else:
            body = msgspec.json.encode(_FullWebhook(events=[event]))
        signature = sign(secret, body)
        if how == "bad_signature":
            signature = sign(secret + "-wrong", body)
        times = 2 if how == "twice" else 1
        delivered = True
        for _ in range(times):
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

    async def release(request: Request) -> Response:
        flavour = _flavour(request)
        if flavour is None:
            return _error(404, "unknown_flavour")
        account = request.path_params["account"]
        echo_id = request.path_params["echo_id"]
        raw = await request.body()
        try:
            body = msgspec.json.decode(raw or b"{}", type=ReleaseBody)
        except msgspec.DecodeError:
            return _error(400, "invalid_body")
        async with await _connect(database_url) as conn:
            cur = await conn.execute(
                """
                SELECT e.reference,
                       array(SELECT kind::text FROM echo_event WHERE echo_id = e.id)
                FROM echo e
                WHERE e.flavour = %s AND e.vendor_account = %s AND e.id = %s
                """,
                (flavour, account, echo_id),
            )
            row = await cur.fetchone()
            if row is None:
                return _error(404, "unknown_echo")
            reference, released = row
            assert isinstance(reference, str) and isinstance(released, list)
            kind: EventKind | None = body.kind
            if kind is None:
                kind = "confirmed" if "confirmed" not in released else "archived"
            if kind in released:
                return _error(409, "already_released")
            event = _Event(
                id=f"evt_{uuid.uuid4().hex}",
                echo_id=echo_id,
                reference=reference,
                kind=kind,
                occurred_at=datetime.now(UTC),
            )
            push = await _push(conn, flavour, account, event, body.push)
            await conn.execute(
                """
                INSERT INTO echo_event (id, echo_id, kind, occurred_at, push)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (event.id, echo_id, kind, event.occurred_at, push),
            )
            await conn.commit()
        return JSONResponse({"id": event.id, "kind": kind, "push": push})

    async def redeliver(request: Request) -> Response:
        flavour = _flavour(request)
        if flavour is None:
            return _error(404, "unknown_flavour")
        account = request.path_params["account"]
        async with await _connect(database_url) as conn:
            cur = await conn.execute(
                """
                SELECT v.id, v.echo_id, e.reference, v.kind::text, v.occurred_at
                FROM echo_event v JOIN echo e ON e.id = v.echo_id
                WHERE e.flavour = %s AND e.vendor_account = %s AND v.push = 'failed'
                ORDER BY v.seq
                """,
                (flavour, account),
            )
            rows = await cur.fetchall()
            delivered = 0
            for event_id, echo_id, reference, kind, occurred_at in rows:
                assert isinstance(event_id, str) and isinstance(echo_id, str)
                assert isinstance(reference, str) and isinstance(occurred_at, datetime)
                assert kind == "confirmed" or kind == "archived"
                event = _Event(event_id, echo_id, reference, kind, occurred_at)
                push = await _push(conn, flavour, account, event, "once")
                await conn.execute(
                    "UPDATE echo_event SET push = %s WHERE id = %s", (push, event_id)
                )
                if push == "delivered":
                    delivered += 1
            await conn.commit()
        return JSONResponse({"redelivered": delivered})

    async def events(request: Request) -> Response:
        """Every event released so far for the echoes with a reference, in
        release order: the poll page, and what a thin webhook points at.
        `refuse` does not apply to a read; the other faults do."""
        flavour = _flavour(request)
        if flavour is None:
            return _error(404, "unknown_flavour")
        reference = request.query_params.get("reference")
        if reference is None:
            return _error(400, "reference_required")
        async with await _connect(database_url) as conn:
            fault = await _take_fault(conn, flavour, "events")
            await conn.commit()
            if fault in ("fail_500", "fail_500_after_work"):
                return _error(500, "internal_error")
            cur = await conn.execute(
                """
                SELECT v.id, v.echo_id, e.reference, v.kind::text, v.occurred_at
                FROM echo_event v JOIN echo e ON e.id = v.echo_id
                WHERE e.flavour = %s AND e.vendor_account = %s AND e.reference = %s
                ORDER BY v.seq
                """,
                (flavour, request.path_params["account"], reference),
            )
            rows = await cur.fetchall()
        released: list[_Event] = []
        for event_id, echo_id, ref, kind, occurred_at in rows:
            assert isinstance(event_id, str) and isinstance(echo_id, str)
            assert isinstance(ref, str) and isinstance(occurred_at, datetime)
            assert kind == "confirmed" or kind == "archived"
            released.append(_Event(event_id, echo_id, ref, kind, occurred_at))
        if fault == "timeout_after_work":
            await hang()
        return Response(
            msgspec.json.encode(_FullWebhook(events=released)),
            media_type="application/json",
        )

    async def cut_report(request: Request) -> Response:
        """Cut a report: every event of the account released since its
        previous report, as CSV. Empty reports are cut too, as a vendor's
        daily file can be."""
        flavour = _flavour(request)
        if flavour is None:
            return _error(404, "unknown_flavour")
        account = request.path_params["account"]
        name = f"r{uuid.uuid7().hex}"
        async with await _connect(database_url) as conn:
            cur = await conn.execute(
                """
                SELECT v.id, v.echo_id, e.reference, v.kind::text, v.occurred_at
                FROM echo_event v JOIN echo e ON e.id = v.echo_id
                WHERE e.flavour = %s AND e.vendor_account = %s AND v.report IS NULL
                ORDER BY v.seq
                FOR UPDATE OF v
                """,
                (flavour, account),
            )
            rows = await cur.fetchall()
            lines = ["event_id,echo_id,reference,kind,occurred_at"]
            for event_id, echo_id, reference, kind, occurred_at in rows:
                assert isinstance(occurred_at, datetime)
                stamp = occurred_at.isoformat()
                lines.append(f"{event_id},{echo_id},{reference},{kind},{stamp}")
            await conn.execute(
                """
                INSERT INTO echo_report (name, flavour, vendor_account, body, cut_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (name, flavour, account, "\n".join(lines) + "\n", datetime.now(UTC)),
            )
            for row in rows:
                await conn.execute(
                    "UPDATE echo_event SET report = %s WHERE id = %s", (name, row[0])
                )
            await conn.commit()
        return JSONResponse({"name": name, "events": len(rows)})

    async def list_reports(request: Request) -> Response:
        flavour = _flavour(request)
        if flavour is None:
            return _error(404, "unknown_flavour")
        after = request.query_params.get("after", "")
        async with await _connect(database_url) as conn:
            fault = await _take_fault(conn, flavour, "events")
            await conn.commit()
            if fault in ("fail_500", "fail_500_after_work"):
                return _error(500, "internal_error")
            cur = await conn.execute(
                """
                SELECT name FROM echo_report
                WHERE flavour = %s AND vendor_account = %s AND name > %s
                ORDER BY name
                """,
                (flavour, request.path_params["account"], after),
            )
            rows = await cur.fetchall()
        if fault == "timeout_after_work":
            await hang()
        names: list[str] = []
        for (name,) in rows:
            assert isinstance(name, str)
            names.append(name)
        return JSONResponse({"reports": names})

    async def get_report(request: Request) -> Response:
        flavour = _flavour(request)
        if flavour is None:
            return _error(404, "unknown_flavour")
        async with await _connect(database_url) as conn:
            fault = await _take_fault(conn, flavour, "events")
            await conn.commit()
            if fault in ("fail_500", "fail_500_after_work"):
                return _error(500, "internal_error")
            cur = await conn.execute(
                """
                SELECT body FROM echo_report
                WHERE flavour = %s AND vendor_account = %s AND name = %s
                """,
                (flavour, request.path_params["account"], request.path_params["name"]),
            )
            row = await cur.fetchone()
        if row is None:
            return _error(404, "unknown_report")
        if fault == "timeout_after_work":
            await hang()
        body = row[0]
        assert isinstance(body, str)
        return Response(body, media_type="text/csv")

    async def script_fault(request: Request) -> Response:
        try:
            body = msgspec.json.decode(await request.body(), type=FaultBody)
        except msgspec.DecodeError:
            return _error(400, "invalid_body")
        async with await _connect(database_url) as conn:
            await conn.execute(
                "INSERT INTO echo_fault (flavour, fault, operation)"
                " VALUES (%s, %s, %s)",
                (body.flavour, body.fault, body.operation),
            )
            await conn.commit()
        return Response(status_code=204)

    account_path = "/_fake/echo/{flavour}/{account}"
    return Starlette(
        routes=[
            Route("/_fake/echo/faults", script_fault, methods=["POST"]),
            Route(f"{account_path}/echoes", send, methods=["POST"]),
            Route(f"{account_path}/echoes", find, methods=["GET"]),
            Route(
                f"{account_path}/echoes/{{echo_id}}/cancel", cancel, methods=["POST"]
            ),
            Route(f"{account_path}/webhooks", subscribe, methods=["POST"]),
            Route(
                f"{account_path}/echoes/{{echo_id}}/events/release",
                release,
                methods=["POST"],
            ),
            Route(f"{account_path}/events/redeliver", redeliver, methods=["POST"]),
            Route(f"{account_path}/events", events, methods=["GET"]),
            Route(f"{account_path}/reports/cut", cut_report, methods=["POST"]),
            Route(f"{account_path}/reports", list_reports, methods=["GET"]),
            Route(f"{account_path}/reports/{{name}}", get_report, methods=["GET"]),
        ]
    )

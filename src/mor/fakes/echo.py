"""The fake echo vendor: stores a message, returns an id (plan-messaging §10).

A made-up vendor API in three flavours, one per duplicate guard:

    POST /_fake/echo/{flavour}/echoes            {"reference", "message"}
         keyed: an Idempotency-Key it has seen returns the first echo, for good
    GET  /_fake/echo/{flavour}/echoes?reference=…   every echo for a reference
    POST /_fake/echo/{flavour}/echoes/{id}/cancel   cancellable only
    POST /_fake/echo/faults                      {"flavour", "fault"}: script
         the next send of a flavour to fail

Its state is its own (schema `fake_vendor`, its own connections), as a third
party's would be. Never imported by our code outside the fakes and tests.
Transitional with the echo flow: removed once the fake PSP exercises the
same machinery (slice 5), unless it serves something else by then.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Literal

import msgspec
import psycopg
from psycopg.rows import TupleRow
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route


type Flavour = Literal["keyed", "cancellable", "plain"]
type Fault = Literal["fail_500", "fail_500_after_work", "timeout_after_work", "refuse"]
FLAVOURS: tuple[Flavour, ...] = ("keyed", "cancellable", "plain")

# Longer than any client timeout: "did the work, then never answered".
HANG_SECONDS = 60


class SendBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    reference: str
    message: str


class FaultBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    flavour: Flavour
    fault: Fault


type FakeConnection = psycopg.AsyncConnection[TupleRow]


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


async def _take_fault(conn: FakeConnection, flavour: Flavour) -> Fault | None:
    cur = await conn.execute(
        """
        DELETE FROM echo_fault
        WHERE seq = (SELECT min(seq) FROM echo_fault WHERE flavour = %s)
        RETURNING fault::text
        """,
        (flavour,),
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


def make_app(database_url: str) -> Starlette:
    async def send(request: Request) -> Response:
        flavour = _flavour(request)
        if flavour is None:
            return _error(404, "unknown_flavour")
        try:
            body = msgspec.json.decode(await request.body(), type=SendBody)
        except msgspec.DecodeError:
            return _error(400, "invalid_body")
        key = request.headers.get("idempotency-key")

        async with await _connect(database_url) as conn:
            fault = await _take_fault(conn, flavour)
            if fault == "fail_500":
                await conn.commit()
                return _error(500, "internal_error")
            if fault == "refuse" and not await _known_key(conn, flavour, key):
                # A known key is replayed, never refused: that is the
                # keyed flavour's promise.
                await conn.commit()
                return _error(422, "message_rejected")

            # Insert first; for `keyed`, a conflict on the key means it was
            # seen before, and the first echo is the answer.
            cur = await conn.execute(
                """
                INSERT INTO echo
                  (id, flavour, reference, idempotency_key, message, state,
                   created_at)
                VALUES (%s, %s, %s, %s, %s, 'live', %s)
                ON CONFLICT (idempotency_key) WHERE flavour = 'keyed' DO NOTHING
                RETURNING id, reference, message, state::text
                """,
                (
                    f"echo_{uuid.uuid4().hex}",
                    flavour,
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
            await asyncio.sleep(HANG_SECONDS)
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
                WHERE flavour = %s AND reference = %s ORDER BY created_at, id
                """,
                (flavour, reference),
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
        echo_id = request.path_params["echo_id"]
        async with await _connect(database_url) as conn:
            cur = await conn.execute(
                """
                UPDATE echo SET state = 'cancelled'
                WHERE flavour = 'cancellable' AND id = %s
                RETURNING id, reference, message, state::text
                """,
                (echo_id,),
            )
            row = await cur.fetchone()
            await conn.commit()
        if row is None:
            return _error(404, "unknown_echo")
        return JSONResponse(_echo_json(row))

    async def script_fault(request: Request) -> Response:
        try:
            body = msgspec.json.decode(await request.body(), type=FaultBody)
        except msgspec.DecodeError:
            return _error(400, "invalid_body")
        async with await _connect(database_url) as conn:
            await conn.execute(
                "INSERT INTO echo_fault (flavour, fault) VALUES (%s, %s)",
                (body.flavour, body.fault),
            )
            await conn.commit()
        return Response(status_code=204)

    return Starlette(
        routes=[
            Route("/_fake/echo/faults", script_fault, methods=["POST"]),
            Route("/_fake/echo/{flavour}/echoes", send, methods=["POST"]),
            Route("/_fake/echo/{flavour}/echoes", find, methods=["GET"]),
            Route(
                "/_fake/echo/{flavour}/echoes/{echo_id}/cancel",
                cancel,
                methods=["POST"],
            ),
        ]
    )

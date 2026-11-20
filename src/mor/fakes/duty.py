"""The fake duty vendor: answers one item's duty and tax rates, the way a
landed-cost vendor would for a one-item cart (docs/plan-quote.md §3, 4b-2).

A made-up API, with vendor-side accounts as a path segment
(`A` = /_fake/duty/{account}):

    POST A/rates                {"destination", "origin", "hs_code"}
         200 {"hs_code_used", "duties": [{"formula", "note"?}],
              "taxes": [{"formula", "note"?}]}
         404 {"error": "unknown_code"}
    POST /_fake/duty/rates      {"destination", "hs_prefix", "duty", "tax"}:
         set the rates for a prefix (the longest prefix wins)
    POST /_fake/duty/faults     {"fault"}: script the next call to fail

Any code without a rate gets DEFAULT_DUTY and DEFAULT_TAX. Every call that
does work is logged in `duty_call`: what a vendor would bill. Its state is
its own (schema `fake_vendor`, its own connections), as a third party's
would be.
"""

from datetime import UTC, datetime
from typing import Literal

import msgspec
import psycopg
from psycopg.rows import TupleRow
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from mor.fakes.hang import Hang


type Fault = Literal[
    "fail_500",
    "timeout_after_work",
    "unknown_code",
    "undecodable",
    "specific_duty",
    "preference_note",
]
FAULTS: tuple[Fault, ...] = (
    "fail_500",
    "timeout_after_work",
    "unknown_code",
    "undecodable",
    "specific_duty",
    "preference_note",
)
DEFAULT_DUTY = "5%"
DEFAULT_TAX = "10%"


class AskBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    destination: str
    origin: str
    hs_code: str


class RateBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    destination: str
    hs_prefix: str
    duty: str
    tax: str


class FaultBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    fault: Fault


type FakeConnection = psycopg.AsyncConnection[TupleRow]


async def _connect(database_url: str) -> FakeConnection:
    return await psycopg.AsyncConnection.connect(
        database_url, options="-c search_path=fake_vendor"
    )


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


async def _take_fault(conn: FakeConnection) -> Fault | None:
    cur = await conn.execute(
        """
        DELETE FROM duty_fault WHERE seq = (SELECT min(seq) FROM duty_fault)
        RETURNING fault::text
        """
    )
    row = await cur.fetchone()
    if row is None:
        return None
    for fault in FAULTS:
        if fault == row[0]:
            return fault
    raise AssertionError(f"unknown fault {row[0]!r}")


async def _formulas(
    conn: FakeConnection, destination: str, hs_code: str
) -> tuple[str, str]:
    cur = await conn.execute(
        """
        SELECT duty_formula, tax_formula FROM duty_rate
        WHERE destination = %s AND starts_with(%s, hs_prefix)
        ORDER BY length(hs_prefix) DESC LIMIT 1
        """,
        (destination, hs_code),
    )
    row = await cur.fetchone()
    if row is None:
        return DEFAULT_DUTY, DEFAULT_TAX
    return row[0], row[1]


def make_app(database_url: str, hang: Hang) -> Starlette:
    async def ask(request: Request) -> Response:
        try:
            body = msgspec.json.decode(await request.body(), type=AskBody)
        except msgspec.DecodeError:
            return _error(400, "invalid_body")
        async with await _connect(database_url) as conn:
            fault = await _take_fault(conn)
            if fault == "fail_500":
                await conn.commit()
                return _error(500, "internal_error")
            await conn.execute(
                """
                INSERT INTO duty_call
                  (account, destination, origin, hs_code, called_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    request.path_params["account"],
                    body.destination,
                    body.origin,
                    body.hs_code,
                    datetime.now(UTC),
                ),
            )
            duty, tax = await _formulas(conn, body.destination, body.hs_code)
            await conn.commit()
        match fault:
            case "timeout_after_work":
                await hang()
            case "unknown_code":
                return _error(404, "unknown_code")
            case "undecodable":
                return Response(b"<html>maintenance</html>", status_code=200)
            case "specific_duty":
                duty = "2.00 USD/kg"
            case "preference_note" | None:
                pass
        duty_line: dict[str, str] = {"formula": duty}
        if fault == "preference_note":
            duty_line["note"] = "ATIGA preferential rate applied"
        return JSONResponse(
            {
                "hs_code_used": body.hs_code,
                "duties": [duty_line],
                "taxes": [{"formula": tax}],
            }
        )

    async def set_rate(request: Request) -> Response:
        try:
            body = msgspec.json.decode(await request.body(), type=RateBody)
        except msgspec.DecodeError:
            return _error(400, "invalid_body")
        async with await _connect(database_url) as conn:
            await conn.execute(
                """
                INSERT INTO duty_rate
                  (destination, hs_prefix, duty_formula, tax_formula)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (destination, hs_prefix)
                DO UPDATE SET duty_formula = excluded.duty_formula,
                              tax_formula = excluded.tax_formula
                """,
                (body.destination, body.hs_prefix, body.duty, body.tax),
            )
            await conn.commit()
        return Response(status_code=204)

    async def script_fault(request: Request) -> Response:
        try:
            body = msgspec.json.decode(await request.body(), type=FaultBody)
        except msgspec.DecodeError:
            return _error(400, "invalid_body")
        async with await _connect(database_url) as conn:
            await conn.execute(
                "INSERT INTO duty_fault (fault) VALUES (%s)", (body.fault,)
            )
            await conn.commit()
        return Response(status_code=204)

    return Starlette(
        routes=[
            Route("/_fake/duty/rates", set_rate, methods=["POST"]),
            Route("/_fake/duty/faults", script_fault, methods=["POST"]),
            Route("/_fake/duty/{account}/rates", ask, methods=["POST"]),
        ]
    )

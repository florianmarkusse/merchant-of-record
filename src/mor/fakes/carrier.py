"""The fake carrier: answers a live price for one parcel, the way a
carrier's rate call would (docs/plan-quote.md §4, 4c-2).

A made-up API, with carrier-side accounts as a path segment
(`A` = /_fake/carrier/{account}):

    POST A/rates                {"service", "origin", "destination",
                                 "weight_g", "length_mm", "width_mm",
                                 "height_mm"}
         200 {"currency", "total_minor", "valid_until",
              "parts": [{"kind", "name", "amount_minor"}]}
         404 {"error": "not_served", "detail"}
    POST /_fake/carrier/rates   {"service", "destination", "currency",
                                 "base_minor", "per_kg_minor", "fuel_bp",
                                 "surcharge_minor", "valid_until"}: set a price
    POST /_fake/carrier/faults  {"fault"}: script the next call to fail

The billed weight is the greater of the actual and the volumetric weight
(L x W x H / 5000 in cm³), rounded up to the next half kilo. Every call
that does work is logged in `carrier_call`. Its state is its own (schema
`fake_vendor`, its own connections), as a third party's would be.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import msgspec
import psycopg
from psycopg.rows import TupleRow
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from mor.db.connect import SESSION_OPTIONS
from mor.fakes.hang import Hang


type Fault = Literal[
    "fail_500",
    "timeout_after_work",
    "not_served",
    "undecodable",
    "parts_mismatch",
    "other_currency",
    "already_expired",
]
FAULTS: tuple[Fault, ...] = (
    "fail_500",
    "timeout_after_work",
    "not_served",
    "undecodable",
    "parts_mismatch",
    "other_currency",
    "already_expired",
)

VOLUMETRIC_DIVISOR = 5000  # cm³ per kg
LONG_PAST = datetime(2000, 1, 1, tzinfo=UTC)
VND_PER_US_CENT = 250  # `other_currency`: roughly 25,000 VND to the dollar


@dataclass(frozen=True, kw_only=True)
class FakeRate:
    currency: str
    base_minor: int
    per_kg_minor: int
    fuel_bp: int
    surcharge_minor: int
    valid_until: datetime | None


DEFAULT_RATE = FakeRate(
    currency="VND",
    base_minor=300_000,
    per_kg_minor=150_000,
    fuel_bp=3_000,
    surcharge_minor=20_000,
    valid_until=None,
)


class AskBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    service: str
    origin: str
    destination: str
    weight_g: int
    length_mm: int
    width_mm: int
    height_mm: int


class RateBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    service: str
    destination: str
    currency: str
    base_minor: int
    per_kg_minor: int
    fuel_bp: int
    surcharge_minor: int
    valid_until: datetime | None = None


class FaultBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    fault: Fault


type FakeConnection = psycopg.AsyncConnection[TupleRow]


async def _connect(database_url: str) -> FakeConnection:
    return await psycopg.AsyncConnection.connect(
        database_url, options=f"-c search_path=fake_vendor {SESSION_OPTIONS}"
    )


def _error(status: int, code: str, detail: str = "") -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail}, status_code=status)


async def _take_fault(conn: FakeConnection) -> Fault | None:
    cur = await conn.execute(
        """
        DELETE FROM carrier_fault WHERE seq = (SELECT min(seq) FROM carrier_fault)
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


async def _rate(conn: FakeConnection, service: str, destination: str) -> FakeRate:
    cur = await conn.execute(
        """
        SELECT currency, base_minor, per_kg_minor, fuel_bp, surcharge_minor,
               valid_until
        FROM carrier_rate WHERE service = %s AND destination = %s
        """,
        (service, destination),
    )
    row = await cur.fetchone()
    if row is None:
        return DEFAULT_RATE
    return FakeRate(
        currency=row[0],
        base_minor=row[1],
        per_kg_minor=row[2],
        fuel_bp=row[3],
        surcharge_minor=row[4],
        valid_until=row[5],
    )


def billed_half_kilos(body: AskBody) -> int:
    """The billed weight in half kilos, rounded up."""
    volumetric_g = -(
        -body.length_mm * body.width_mm * body.height_mm // VOLUMETRIC_DIVISOR
    )
    billed_g = max(body.weight_g, volumetric_g)
    return -(-billed_g // 500)


def _parts(rate: FakeRate, half_kilos: int) -> list[tuple[str, str, int]]:
    freight = rate.base_minor + rate.per_kg_minor * half_kilos // 2
    parts = [("freight", "freight", freight)]
    parts.append(("fuel", "fuel surcharge", freight * rate.fuel_bp // 10_000))
    if rate.surcharge_minor:
        parts.append(("surcharge", "demand surcharge", rate.surcharge_minor))
    return parts


async def handle_ask(
    request: Request, database_url: str, clock: Callable[[], datetime], hang: Hang
) -> Response:
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
            INSERT INTO carrier_call
              (account, service, origin, destination, weight_g, length_mm,
               width_mm, height_mm, called_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                request.path_params["account"],
                body.service,
                body.origin,
                body.destination,
                body.weight_g,
                body.length_mm,
                body.width_mm,
                body.height_mm,
                clock(),
            ),
        )
        rate = await _rate(conn, body.service, body.destination)
        await conn.commit()
    currency = rate.currency
    parts = _parts(rate, billed_half_kilos(body))
    valid_until = rate.valid_until
    match fault:
        case "timeout_after_work":
            await hang()
        case "not_served":
            return _error(404, "not_served", f"no {body.service} to here")
        case "undecodable":
            return Response(b"<html>maintenance</html>", status_code=200)
        case "other_currency":
            currency = "USD"
            converted: list[tuple[str, str, int]] = []
            for kind, name, amount in parts:
                converted.append((kind, name, amount // VND_PER_US_CENT))
            parts = converted
        case "already_expired":
            valid_until = LONG_PAST
        case "parts_mismatch" | None:
            pass
    total = 0
    for _kind, _name, amount in parts:
        total += amount
    if fault == "parts_mismatch":
        total += 1
    rendered: list[dict[str, str | int]] = []
    for kind, name, amount in parts:
        rendered.append({"kind": kind, "name": name, "amount_minor": amount})
    return JSONResponse(
        {
            "currency": currency,
            "total_minor": total,
            "parts": rendered,
            "valid_until": None if valid_until is None else valid_until.isoformat(),
        }
    )


async def handle_set_rate(request: Request, database_url: str) -> Response:
    try:
        body = msgspec.json.decode(await request.body(), type=RateBody)
    except msgspec.DecodeError:
        return _error(400, "invalid_body")
    async with await _connect(database_url) as conn:
        await conn.execute(
            """
            INSERT INTO carrier_rate
              (service, destination, currency, base_minor, per_kg_minor,
               fuel_bp, surcharge_minor, valid_until)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (service, destination)
            DO UPDATE SET currency = excluded.currency,
                          base_minor = excluded.base_minor,
                          per_kg_minor = excluded.per_kg_minor,
                          fuel_bp = excluded.fuel_bp,
                          surcharge_minor = excluded.surcharge_minor,
                          valid_until = excluded.valid_until
            """,
            (
                body.service,
                body.destination,
                body.currency,
                body.base_minor,
                body.per_kg_minor,
                body.fuel_bp,
                body.surcharge_minor,
                body.valid_until,
            ),
        )
        await conn.commit()
    return Response(status_code=204)


async def handle_script_fault(request: Request, database_url: str) -> Response:
    try:
        body = msgspec.json.decode(await request.body(), type=FaultBody)
    except msgspec.DecodeError:
        return _error(400, "invalid_body")
    async with await _connect(database_url) as conn:
        await conn.execute(
            "INSERT INTO carrier_fault (fault) VALUES (%s)", (body.fault,)
        )
        await conn.commit()
    return Response(status_code=204)


def make_app(database_url: str, clock: Callable[[], datetime], hang: Hang) -> Starlette:
    """`clock` is the vendor's own time, shared with tests as the other
    fakes'."""

    async def ask(request: Request) -> Response:
        return await handle_ask(request, database_url, clock, hang)

    async def set_rate(request: Request) -> Response:
        return await handle_set_rate(request, database_url)

    async def script_fault(request: Request) -> Response:
        return await handle_script_fault(request, database_url)

    return Starlette(
        routes=[
            Route("/_fake/carrier/rates", set_rate, methods=["POST"]),
            Route("/_fake/carrier/faults", script_fault, methods=["POST"]),
            Route("/_fake/carrier/{account}/rates", ask, methods=["POST"]),
        ]
    )

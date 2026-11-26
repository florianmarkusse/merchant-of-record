"""The fake carrier: answers a live price for one parcel and books
labels, the way a carrier's rate and shipment calls would
(docs/plan-quote.md §4, 4c-2; docs/plan-shipments.md §3).

A made-up API in three flavours, one per duplicate guard, with
carrier-side accounts as a path segment
(`A` = /_fake/carrier/{flavour}/{account}):

    POST A/rates                {"service", "origin", "destination",
                                 "weight_g", "length_mm", "width_mm",
                                 "height_mm"}
         200 {"currency", "total_minor", "valid_until",
              "parts": [{"kind", "name", "amount_minor"}]}
         404 {"error": "not_served", "detail"}
    POST A/shipments            book a label: {"reference", "service",
                                 "shipper", "receiver", "parcel",
                                 "incoterm", "items"}, Idempotency-Key
         201 {"tracking_number", "export_declaration_no", "label_pdf",
              "commercial_invoice_pdf"} (the PDFs in base64)
         422 {"error": "address_refused" | "customs_refused", "detail"}
    GET  A/shipments?reference  every label for our reference, oldest
                                 first, each with its "state"
    POST A/shipments/{tn}/cancel   cancellable only
    POST /_fake/carrier/rates   {"service", "destination", "currency",
                                 "base_minor", "per_kg_minor", "fuel_bp",
                                 "surcharge_minor", "valid_until"}: set a price
    POST /_fake/carrier/faults  {"fault", "operation"}: script the next
                                 call of an operation to fail

Flavours: `keyed` books once per Idempotency-Key, for good, and answers a
repeat with the first label; `cancellable` books every request and lets a
label be cancelled; `plain` books every request and cancels nothing.

The scenario comes from values in the request: a receiver whose first
address line starts with `REFUSE` is a refused address, an item whose
description starts with `REFUSE` refused customs data.

The billed weight is the greater of the actual and the volumetric weight
(L x W x H / 5000 in cm³), rounded up to the next half kilo. Every rate
call that does work is logged in `carrier_call`, every label in
`carrier_shipment`: the count per reference is the fuzzer's proof that
nothing was booked twice. Its state is its own (schema `fake_vendor`, its
own connections), as a third party's would be. Its PDFs are one-page
WeasyPrint renders of its own markup.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import htpy
import msgspec
import psycopg
from psycopg.rows import TupleRow
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from weasyprint import HTML
from weasyprint.urls import URLFetcher

from mor.db.connect import SESSION_OPTIONS
from mor.db.fetch import one_value
from mor.fakes.hang import Hang


type Flavour = Literal["keyed", "cancellable", "plain"]
FLAVOURS: tuple[Flavour, ...] = ("keyed", "cancellable", "plain")
type Operation = Literal["rate", "book", "find", "cancel"]
type Fault = Literal[
    "fail_500",
    "fail_500_after_work",
    "timeout_after_work",
    "not_served",
    "undecodable",
    "parts_mismatch",
    "other_currency",
    "already_expired",
]
FAULTS: tuple[Fault, ...] = (
    "fail_500",
    "fail_500_after_work",
    "timeout_after_work",
    "not_served",
    "undecodable",
    "parts_mismatch",
    "other_currency",
    "already_expired",
)
# What each operation can be scripted to do; a rate call's answer can
# also be wrong in the ways its client must refuse.
OPERATION_FAULTS: dict[Operation, tuple[Fault, ...]] = {
    "rate": (
        "fail_500",
        "timeout_after_work",
        "not_served",
        "undecodable",
        "parts_mismatch",
        "other_currency",
        "already_expired",
    ),
    "book": ("fail_500", "fail_500_after_work", "timeout_after_work"),
    "find": ("fail_500", "timeout_after_work"),
    "cancel": ("fail_500", "fail_500_after_work", "timeout_after_work"),
}
REFUSE = "REFUSE"

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
    operation: Operation = "rate"


class PartyBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    name: str
    email: str
    phone: str
    line1: str
    line2: str | None
    city: str
    region: str | None
    postcode: str | None
    country: str


class ParcelBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    weight_g: int
    length_mm: int
    width_mm: int
    height_mm: int


class ItemBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    description: str
    hs_code: str
    origin: str
    quantity: int
    value_minor: int
    currency: str


class BookBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    reference: str
    service: str
    shipper: PartyBody
    receiver: PartyBody
    parcel: ParcelBody
    incoterm: Literal["ddp", "dap"]
    items: list[ItemBody]


class LabelAnswer(msgspec.Struct, frozen=True, kw_only=True):
    tracking_number: str
    export_declaration_no: str
    label_pdf: bytes
    commercial_invoice_pdf: bytes


class FoundAnswer(msgspec.Struct, frozen=True, kw_only=True):
    tracking_number: str
    export_declaration_no: str
    state: Literal["live", "cancelled"]
    label_pdf: bytes
    commercial_invoice_pdf: bytes


class FindAnswer(msgspec.Struct, frozen=True):
    shipments: list[FoundAnswer]


type FakeConnection = psycopg.AsyncConnection[TupleRow]


async def _connect(database_url: str) -> FakeConnection:
    return await psycopg.AsyncConnection.connect(
        database_url, options=f"-c search_path=fake_vendor {SESSION_OPTIONS}"
    )


def _error(status: int, code: str, detail: str = "") -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail}, status_code=status)


def _flavour(request: Request) -> Flavour | None:
    raw = request.path_params["flavour"]
    for flavour in FLAVOURS:
        if raw == flavour:
            return flavour
    return None


async def _take_fault(conn: FakeConnection, operation: Operation) -> Fault | None:
    cur = await conn.execute(
        """
        DELETE FROM carrier_fault
        WHERE seq = (SELECT min(seq) FROM carrier_fault WHERE operation = %s)
        RETURNING fault::text
        """,
        (operation,),
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
    if _flavour(request) is None:
        return _error(404, "unknown_flavour")
    try:
        body = msgspec.json.decode(await request.body(), type=AskBody)
    except msgspec.DecodeError:
        return _error(400, "invalid_body")
    async with await _connect(database_url) as conn:
        fault = await _take_fault(conn, "rate")
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
        case "parts_mismatch" | "fail_500_after_work" | None:
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
    if body.fault not in OPERATION_FAULTS[body.operation]:
        return _error(400, "fault_not_for_operation")
    async with await _connect(database_url) as conn:
        await conn.execute(
            "INSERT INTO carrier_fault (fault, operation) VALUES (%s, %s)",
            (body.fault, body.operation),
        )
        await conn.commit()
    return Response(status_code=204)


# --- booking --------------------------------------------------------------


_NO_FETCHING = URLFetcher(allowed_protocols=())


def _render(page: htpy.Element) -> bytes:
    """One page as a PDF. Nothing in it may make WeasyPrint fetch a URL or
    read a file."""
    return HTML(string=str(page), url_fetcher=_NO_FETCHING).write_pdf(stylesheets=[])


def _party(title: str, party: PartyBody) -> htpy.Element:
    lines: list[htpy.Node] = [htpy.strong[title], htpy.br, party.name, htpy.br]
    lines.extend([party.line1, htpy.br])
    if party.line2:
        lines.extend([party.line2, htpy.br])
    lines.extend([party.postcode or "", " ", party.city, htpy.br])
    lines.extend([party.region or "", " ", party.country])
    return htpy.p[lines]


def label_page(body: BookBody, tracking_number: str) -> htpy.Element:
    return htpy.html[
        htpy.body[
            htpy.h1["FAKE CARRIER"],
            htpy.p[f"{body.service} · {body.incoterm.upper()}"],
            htpy.h2[tracking_number],
            _party("From", body.shipper),
            _party("To", body.receiver),
            htpy.p[f"Ref {body.reference} · {body.parcel.weight_g} g"],
        ]
    ]


def invoice_page(body: BookBody, export_declaration_no: str) -> htpy.Element:
    rows: list[htpy.Node] = []
    for item in body.items:
        rows.append(
            htpy.tr[
                htpy.td[item.description],
                htpy.td[item.hs_code],
                htpy.td[item.origin],
                htpy.td[str(item.quantity)],
                htpy.td[f"{item.value_minor} {item.currency}"],
            ]
        )
    return htpy.html[
        htpy.body[
            htpy.h1["Commercial invoice"],
            htpy.p[f"Export declaration {export_declaration_no}"],
            _party("Exporter", body.shipper),
            _party("Consignee", body.receiver),
            htpy.table[rows],
        ]
    ]


async def _new_id(conn: FakeConnection, prefix: str) -> str:
    """The fake's next number (`FK000000000042`): from a sequence, never at
    random, so a fuzzer's replay meets the same numbers in the same
    order."""
    number = await one_value(conn, "SELECT nextval('fake_id')", None, int)
    return f"{prefix}{number:012d}"


def _refusal(body: BookBody) -> JSONResponse | None:
    if body.receiver.line1.startswith(REFUSE):
        return _error(422, "address_refused", "the receiver's address is unknown")
    for item in body.items:
        if item.description.startswith(REFUSE):
            return _error(422, "customs_refused", f"{item.description!r} is vague")
    return None


def _label_json(answer: LabelAnswer, status: int) -> Response:
    return Response(
        msgspec.json.encode(answer), status_code=status, media_type="application/json"
    )


async def _keyed_label(
    conn: FakeConnection, account: str, key: str
) -> LabelAnswer | None:
    cur = await conn.execute(
        """
        SELECT tracking_number, export_declaration_no, label, commercial_invoice
        FROM carrier_shipment
        WHERE flavour = 'keyed' AND account = %s AND idempotency_key = %s
        """,
        (account, key),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return LabelAnswer(
        tracking_number=row[0],
        export_declaration_no=row[1],
        label_pdf=bytes(row[2]),
        commercial_invoice_pdf=bytes(row[3]),
    )


async def handle_book(
    request: Request, database_url: str, clock: Callable[[], datetime], hang: Hang
) -> Response:
    flavour = _flavour(request)
    if flavour is None:
        return _error(404, "unknown_flavour")
    account = request.path_params["account"]
    try:
        body = msgspec.json.decode(await request.body(), type=BookBody)
    except msgspec.DecodeError:
        return _error(400, "invalid_body")
    key = request.headers.get("idempotency-key")
    if flavour == "keyed" and not key:
        return _error(400, "idempotency_key_required")
    async with await _connect(database_url) as conn:
        fault = await _take_fault(conn, "book")
        if fault == "fail_500":
            await conn.commit()
            return _error(500, "internal_error")
        if flavour == "keyed" and key is not None:
            earlier = await _keyed_label(conn, account, key)
            if earlier is not None:
                await conn.commit()
                return _label_json(earlier, 201)
        refused = _refusal(body)
        if refused is not None:
            await conn.commit()
            return refused
        tracking_number = await _new_id(conn, "FK")
        export_declaration_no = await _new_id(conn, "EX")
        label_pdf = await asyncio.to_thread(_render, label_page(body, tracking_number))
        invoice_pdf = await asyncio.to_thread(
            _render, invoice_page(body, export_declaration_no)
        )
        cur = await conn.execute(
            """
            INSERT INTO carrier_shipment
              (tracking_number, flavour, account, reference, idempotency_key,
               service, origin, destination, export_declaration_no, state,
               label, commercial_invoice, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'live', %s, %s, %s)
            ON CONFLICT (account, idempotency_key) WHERE flavour = 'keyed'
            DO NOTHING
            RETURNING tracking_number
            """,
            (
                tracking_number,
                flavour,
                account,
                body.reference,
                key if flavour == "keyed" else None,
                body.service,
                body.shipper.country,
                body.receiver.country,
                export_declaration_no,
                label_pdf,
                invoice_pdf,
                clock(),
            ),
        )
        answer = LabelAnswer(
            tracking_number=tracking_number,
            export_declaration_no=export_declaration_no,
            label_pdf=label_pdf,
            commercial_invoice_pdf=invoice_pdf,
        )
        if await cur.fetchone() is None:
            # The same key booked meanwhile: its label is the answer.
            assert key is not None
            earlier = await _keyed_label(conn, account, key)
            assert earlier is not None
            answer = earlier
        await conn.commit()
    match fault:
        case "fail_500_after_work":
            return _error(500, "internal_error")
        case "timeout_after_work":
            await hang()
        case (
            "fail_500"
            | "not_served"
            | "undecodable"
            | "parts_mismatch"
            | "other_currency"
            | "already_expired"
            | None
        ):
            pass
    return _label_json(answer, 201)


async def handle_find(request: Request, database_url: str, hang: Hang) -> Response:
    flavour = _flavour(request)
    if flavour is None:
        return _error(404, "unknown_flavour")
    reference = request.query_params.get("reference")
    if not reference:
        return _error(400, "reference_required")
    async with await _connect(database_url) as conn:
        fault = await _take_fault(conn, "find")
        await conn.commit()
        if fault == "fail_500":
            return _error(500, "internal_error")
        cur = await conn.execute(
            """
            SELECT tracking_number, export_declaration_no, state::text, label,
                   commercial_invoice
            FROM carrier_shipment
            WHERE flavour = %s AND account = %s AND reference = %s
            ORDER BY created_at, tracking_number
            """,
            (flavour, request.path_params["account"], reference),
        )
        rows = await cur.fetchall()
    if fault == "timeout_after_work":
        await hang()
    found: list[FoundAnswer] = []
    for row in rows:
        found.append(
            FoundAnswer(
                tracking_number=row[0],
                export_declaration_no=row[1],
                state="cancelled" if row[2] == "cancelled" else "live",
                label_pdf=bytes(row[3]),
                commercial_invoice_pdf=bytes(row[4]),
            )
        )
    return Response(
        msgspec.json.encode(FindAnswer(found)), media_type="application/json"
    )


async def handle_cancel(request: Request, database_url: str, hang: Hang) -> Response:
    flavour = _flavour(request)
    if flavour is None:
        return _error(404, "unknown_flavour")
    if flavour != "cancellable":
        return _error(405, "not_cancellable")
    async with await _connect(database_url) as conn:
        fault = await _take_fault(conn, "cancel")
        if fault == "fail_500":
            await conn.commit()
            return _error(500, "internal_error")
        cur = await conn.execute(
            """
            UPDATE carrier_shipment SET state = 'cancelled'
            WHERE flavour = 'cancellable' AND account = %s AND tracking_number = %s
            RETURNING tracking_number
            """,
            (request.path_params["account"], request.path_params["tracking_number"]),
        )
        cancelled = await cur.fetchone()
        await conn.commit()
    if cancelled is None:
        return _error(404, "not_found")
    match fault:
        case "fail_500_after_work":
            return _error(500, "internal_error")
        case "timeout_after_work":
            await hang()
        case (
            "fail_500"
            | "not_served"
            | "undecodable"
            | "parts_mismatch"
            | "other_currency"
            | "already_expired"
            | None
        ):
            pass
    return JSONResponse({"state": "cancelled"})


def make_app(database_url: str, clock: Callable[[], datetime], hang: Hang) -> Starlette:
    """`clock` is the vendor's own time, shared with tests as the other
    fakes'."""

    async def ask(request: Request) -> Response:
        return await handle_ask(request, database_url, clock, hang)

    async def set_rate(request: Request) -> Response:
        return await handle_set_rate(request, database_url)

    async def script_fault(request: Request) -> Response:
        return await handle_script_fault(request, database_url)

    async def book(request: Request) -> Response:
        return await handle_book(request, database_url, clock, hang)

    async def find(request: Request) -> Response:
        return await handle_find(request, database_url, hang)

    async def cancel(request: Request) -> Response:
        return await handle_cancel(request, database_url, hang)

    account = "/_fake/carrier/{flavour}/{account}"
    return Starlette(
        routes=[
            Route("/_fake/carrier/rates", set_rate, methods=["POST"]),
            Route("/_fake/carrier/faults", script_fault, methods=["POST"]),
            Route(f"{account}/rates", ask, methods=["POST"]),
            Route(f"{account}/shipments", book, methods=["POST"]),
            Route(f"{account}/shipments", find, methods=["GET"]),
            Route(
                f"{account}/shipments/{{tracking_number}}/cancel",
                cancel,
                methods=["POST"],
            ),
        ]
    )

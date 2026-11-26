"""The staff Shipments page (docs/plan-shipments.md §6): every paid
order's shipment, those waiting on a person first. A shipment whose label
failed, or whose booking was lost on a carrier that promises nothing
about a resend, can be booked again: the next request to the carrier.

    GET  /staff/shipments                      the list
    POST /staff/shipments/{shipment_id}/book   book again

Correcting the merchant's data waits for the merchant surfaces; until then
it is fixed with the carrier and the merchant by hand, then booked again.
Mounted outside prod only until staff sign-in exists, as the other staff
pages; until then the actor is this page, not a person.
"""

from datetime import datetime
from urllib.parse import urlencode

import htpy
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from mor.audit import Actor, ActorKind
from mor.dates import format_moment
from mor.db.connect import connect
from mor.public_ids import order_public_id, parse_shipment_id, shipment_public_id
from mor.shipments.flow import (
    BookingRetried,
    NotRetryable,
    NotRetryableReason,
    book_again,
)
from mor.shipments.records import BookingState
from mor.shipments.store import ShipmentSummary, recent_shipments
from mor.web.forms import same_origin
from mor.web.staff_layout import staff_page


PAGE = "/staff/shipments"
PERSON = Actor(ActorKind.STAFF, "staff-shipments-page")
SHOWN = 100


async def page(request: Request, database_url: str) -> Response:
    async with await connect(database_url) as conn:
        shipments = await recent_shipments(conn, SHOWN)
    note = request.query_params.get("note")
    return HTMLResponse(render(shipments, note))


def _back(note: str) -> Response:
    return RedirectResponse(f"{PAGE}?{urlencode({'note': note})}", status_code=303)


async def book(request: Request, database_url: str, now: datetime) -> Response:
    if not same_origin(request):
        return Response("cross-site POST refused", status_code=403)
    shipment_id = parse_shipment_id(request.path_params["shipment_id"])
    if shipment_id is None:
        return Response("no such shipment", status_code=404)
    async with await connect(database_url) as conn:
        retried = await book_again(conn, shipment_id, PERSON, now)
        match retried:
            case BookingRetried():
                await conn.commit()
                return _back(f"{shipment_public_id(shipment_id)} is booked again")
            case NotRetryable(reason=reason):
                return _back(f"not booked again: {_not_retryable(reason)}")


def _not_retryable(reason: NotRetryableReason) -> str:
    match reason:
        case "not_stuck":
            return "it is booking or booked already"
        case "ship_from_elsewhere":
            return (
                "its ship-from is not where its service leaves from; book it"
                " with the carrier by hand"
            )
        case "no_carrier_account":
            return "its carrier has no account here yet"


def _action(shipment: ShipmentSummary) -> htpy.Node:
    match shipment.booking_state:
        case BookingState.LABEL_FAILED | BookingState.NEEDS_PERSON:
            return htpy.form(
                method="post",
                action=f"{PAGE}/{shipment_public_id(shipment.id)}/book",
            )[htpy.button(type="submit")["Book again"]]
        case BookingState.BOOKING | BookingState.DEDUPLICATING | BookingState.LABELLED:
            return None


def _why(shipment: ShipmentSummary) -> str:
    parts: list[str] = []
    if shipment.failure is not None:
        parts.append(shipment.failure.value)
    if shipment.detail is not None:
        parts.append(shipment.detail)
    return ": ".join(parts)


def _table(shipments: list[ShipmentSummary]) -> htpy.Node:
    if not shipments:
        return htpy.p(class_="muted")["No shipments yet."]
    rows: list[htpy.Node] = []
    for shipment in shipments:
        rows.append(
            htpy.tr[
                htpy.td[shipment_public_id(shipment.id)],
                htpy.td[order_public_id(shipment.order_id)],
                htpy.td[shipment.merchant_name],
                htpy.td[f"{shipment.carrier} {shipment.service}"],
                htpy.td[shipment.incoterm.value.upper()],
                htpy.td[shipment.booking_state.value],
                htpy.td[_why(shipment)],
                htpy.td[shipment.tracking_number or ""],
                htpy.td[shipment.tracking_status.value],
                htpy.td(class_="num")[str(shipment.book_requests)],
                htpy.td[format_moment(shipment.updated_at)],
                htpy.td[_action(shipment)],
            ]
        )
    return htpy.div(class_="scroll")[
        htpy.table(class_="data")[
            htpy.thead[
                htpy.tr[
                    htpy.th["Shipment"],
                    htpy.th["Order"],
                    htpy.th["Merchant"],
                    htpy.th["Service"],
                    htpy.th["Incoterm"],
                    htpy.th["Booking"],
                    htpy.th["Why"],
                    htpy.th["Tracking number"],
                    htpy.th["Tracking"],
                    htpy.th["Book requests"],
                    htpy.th["Changed"],
                    htpy.th[""],
                ]
            ],
            htpy.tbody[rows],
        ]
    ]


def render(shipments: list[ShipmentSummary], note: str | None) -> str:
    return staff_page(
        "Shipments",
        PAGE,
        ("table_filter.js",),
        htpy.h1["Shipments"],
        htpy.p(role="status")[htpy.strong[note]] if note else None,
        htpy.p(class_="muted")[
            "Each paid order's shipment, those waiting on a person first, then"
            f" the newest {SHOWN}. Book again sends the next request to the"
            " carrier. A shipment that needs a person lost its answer on a"
            " carrier that keeps no keys: check with the carrier first, as it"
            " may hold a label already."
        ],
        _table(shipments),
    )

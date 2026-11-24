"""The staff Settlement pages (docs/plan-payments.md §7,
docs/plan-payments.md §7): per PSP account its health
figures (`psp_clearing` and `payout_in_transit` per currency), its payouts
with whether each adds up to its lines yet (open ones first, with how long
they have been open), and its exceptions, each open one with a form to
resolve it with a note (question 10 of 20-Nov-2026). A note books
nothing.

    GET  /staff/settlement                                   every account
    GET  /staff/settlement/{account}                         one account
    POST /staff/settlement/{account}/exceptions/{exception}  resolve one

Mounted outside prod only until staff sign-in exists, as the other staff
pages; until then the actor is this page, not a person.
"""

import uuid
from datetime import datetime, timedelta
from urllib.parse import urlencode

import htpy
import msgspec
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from mor.audit import Actor, ActorKind
from mor.dates import format_moment
from mor.db.connect import connect
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.settlement.records import Reconciliation
from mor.settlement.review import (
    AccountSummary,
    AlreadyResolved,
    EmptyNote,
    ExceptionRow,
    NoSuchException,
    PayoutRow,
    Resolved,
    account_summaries,
    account_summary,
    exceptions_of,
    payouts_of,
    resolve_exception,
)
from mor.web.forms import read_form, same_origin
from mor.web.staff_layout import money_text, staff_page


PAGE = "/staff/settlement"
PERSON = Actor(ActorKind.STAFF, "staff-settlement-page")
PAYOUTS_SHOWN = 50


class ResolveForm(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    note: str


def _money(amount_minor: int, currency: Currency) -> str:
    return money_text(Money(amount_minor, currency))


def _balances(summary: AccountSummary) -> htpy.Node:
    if not summary.balances:
        return htpy.span(class_="muted")["nothing booked"]
    parts: list[htpy.Node] = []
    for balance in summary.balances:
        parts.append(
            htpy.div[
                f"clearing {_money(balance.clearing_minor, balance.currency)}, "
                f"in transit {_money(balance.in_transit_minor, balance.currency)}"
            ]
        )
    return parts


def render_accounts(summaries: list[AccountSummary]) -> str:
    rows: list[htpy.Node] = []
    for summary in summaries:
        rows.append(
            htpy.tr[
                htpy.td[htpy.a(href=f"{PAGE}/{summary.id}")[summary.name]],
                htpy.td[summary.settlement_mode],
                htpy.td[summary.last_read or "—"],
                htpy.td[_balances(summary)],
                htpy.td(class_="num")[str(summary.open_exceptions)],
                htpy.td(class_="num")[str(summary.open_payouts)],
            ]
        )
    table: htpy.Node = htpy.p(class_="muted")["No PSP accounts yet."]
    if rows:
        table = htpy.div(class_="scroll")[
            htpy.table(class_="data")[
                htpy.thead[
                    htpy.tr[
                        htpy.th["Account"],
                        htpy.th["Settles by"],
                        htpy.th["Last read"],
                        htpy.th["Balances"],
                        htpy.th["Open exceptions"],
                        htpy.th["Payouts not adding up"],
                    ]
                ],
                htpy.tbody[rows],
            ]
        ]
    return staff_page(
        "Settlement",
        PAGE,
        ("table_filter.js",),
        htpy.h1["Settlement"],
        htpy.p(class_="muted")[
            "Per PSP account: psp_clearing is what the PSP holds for us by our"
            " books, zero once everything captured is settled and paid out; in"
            " transit is what the PSP says it paid out and our bank has not"
            " shown yet."
        ],
        table,
    )


def _difference(payout: PayoutRow) -> str:
    """The net less its lines so far: in whole minor units when it is one,
    else exactly (a PSP that counts finer than we do)."""
    difference = payout.difference
    if difference.denominator == 1:
        return _money(difference.numerator, payout.currency)
    return f"{difference} {payout.currency} minor"


def _payout_rows(payouts: list[PayoutRow], now: datetime) -> htpy.Node:
    if not payouts:
        return htpy.p(class_="muted")["No payouts read yet."]
    rows: list[htpy.Node] = []
    for payout in payouts:
        booked = "—" if payout.booked_at is None else format_moment(payout.booked_at)
        match payout.reconciliation:
            case Reconciliation.BALANCED:
                state: htpy.Node = "adds up"
            case Reconciliation.OPEN:
                state = htpy.strong[
                    f"open {_age(now - payout.recorded_at)}, off by"
                    f" {_difference(payout)}"
                ]
        rows.append(
            htpy.tr[
                htpy.td[payout.vendor_payout_id],
                htpy.td(class_="num")[_money(payout.net_minor, payout.currency)],
                htpy.td[format_moment(payout.paid_out_at)],
                htpy.td(class_="num")[f"{payout.booked_lines} of {payout.lines}"],
                htpy.td[state],
                htpy.td[booked],
            ]
        )
    return htpy.div(class_="scroll")[
        htpy.table(class_="data")[
            htpy.thead[
                htpy.tr[
                    htpy.th["Payout"],
                    htpy.th["Net paid out"],
                    htpy.th["Paid out"],
                    htpy.th["Lines booked"],
                    htpy.th["Lines add up"],
                    htpy.th["Payout booked"],
                ]
            ],
            htpy.tbody[rows],
        ]
    ]


def _age(age: timedelta) -> str:
    """How long, in the largest whole unit."""
    if age >= timedelta(days=1):
        return f"{age.days} d"
    if age >= timedelta(hours=1):
        return f"{age.seconds // 3600} h"
    return f"{age.seconds // 60} min"


def _exception_rows(account_id: uuid.UUID, found: list[ExceptionRow]) -> htpy.Node:
    if not found:
        return htpy.p(class_="muted")["No exceptions."]
    rows: list[htpy.Node] = []
    for row in found:
        outcome: htpy.Node
        if row.note is None:
            outcome = htpy.form(
                method="post", action=f"{PAGE}/{account_id}/exceptions/{row.id}"
            )[
                htpy.label[
                    "What was found ",
                    htpy.input(type="text", name="note", required=True),
                ],
                " ",
                htpy.button(type="submit")["Resolve"],
            ]
        else:
            when = "" if row.resolved_at is None else format_moment(row.resolved_at)
            outcome = htpy.span[f"{row.note} ({row.resolved_by}, {when})"]
        rows.append(
            htpy.tr[
                htpy.td[row.kind],
                htpy.td[row.vendor_ref],
                htpy.td[row.detail],
                htpy.td[format_moment(row.created_at)],
                htpy.td[outcome],
            ]
        )
    return htpy.div(class_="scroll")[
        htpy.table(class_="data")[
            htpy.thead[
                htpy.tr[
                    htpy.th["Kind"],
                    htpy.th["PSP's id"],
                    htpy.th["Detail"],
                    htpy.th["Found"],
                    htpy.th["Resolved"],
                ]
            ],
            htpy.tbody[rows],
        ]
    ]


def render_account(
    summary: AccountSummary,
    payouts: list[PayoutRow],
    found: list[ExceptionRow],
    note: str | None,
    now: datetime,
) -> str:
    return staff_page(
        summary.name,
        PAGE,
        ("table_filter.js",),
        htpy.p[htpy.a(href=PAGE)["All accounts"]],
        htpy.h1[f"Settlement: {summary.name}"],
        htpy.p(role="status")[htpy.strong[note]] if note else None,
        htpy.div(class_="balances")[_balances(summary)],
        htpy.p(class_="muted")[
            f"Settles by {summary.settlement_mode}; last read "
            f"{summary.last_read or 'nothing yet'}. Resolving an exception"
            " records what was found and books nothing: a correction is a"
            " journal entry made by hand. A payout is open until the lines"
            " that have arrived add up to its net; lines may still be on"
            " their way."
        ],
        htpy.h2["Exceptions"],
        _exception_rows(summary.id, found),
        htpy.h2["Payouts"],
        _payout_rows(payouts, now),
    )


def _account_id(request: Request) -> uuid.UUID | None:
    try:
        return uuid.UUID(request.path_params["account_id"])
    except ValueError:
        return None


async def accounts_page(database_url: str) -> Response:
    async with await connect(database_url) as conn:
        summaries = await account_summaries(conn)
    return HTMLResponse(render_accounts(summaries))


async def account_page(request: Request, database_url: str, now: datetime) -> Response:
    account_id = _account_id(request)
    if account_id is None:
        return Response("no such account", status_code=404)
    async with await connect(database_url) as conn:
        summary = await account_summary(conn, account_id)
        if summary is None:
            return Response("no such account", status_code=404)
        payouts = await payouts_of(conn, account_id, PAYOUTS_SHOWN)
        found = await exceptions_of(conn, account_id)
    note = request.query_params.get("note")
    return HTMLResponse(render_account(summary, payouts, found, note, now))


async def resolve(request: Request, database_url: str, now: datetime) -> Response:
    if not same_origin(request):
        return Response("cross-site POST refused", status_code=403)
    account_id = _account_id(request)
    try:
        exception_id = uuid.UUID(request.path_params["exception_id"])
    except ValueError:
        return Response("no such exception", status_code=404)
    if account_id is None:
        return Response("no such account", status_code=404)
    form = await read_form(request, ResolveForm)
    if form is None:
        return Response("bad form", status_code=400)
    async with await connect(database_url) as conn:
        result = await resolve_exception(
            conn, account_id, exception_id, form.note, PERSON, now
        )
        await conn.commit()
    match result:
        case Resolved():
            note = "resolved"
        case AlreadyResolved():
            note = "already resolved: the first note stands"
        case EmptyNote():
            note = "not resolved: write what was found"
        case NoSuchException():
            return Response("no such exception", status_code=404)
    back = f"{PAGE}/{account_id}?{urlencode({'note': note})}"
    return RedirectResponse(back, status_code=303)

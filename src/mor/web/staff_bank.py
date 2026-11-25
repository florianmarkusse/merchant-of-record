"""The staff Bank pages (docs/plan-payments.md §7, the bank statement): per
bank account our `bank` balance beside the bank's own and what sits in
suspense; its entries, unexplained ones first, each credit with a form to
link it to a payout no rule matched, and any entry with a form for a note.
Linking books the match (decided 25-Nov-2026); a note books nothing.

    GET  /staff/bank                                  every account
    GET  /staff/bank/{account}                        one account
    POST /staff/bank/{account}/entries/{entry}/link   {"payout"}
    POST /staff/bank/{account}/entries/{entry}/note   {"note"}

Mounted outside prod only until staff sign-in exists, as the other staff
pages; until then the actor is this page, not a person.
"""

import uuid
from datetime import datetime
from urllib.parse import urlencode

import htpy
import msgspec
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from mor.audit import Actor, ActorKind
from mor.bank.matching import (
    AlreadyNoted,
    EmptyNote,
    Linked,
    Noted,
    NothingToNote,
    NotLinkable,
    link,
    lock_bank_account,
    note_entry,
)
from mor.bank.review import (
    BankSummary,
    EntryRow,
    LinkablePayout,
    bank_summaries,
    bank_summary,
    entries_of,
    linkable_payouts,
)
from mor.dates import format_day, format_moment
from mor.db.connect import connect
from mor.ids import BankAccountId, BankEntryId, SettlementPayoutId
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.web.forms import read_form, same_origin
from mor.web.staff_layout import money_text, staff_page


PAGE = "/staff/bank"
PERSON = Actor(ActorKind.STAFF, "staff-bank-page")
ENTRIES_SHOWN = 100


class LinkForm(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    payout: str


class NoteForm(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    note: str


def _money(amount_minor: int, currency: Currency) -> str:
    return money_text(Money(amount_minor, currency))


def _agreement(summary: BankSummary) -> htpy.Node:
    if summary.bank_balance_minor is None:
        return htpy.span(class_="muted")["no transactions yet"]
    shown = _money(summary.bank_balance_minor, summary.currency)
    if summary.bank_balance_minor == summary.ledger_minor:
        return f"{shown}, agrees"
    off = summary.ledger_minor - summary.bank_balance_minor
    return htpy.strong[f"{shown}, ours off by {_money(off, summary.currency)}"]


def render_accounts(summaries: list[BankSummary]) -> str:
    rows: list[htpy.Node] = []
    for summary in summaries:
        rows.append(
            htpy.tr[
                htpy.td[htpy.a(href=f"{PAGE}/{summary.id}")[summary.name]],
                htpy.td[summary.external_ref],
                htpy.td(class_="num")[_money(summary.ledger_minor, summary.currency)],
                htpy.td[_agreement(summary)],
                htpy.td(class_="num")[_money(summary.suspense_minor, summary.currency)],
                htpy.td(class_="num")[str(summary.unexplained)],
            ]
        )
    table: htpy.Node = htpy.p(class_="muted")["No bank accounts yet."]
    if rows:
        table = htpy.div(class_="scroll")[
            htpy.table(class_="data")[
                htpy.thead[
                    htpy.tr[
                        htpy.th["Account"],
                        htpy.th["Bank's reference"],
                        htpy.th["Our books"],
                        htpy.th["The bank says"],
                        htpy.th["In suspense"],
                        htpy.th["Unexplained entries"],
                    ]
                ],
                htpy.tbody[rows],
            ]
        ]
    return staff_page(
        "Bank",
        PAGE,
        ("table_filter.js",),
        htpy.h1["Bank"],
        htpy.p(class_="muted")[
            "Per bank account, one currency each: our books (bank) beside the"
            " bank's own balance after the last transaction read, and what"
            " sits in suspense because nothing explains it yet. Each PSP payout"
            " leaves suspense once its credit is matched."
        ],
        table,
    )


def _explained(
    account_id: BankAccountId, entry: EntryRow, payouts: list[LinkablePayout]
) -> htpy.Node:
    if entry.payout is not None:
        return f"payout {entry.payout} ({entry.matched_by})"
    if entry.note is not None:
        when = "" if entry.noted_at is None else format_moment(entry.noted_at)
        return f"{entry.note} ({entry.noted_by}, {when})"
    forms: list[htpy.Node] = []
    action = f"{PAGE}/{account_id}/entries/{entry.id}"
    if entry.amount_minor > 0 and payouts:
        options: list[htpy.Node] = []
        for payout in payouts:
            options.append(
                htpy.option(value=str(payout.id))[
                    f"{payout.vendor_payout_id} ({payout.psp_account},"
                    f" {_money(payout.net_minor, entry.currency)},"
                    f" {format_moment(payout.paid_out_at)})"
                ]
            )
        forms.append(
            htpy.form(method="post", action=f"{action}/link")[
                htpy.label["Is payout ", htpy.select(name="payout")[options]],
                " ",
                htpy.button(type="submit")["Link"],
            ]
        )
    forms.append(
        htpy.form(method="post", action=f"{action}/note")[
            htpy.label[
                "Or what it is ",
                htpy.input(type="text", name="note", required=True),
            ],
            " ",
            htpy.button(type="submit")["Note"],
        ]
    )
    return forms


def render_account(
    summary: BankSummary,
    entries: list[EntryRow],
    payouts: list[LinkablePayout],
    note: str | None,
) -> str:
    rows: list[htpy.Node] = []
    for entry in entries:
        rows.append(
            htpy.tr[
                htpy.td[format_day(entry.booked_on)],
                htpy.td[entry.vendor_entry_id],
                htpy.td(class_="num")[_money(entry.amount_minor, entry.currency)],
                htpy.td[entry.counterparty],
                htpy.td[entry.remittance],
                htpy.td[_explained(summary.id, entry, payouts)],
            ]
        )
    table: htpy.Node = htpy.p(class_="muted")["No entries read yet."]
    if rows:
        table = htpy.div(class_="scroll")[
            htpy.table(class_="data")[
                htpy.thead[
                    htpy.tr[
                        htpy.th["Booked"],
                        htpy.th["Bank's id"],
                        htpy.th["Amount"],
                        htpy.th["From or to"],
                        htpy.th["Text"],
                        htpy.th["Explained by"],
                    ]
                ],
                htpy.tbody[rows],
            ]
        ]
    return staff_page(
        summary.name,
        PAGE,
        ("table_filter.js",),
        htpy.p[htpy.a(href=PAGE)["All bank accounts"]],
        htpy.h1[f"Bank: {summary.name}"],
        htpy.p(role="status")[htpy.strong[note]] if note else None,
        htpy.p[
            f"Our books {_money(summary.ledger_minor, summary.currency)}; the"
            " bank says ",
            _agreement(summary),
            f"; in suspense {_money(summary.suspense_minor, summary.currency)}.",
        ],
        htpy.p(class_="muted")[
            f"{summary.external_ref}. A credit is matched to a PSP payout when"
            " the payout's id is in its text and nothing else claims either."
            " Linking one by hand books the same match; a note books nothing"
            " and leaves the entry in suspense."
        ],
        table,
    )


def _account_id(request: Request) -> BankAccountId | None:
    try:
        return BankAccountId(uuid.UUID(request.path_params["account_id"]))
    except ValueError:
        return None


def _entry_id(request: Request) -> BankEntryId | None:
    try:
        return BankEntryId(uuid.UUID(request.path_params["entry_id"]))
    except ValueError:
        return None


async def accounts_page(database_url: str) -> Response:
    async with await connect(database_url) as conn:
        summaries = await bank_summaries(conn)
    return HTMLResponse(render_accounts(summaries))


async def account_page(request: Request, database_url: str) -> Response:
    account_id = _account_id(request)
    if account_id is None:
        return Response("no such account", status_code=404)
    async with await connect(database_url) as conn:
        summary = await bank_summary(conn, account_id)
        if summary is None:
            return Response("no such account", status_code=404)
        entries = await entries_of(conn, account_id, ENTRIES_SHOWN)
        payouts = await linkable_payouts(conn, account_id)
    note = request.query_params.get("note")
    return HTMLResponse(render_account(summary, entries, payouts, note))


def _back(account_id: BankAccountId, note: str) -> Response:
    return RedirectResponse(
        f"{PAGE}/{account_id}?{urlencode({'note': note})}", status_code=303
    )


async def link_entry(request: Request, database_url: str, now: datetime) -> Response:
    if not same_origin(request):
        return Response("cross-site POST refused", status_code=403)
    account_id = _account_id(request)
    entry_id = _entry_id(request)
    if account_id is None or entry_id is None:
        return Response("no such entry", status_code=404)
    form = await read_form(request, LinkForm)
    if form is None:
        return Response("bad form", status_code=400)
    try:
        payout_id = SettlementPayoutId(uuid.UUID(form.payout))
    except ValueError:
        return Response("bad form", status_code=400)
    async with await connect(database_url) as conn:
        if await bank_summary(conn, account_id) is None:
            return Response("no such account", status_code=404)
        account = await lock_bank_account(conn, account_id)
        result = await link(conn, account, entry_id, payout_id, PERSON, now)
        await conn.commit()
    match result:
        case Linked():
            return _back(account_id, "linked")
        case NotLinkable(reason=reason):
            return _back(account_id, f"not linked: {reason}")


async def note_on_entry(request: Request, database_url: str, now: datetime) -> Response:
    if not same_origin(request):
        return Response("cross-site POST refused", status_code=403)
    account_id = _account_id(request)
    entry_id = _entry_id(request)
    if account_id is None or entry_id is None:
        return Response("no such entry", status_code=404)
    form = await read_form(request, NoteForm)
    if form is None:
        return Response("bad form", status_code=400)
    async with await connect(database_url) as conn:
        result = await note_entry(conn, account_id, entry_id, form.note, PERSON, now)
        await conn.commit()
    match result:
        case Noted():
            return _back(account_id, "noted")
        case AlreadyNoted():
            return _back(account_id, "already noted: the first note stands")
        case EmptyNote():
            return _back(account_id, "not noted: write what it is")
        case NothingToNote():
            return Response("no such entry", status_code=404)

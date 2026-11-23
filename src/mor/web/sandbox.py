"""The test setup's page: walk the echo flow by hand in every mode
(docs/plan-test-setup.md §4). Never part of the prod app.

    GET  /_fake/                     every account, its jobs and their events
    POST /_fake/sandbox/jobs         our side: create a job
    POST /_fake/sandbox/manual       our side: record an event by hand
    POST /_fake/sandbox/release      vendor side: release a job's next event
    POST /_fake/sandbox/redeliver    vendor side: push failed webhooks again
    POST /_fake/sandbox/cut          vendor side: cut a report
    POST /_fake/sandbox/fault        vendor side: script a fault
    POST /_fake/sandbox/settlement   PSP side: cut a settlement, maybe with
                                     a fault (docs/plan-payments.md §7.1)

Forms are plain POSTs answered with a redirect back to the page, which
carries a short note on what happened. The vendor-side buttons call the
fake's own API over HTTP, as the fuzzer does; nothing here writes the
fake's tables. The echo part is transitional with the echo flow; the PSP
settlement part stays.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from urllib.parse import urlencode

import htpy
import httpx2
import msgspec
from psycopg.rows import class_row
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from mor.audit import Actor, ActorKind
from mor.dates import format_moment
from mor.db.connect import Connection, connect
from mor.echo.events import VendorStatus
from mor.echo.flow import create_echo_job
from mor.echo.inbound import JobNotFound, record_manual_event
from mor.echo.port import EchoProvider, EventKind, EventMode
from mor.echo.records import EchoJobState
from mor.fakes.psp import CUT_FAULTS
from mor.web.forms import read_form, same_origin


PAGE = "/_fake/"
# No login yet (plan-test-setup §1): the page is reached on loopback only.
PERSON = Actor(ActorKind.STAFF, "sandbox-page")
JOBS_SHOWN = 200


# --- forms, decoded at the edge -------------------------------------------


class CreateJobForm(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    account_id: uuid.UUID
    message: str = "hello"


class ManualEventForm(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    job_id: uuid.UUID
    kind: Literal["confirmed", "archived"]


class ReleaseForm(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    job_id: uuid.UUID
    push: Literal["once", "twice", "bad_signature"]


class AccountForm(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    account_id: uuid.UUID


class FaultForm(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    flavour: Literal["keyed", "cancellable", "plain"]
    fault: Literal["fail_500", "fail_500_after_work", "timeout_after_work", "refuse"]
    operation: Literal["send", "events"]


class SettlementForm(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    account_id: uuid.UUID
    fault: str = ""


def _back(note: str) -> Response:
    return RedirectResponse(f"{PAGE}?{urlencode({'note': note})}", status_code=303)


# --- what the page shows ----------------------------------------------------


@dataclass(frozen=True)
class AccountRow:
    id: uuid.UUID
    provider: EchoProvider
    event_mode: EventMode
    base_url: str


@dataclass(frozen=True)
class JobRow:
    id: uuid.UUID
    account_id: uuid.UUID
    state: EchoJobState
    vendor_status: VendorStatus
    vendor_echo_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class EventRow:
    echo_job_id: uuid.UUID
    kind: str
    source: str


@dataclass(frozen=True)
class PspRow:
    id: uuid.UUID
    name: str
    settlement_mode: str


@dataclass(frozen=True)
class View:
    accounts: list[AccountRow]
    jobs: list[JobRow]
    events: list[EventRow]
    psp_accounts: list[PspRow]
    note: str | None


async def read_view(conn: Connection, note: str | None) -> View:
    async with conn.cursor(row_factory=class_row(AccountRow)) as cur:
        await cur.execute(
            "SELECT id, provider, event_mode, base_url FROM echo_account"
            " ORDER BY provider, event_mode"
        )
        accounts = await cur.fetchall()
    async with conn.cursor(row_factory=class_row(JobRow)) as cur:
        await cur.execute(
            "SELECT id, account_id, state, vendor_status,"
            " vendor_echo_id, created_at FROM echo_job"
            " ORDER BY created_at DESC LIMIT %s",
            (JOBS_SHOWN,),
        )
        jobs = await cur.fetchall()
    async with conn.cursor(row_factory=class_row(EventRow)) as cur:
        await cur.execute(
            "SELECT echo_job_id, kind::text, source::text FROM echo_job_event"
            " ORDER BY recorded_at"
        )
        events = await cur.fetchall()
    async with conn.cursor(row_factory=class_row(PspRow)) as cur:
        await cur.execute(
            "SELECT id, name, settlement_mode::text AS settlement_mode"
            " FROM psp_account WHERE kind = 'fake' ORDER BY name"
        )
        psp_accounts = await cur.fetchall()
    return View(
        accounts=accounts,
        jobs=jobs,
        events=events,
        psp_accounts=psp_accounts,
        note=note,
    )


def _button(action: str, label: str, fields: dict[str, str]) -> htpy.Element:
    hidden: list[htpy.Node] = []
    for name, value in fields.items():
        hidden.append(htpy.input(type="hidden", name=name, value=value))
    return htpy.form(method="post", action=action)[
        hidden, htpy.button(type="submit")[label]
    ]


def _job_row(job: JobRow, events: list[EventRow]) -> htpy.Element:
    recorded: list[str] = []
    for event in events:
        if event.echo_job_id == job.id:
            recorded.append(f"{event.kind} ({event.source})")
    job_id = str(job.id)
    vendor: list[htpy.Node] = []
    if job.vendor_echo_id is not None:
        for push, label in (
            ("once", "release next"),
            ("twice", "release, pushed twice"),
            ("bad_signature", "release, badly signed"),
        ):
            vendor.append(
                _button(
                    "/_fake/sandbox/release", label, {"job_id": job_id, "push": push}
                )
            )
    manual: list[htpy.Node] = []
    for kind in EventKind:
        manual.append(
            _button(
                "/_fake/sandbox/manual",
                f"record {kind.value} by hand",
                {"job_id": job_id, "kind": kind.value},
            )
        )
    return htpy.tr[
        htpy.td[htpy.code[job_id]],
        htpy.td[format_moment(job.created_at)],
        htpy.td[job.state],
        htpy.td[job.vendor_status],
        htpy.td[", ".join(recorded) or "none"],
        htpy.td[vendor],
        htpy.td[manual],
    ]


def _account_section(account: AccountRow, view: View) -> htpy.Element:
    account_id = str(account.id)
    actions: list[htpy.Node] = [
        _button("/_fake/sandbox/jobs", "create a job", {"account_id": account_id})
    ]
    if account.event_mode in (EventMode.PUSH, EventMode.PUSH_THIN):
        actions.append(
            _button(
                "/_fake/sandbox/redeliver",
                "vendor: push failed webhooks again",
                {"account_id": account_id},
            )
        )
    if account.event_mode == EventMode.FILE:
        actions.append(
            _button(
                "/_fake/sandbox/cut", "vendor: cut a report", {"account_id": account_id}
            )
        )
    rows: list[htpy.Node] = []
    for job in view.jobs:
        if job.account_id == account.id:
            rows.append(_job_row(job, view.events))
    return htpy.section[
        htpy.h2[f"{account.provider}, {account.event_mode}"],
        htpy.p[htpy.code[account.base_url]],
        htpy.div[actions],
        htpy.table[
            htpy.tr[
                htpy.th["job"],
                htpy.th["created"],
                htpy.th["state"],
                htpy.th["vendor status"],
                htpy.th["events recorded"],
                htpy.th["vendor"],
                htpy.th["by hand"],
            ],
            rows,
        ]
        if rows
        else htpy.p["No jobs yet."],
    ]


def _fault_form() -> htpy.Element:
    def choice(name: str, values: tuple[str, ...]) -> htpy.Element:
        options: list[htpy.Element] = []
        for value in values:
            options.append(htpy.option(value=value)[value])
        return htpy.select(name=name)[options]

    return htpy.form(method="post", action="/_fake/sandbox/fault")[
        "Vendor: the next ",
        choice("operation", ("send", "events")),
        " call to ",
        choice("flavour", ("keyed", "cancellable", "plain")),
        " fails with ",
        choice(
            "fault", ("fail_500", "fail_500_after_work", "timeout_after_work", "refuse")
        ),
        " ",
        htpy.button(type="submit")["script fault"],
    ]


def _settlement_section(view: View) -> htpy.Node:
    if not view.psp_accounts:
        return None
    options: list[htpy.Element] = [htpy.option(value="")["no fault"]]
    for fault in CUT_FAULTS:
        options.append(htpy.option(value=fault)[fault])
    rows: list[htpy.Node] = []
    for account in view.psp_accounts:
        rows.append(
            htpy.li[
                htpy.form(method="post", action="/_fake/sandbox/settlement")[
                    htpy.input(type="hidden", name="account_id", value=str(account.id)),
                    f"{account.name} (settles by {account.settlement_mode}) ",
                    htpy.select(name="fault")[options],
                    " ",
                    htpy.button(type="submit")["cut a settlement"],
                ]
            ]
        )
    return htpy.section[
        htpy.h2["PSP settlement"],
        htpy.p[
            "The fake PSP settles every captured payment not settled yet, one"
            " batch per currency. Our side reads it at the account's next look;"
            " see ",
            htpy.a(href="/staff/settlement")["the Settlement staff page"],
            ".",
        ],
        htpy.ul[rows],
    ]


def render(view: View) -> str:
    sections: list[htpy.Node] = []
    for account in view.accounts:
        sections.append(_account_section(account, view))
    page = htpy.html(lang="en")[
        htpy.head[
            htpy.meta(charset="utf-8"),
            htpy.title["Sandbox: echo flow"],
        ],
        htpy.body[
            htpy.h1["Sandbox: echo flow"],
            htpy.p[
                "Not prod. Our side creates jobs and records events by hand;"
                " the vendor side releases events, pushes webhooks and cuts"
                " reports. The worker does the rest; reload to see it."
            ],
            htpy.p(role="status")[htpy.strong[view.note]] if view.note else None,
            _settlement_section(view),
            _fault_form(),
            sections or htpy.p["No accounts: run `python -m mor.seed`."],
        ],
    ]
    return str(page)


# --- handlers -------------------------------------------------------------


async def show(request: Request, database_url: str) -> Response:
    note = request.query_params.get("note")
    async with await connect(database_url) as conn:
        view = await read_view(conn, note)
    return HTMLResponse(render(view))


async def create_job(
    request: Request, database_url: str, clock: Callable[[], datetime]
) -> Response:
    if not same_origin(request):
        return Response("cross-site POST refused", status_code=403)
    form = await read_form(request, CreateJobForm)
    if form is None:
        return Response("bad form", status_code=400)
    async with await connect(database_url) as conn:
        job_id = await create_echo_job(
            conn, form.account_id, form.message, PERSON, clock()
        )
        await conn.commit()
    return _back(f"created job {job_id}")


async def record_by_hand(
    request: Request, database_url: str, clock: Callable[[], datetime]
) -> Response:
    if not same_origin(request):
        return Response("cross-site POST refused", status_code=403)
    form = await read_form(request, ManualEventForm)
    if form is None:
        return Response("bad form", status_code=400)
    now = clock()
    async with await connect(database_url) as conn:
        recorded = await record_manual_event(
            conn, form.job_id, EventKind(form.kind), now, PERSON, now
        )
        await conn.commit()
    if isinstance(recorded, JobNotFound):
        return _back(f"no job {form.job_id}")
    return _back(f"recorded {form.kind} by hand for {form.job_id}")


async def _job_at_vendor(
    database_url: str, job_id: uuid.UUID
) -> tuple[str, str] | None:
    """The job's account URL at the fake and its echo id there."""
    async with await connect(database_url) as conn:
        cur = await conn.execute(
            "SELECT a.base_url, j.vendor_echo_id FROM echo_job j"
            " JOIN echo_account a ON a.id = j.account_id WHERE j.id = %s",
            (job_id,),
        )
        row = await cur.fetchone()
    if row is None or row[1] is None:
        return None
    return row[0], row[1]


async def _account_url(database_url: str, account_id: uuid.UUID) -> str | None:
    async with await connect(database_url) as conn:
        cur = await conn.execute(
            "SELECT base_url FROM echo_account WHERE id = %s", (account_id,)
        )
        row = await cur.fetchone()
    return None if row is None else row[0]


async def _vendor_call(http: httpx2.AsyncClient, url: str, body: object) -> str:
    try:
        response = await http.post(url, json=body)
    except httpx2.TransportError as error:
        return f"the fake did not answer: {error!r}"
    return f"vendor answered {response.status_code}: {response.text}"


async def release(
    request: Request, database_url: str, http: httpx2.AsyncClient
) -> Response:
    if not same_origin(request):
        return Response("cross-site POST refused", status_code=403)
    form = await read_form(request, ReleaseForm)
    if form is None:
        return Response("bad form", status_code=400)
    at_vendor = await _job_at_vendor(database_url, form.job_id)
    if at_vendor is None:
        return _back(f"job {form.job_id} has no echo at the vendor yet")
    base_url, echo_id = at_vendor
    url = f"{base_url}/echoes/{echo_id}/events/release"
    return _back(await _vendor_call(http, url, {"push": form.push}))


async def redeliver(
    request: Request, database_url: str, http: httpx2.AsyncClient
) -> Response:
    return await _account_call(request, database_url, http, "/events/redeliver")


async def cut(
    request: Request, database_url: str, http: httpx2.AsyncClient
) -> Response:
    return await _account_call(request, database_url, http, "/reports/cut")


async def _account_call(
    request: Request, database_url: str, http: httpx2.AsyncClient, path: str
) -> Response:
    if not same_origin(request):
        return Response("cross-site POST refused", status_code=403)
    form = await read_form(request, AccountForm)
    if form is None:
        return Response("bad form", status_code=400)
    base_url = await _account_url(database_url, form.account_id)
    if base_url is None:
        return _back(f"no account {form.account_id}")
    return _back(await _vendor_call(http, f"{base_url}{path}", {}))


async def fault(request: Request, self_url: str, http: httpx2.AsyncClient) -> Response:
    if not same_origin(request):
        return Response("cross-site POST refused", status_code=403)
    form = await read_form(request, FaultForm)
    if form is None:
        return Response("bad form", status_code=400)
    body = {"flavour": form.flavour, "fault": form.fault, "operation": form.operation}
    return _back(await _vendor_call(http, f"{self_url}/_fake/echo/faults", body))


async def cut_settlement(
    request: Request, database_url: str, http: httpx2.AsyncClient
) -> Response:
    if not same_origin(request):
        return Response("cross-site POST refused", status_code=403)
    form = await read_form(request, SettlementForm)
    if form is None or (form.fault and form.fault not in CUT_FAULTS):
        return Response("bad form", status_code=400)
    async with await connect(database_url) as conn:
        cur = await conn.execute(
            "SELECT base_url FROM psp_account WHERE id = %s AND kind = 'fake'",
            (form.account_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return _back(f"no fake PSP account {form.account_id}")
    faults = [form.fault] if form.fault else []
    return _back(await _vendor_call(http, f"{row[0]}/settlements", {"faults": faults}))

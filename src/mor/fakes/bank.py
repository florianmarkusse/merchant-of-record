"""The fake bank: our accounts at a made-up bank, in the shape of a bank's
transactions API (ABN AMRO's Business Account Insight)
(docs/plan-payments.md §7, the bank statement).

A bank account is one currency balance (`B` = /_fake/bank/{account}):

    POST B/setup                  {"currency"}: open the account
    POST B/destinations           {"psp_account"}: that fake PSP account pays
                                  out this currency here; its cut leaves a
                                  pending credit
    POST B/book                   book what is pending, in the order it came:
                                  the day's transactions (called by the staff
                                  sandbox page, the seed and the fuzzer,
                                  never by a timer)
    GET  B/transactions?pageKey=  {"transactions": [...], "nextPageKey",
                                  "has_more"}, booked ones after that
                                  position, oldest first, a few a page
    POST /_fake/bank/faults       {"account", "fault": fail_500|timeout}: the
                                  next list fails once

A transaction: `transactionId`, a signed decimal `amount` in major units,
`currency`, `balanceAfterMutation`, `descriptionLines`,
`counterPartyName`, `bookDate`. Amounts are kept in ISO 4217 minor units
(IDR has 2 here). Its state is its own (schema `fake_vendor`).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import msgspec
import psycopg
from psycopg.rows import TupleRow, class_row
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from mor.db.connect import SESSION_OPTIONS
from mor.db.fetch import one_value
from mor.fakes.hang import Hang


# What a cut can be scripted to get wrong at the bank, each once, on its
# first batch's credit: no reference in the text, the reference cut short,
# a correspondent bank's charge taken off, a credit that never arrives, a
# transaction listed twice, a credit nobody announced and a debit we did not
# make, each beside it.
type BankFault = Literal[
    "no_reference",
    "reference_cut",
    "short",
    "never_arrives",
    "listed_twice",
    "unannounced",
    "unexpected_debit",
]
BANK_FAULTS: tuple[BankFault, ...] = (
    "no_reference",
    "reference_cut",
    "short",
    "never_arrives",
    "listed_twice",
    "unannounced",
    "unexpected_debit",
)
type ListFault = Literal["fail_500", "timeout"]

PAGE_SIZE = 3
# What a `short` credit lacks, and the stray amounts, in minor units.
CHARGE_MINOR = 1500
UNANNOUNCED_MINOR = 420000
DEBIT_MINOR = 2500
PSP_NAME = "Fake PSP B.V."

type FakeConnection = psycopg.AsyncConnection[TupleRow]


class SetupBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    currency: str


class DestinationBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    psp_account: str


class FaultBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    account: str
    fault: ListFault


class Transaction(msgspec.Struct, frozen=True, kw_only=True, rename="camel"):
    transaction_id: str
    amount: str
    currency: str
    balance_after_mutation: str
    description_lines: list[str]
    counter_party_name: str
    book_date: date


class Page(msgspec.Struct, frozen=True, rename="camel"):
    transactions: list[Transaction]
    next_page_key: str | None
    has_more: bool


async def _connect(database_url: str) -> FakeConnection:
    return await psycopg.AsyncConnection.connect(
        database_url, options=f"-c search_path=fake_vendor {SESSION_OPTIONS}"
    )


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


# The fake's own table: ISO 4217's, where IDR has 2 (ours has 0).
ZERO_DECIMAL = frozenset({"VND", "JPY", "KRW"})


def iso_exponent(currency: str) -> int:
    if currency in ZERO_DECIMAL:
        return 0
    return 2


def decimal_text(amount_minor: int, currency: str) -> str:
    """`-1234.50`: minor units as a bank shows them."""
    places = iso_exponent(currency)
    sign = "-" if amount_minor < 0 else ""
    whole, part = divmod(abs(amount_minor), 10**places)
    if places == 0:
        return f"{sign}{whole}"
    return f"{sign}{whole}.{part:0{places}d}"


async def _new_id(conn: FakeConnection, prefix: str) -> str:
    """From the fakes' sequence, never at random: ids sort in the order they
    were made, so a fuzzer's replay meets the same order."""
    number = await one_value(conn, "SELECT nextval('fake_id')", None, int)
    return f"{prefix}_{number:012d}"


async def _pending(
    conn: FakeConnection,
    account: str,
    amount_minor: int,
    description: str,
    counterparty: str,
    payout_id: str | None,
    faults: list[BankFault],
    state: Literal["pending", "lost"] = "pending",
) -> None:
    await conn.execute(
        """
        INSERT INTO bank_fake_transaction
          (account, amount_minor, description, counterparty, state, payout_id,
           faults)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (account, amount_minor, description, counterparty, state, payout_id, faults),
    )


async def credit_payout(
    conn: FakeConnection,
    psp_account: str,
    currency: str,
    payout_id: str,
    net_minor: int,
    faults: frozenset[BankFault],
) -> None:
    """A PSP payout on its way: a pending credit at the PSP account's
    destination for this currency, if it names one, in the PSP's own
    transaction. `faults` are this credit's (the caller passes them for its
    first batch only)."""
    cur = await conn.execute(
        """
        SELECT bank_account FROM bank_fake_destination
        WHERE psp_account = %s AND currency = %s
        """,
        (psp_account, currency),
    )
    row = await cur.fetchone()
    if row is None or net_minor <= 0:
        return
    bank_account: str = row[0]
    description = f"PAYOUT {payout_id} {psp_account}"
    amount = net_minor
    applied: list[BankFault] = []
    if "no_reference" in faults:
        description = f"PAYOUT {psp_account}"
        applied.append("no_reference")
    elif "reference_cut" in faults:
        description = f"PAYOUT {payout_id[:-3]}"
        applied.append("reference_cut")
    if "short" in faults and amount > CHARGE_MINOR:
        amount -= CHARGE_MINOR
        applied.append("short")
    state: Literal["pending", "lost"] = "pending"
    if "never_arrives" in faults:
        state = "lost"
        applied.append("never_arrives")
    elif "listed_twice" in faults:
        applied.append("listed_twice")
    await _pending(
        conn, bank_account, amount, description, PSP_NAME, payout_id, applied, state
    )
    if "unannounced" in faults:
        await _pending(
            conn,
            bank_account,
            UNANNOUNCED_MINOR,
            "INVOICE 2231 THANK YOU",
            "Someone Else Pte Ltd",
            None,
            ["unannounced"],
        )
    if "unexpected_debit" in faults:
        await _pending(
            conn,
            bank_account,
            -DEBIT_MINOR,
            "ACCOUNT MAINTENANCE FEE",
            "The Bank",
            None,
            ["unexpected_debit"],
        )


async def handle_setup(request: Request, database_url: str) -> Response:
    try:
        body = msgspec.json.decode(await request.body(), type=SetupBody)
    except msgspec.DecodeError:
        return _error(400, "invalid_body")
    async with await _connect(database_url) as conn:
        await conn.execute(
            """
            INSERT INTO bank_fake_account (name, currency) VALUES (%s, %s)
            ON CONFLICT (name) DO NOTHING
            """,
            (request.path_params["account"], body.currency),
        )
        await conn.commit()
    return Response(status_code=204)


async def handle_destination(request: Request, database_url: str) -> Response:
    account = request.path_params["account"]
    try:
        body = msgspec.json.decode(await request.body(), type=DestinationBody)
    except msgspec.DecodeError:
        return _error(400, "invalid_body")
    async with await _connect(database_url) as conn:
        cur = await conn.execute(
            "SELECT currency FROM bank_fake_account WHERE name = %s", (account,)
        )
        row = await cur.fetchone()
        if row is None:
            return _error(404, "unknown_account")
        try:
            await conn.execute(
                """
                INSERT INTO bank_fake_destination
                  (psp_account, currency, bank_account)
                VALUES (%s, %s, %s)
                ON CONFLICT (psp_account, currency)
                DO UPDATE SET bank_account = excluded.bank_account
                """,
                (body.psp_account, row[0], account),
            )
        except psycopg.errors.ForeignKeyViolation:
            return _error(404, "unknown_psp_account")
        await conn.commit()
    return Response(status_code=204)


@dataclass(frozen=True, kw_only=True)
class _PendingRow:
    seq: int
    amount_minor: int
    description: str
    counterparty: str
    payout_id: str | None
    faults: list[str]


async def handle_book(
    request: Request, database_url: str, clock: Callable[[], datetime]
) -> Response:
    """The day's booking: every pending transaction, in the order it came."""
    account = request.path_params["account"]
    booked: list[str] = []
    async with await _connect(database_url) as conn:
        cur = await conn.execute(
            "SELECT balance_minor FROM bank_fake_account WHERE name = %s FOR UPDATE",
            (account,),
        )
        row = await cur.fetchone()
        if row is None:
            return _error(404, "unknown_account")
        balance: int = row[0]
        async with conn.cursor(row_factory=class_row(_PendingRow)) as rows:
            await rows.execute(
                """
                SELECT seq, amount_minor, description, counterparty, payout_id,
                       faults
                FROM bank_fake_transaction
                WHERE account = %s AND state = 'pending'
                ORDER BY seq
                """,
                (account,),
            )
            pending = await rows.fetchall()
        today = clock().date()
        twice: list[tuple[str, _PendingRow]] = []
        for found in pending:
            transaction_id = await _new_id(conn, "txn")
            balance += found.amount_minor
            await conn.execute(
                """
                UPDATE bank_fake_transaction
                SET state = 'booked', transaction_id = %s, booked_on = %s,
                    balance_after_minor = %s
                WHERE seq = %s
                """,
                (transaction_id, today, balance, found.seq),
            )
            booked.append(transaction_id)
            if "listed_twice" in found.faults:
                twice.append((transaction_id, found))
        # The same transactions again, after the day's last, with the
        # balance as it then stands: no money moved.
        for transaction_id, found in twice:
            await conn.execute(
                """
                INSERT INTO bank_fake_transaction
                  (account, transaction_id, amount_minor, description,
                   counterparty, state, booked_on, balance_after_minor,
                   payout_id, faults)
                VALUES (%s, %s, %s, %s, %s, 'booked', %s, %s, %s, %s)
                """,
                (
                    account,
                    transaction_id,
                    found.amount_minor,
                    found.description,
                    found.counterparty,
                    today,
                    balance,
                    found.payout_id,
                    found.faults,
                ),
            )
        await conn.execute(
            "UPDATE bank_fake_account SET balance_minor = %s WHERE name = %s",
            (balance, account),
        )
        await conn.commit()
    return JSONResponse({"booked": booked})


@dataclass(frozen=True, kw_only=True)
class _BookedRow:
    seq: int
    transaction_id: str
    amount_minor: int
    description: str
    counterparty: str
    booked_on: date
    balance_after_minor: int


async def handle_list(request: Request, database_url: str, hang: Hang) -> Response:
    account = request.path_params["account"]
    after = request.query_params.get("pageKey", "0")
    async with await _connect(database_url) as conn:
        cur = await conn.execute(
            "SELECT currency FROM bank_fake_account WHERE name = %s", (account,)
        )
        row = await cur.fetchone()
        if row is None:
            return _error(404, "unknown_account")
        currency: str = row[0]
        cur = await conn.execute(
            "DELETE FROM bank_fake_scripted WHERE account = %s RETURNING fault",
            (account,),
        )
        scripted = await cur.fetchone()
        await conn.commit()
        if scripted is not None:
            if scripted[0] == "timeout":
                await hang()
            return _error(500, "internal")
        if not after.isdigit():
            return _error(400, "bad_page_key")
        async with conn.cursor(row_factory=class_row(_BookedRow)) as rows:
            await rows.execute(
                """
                SELECT seq, transaction_id, amount_minor, description,
                       counterparty, booked_on, balance_after_minor
                FROM bank_fake_transaction
                WHERE account = %(account)s AND state = 'booked'
                  AND seq > %(after)s
                ORDER BY seq
                LIMIT %(limit)s
                """,
                {"account": account, "after": int(after), "limit": PAGE_SIZE + 1},
            )
            found = await rows.fetchall()
    shown: list[Transaction] = []
    next_key: str | None = None
    for booked in found[:PAGE_SIZE]:
        next_key = str(booked.seq)
        shown.append(
            Transaction(
                transaction_id=booked.transaction_id,
                amount=decimal_text(booked.amount_minor, currency),
                currency=currency,
                balance_after_mutation=decimal_text(
                    booked.balance_after_minor, currency
                ),
                description_lines=[booked.description],
                counter_party_name=booked.counterparty,
                book_date=booked.booked_on,
            )
        )
    page = Page(shown, next_key, len(found) > PAGE_SIZE)
    return Response(msgspec.json.encode(page), media_type="application/json")


async def handle_fault(request: Request, database_url: str) -> Response:
    try:
        body = msgspec.json.decode(await request.body(), type=FaultBody)
    except msgspec.DecodeError:
        return _error(400, "invalid_body")
    async with await _connect(database_url) as conn:
        try:
            await conn.execute(
                """
                INSERT INTO bank_fake_scripted (account, fault) VALUES (%s, %s)
                ON CONFLICT (account) DO UPDATE SET fault = excluded.fault
                """,
                (body.account, body.fault),
            )
        except psycopg.errors.ForeignKeyViolation:
            return _error(404, "unknown_account")
        await conn.commit()
    return Response(status_code=204)


def make_app(database_url: str, clock: Callable[[], datetime], hang: Hang) -> Starlette:
    """`clock` is the bank's own day; tests share theirs with it."""

    async def setup(request: Request) -> Response:
        return await handle_setup(request, database_url)

    async def destination(request: Request) -> Response:
        return await handle_destination(request, database_url)

    async def book(request: Request) -> Response:
        return await handle_book(request, database_url, clock)

    async def transactions(request: Request) -> Response:
        return await handle_list(request, database_url, hang)

    async def fault(request: Request) -> Response:
        return await handle_fault(request, database_url)

    account_path = "/_fake/bank/{account}"
    return Starlette(
        routes=[
            Route("/_fake/bank/faults", fault, methods=["POST"]),
            Route(f"{account_path}/setup", setup, methods=["POST"]),
            Route(f"{account_path}/destinations", destination, methods=["POST"]),
            Route(f"{account_path}/book", book, methods=["POST"]),
            Route(f"{account_path}/transactions", transactions, methods=["GET"]),
        ]
    )

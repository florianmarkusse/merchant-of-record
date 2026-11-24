import asyncio
import uuid
from datetime import UTC, datetime

import psycopg
import pytest

from mor.audit import Actor, ActorKind
from mor.db.connect import connect
from mor.ledger.account import Account, AccountKind
from mor.ledger.entry import JournalEntry, build_entry, credit, debit
from mor.ledger.store import (
    AlreadyRecorded,
    IdempotencyKeyReused,
    Recorded,
    balance,
    record_entry,
)
from mor.money.amount import Money
from mor.money.currency import Currency


NOW = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)
SYSTEM = Actor(ActorKind.SYSTEM, "test")

CLEARING = Account(AccountKind.PSP_CLEARING)
GST_SG = Account(AccountKind.TAX_PAYABLE, "SG")
SHIPPING = Account(AccountKind.SHIPPING_PAYABLE, "fake_courier")
MERCHANT_1 = Account(AccountKind.MERCHANT_PAYABLE, "merchant-1")
MERCHANT_2 = Account(AccountKind.MERCHANT_PAYABLE, "merchant-2")
PSP_FEE = Account(AccountKind.PSP_FEE_COLLECTED)
MARGIN = Account(AccountKind.REVENUE_MOR_MARGIN)


def _sgd(amount_minor: int) -> Money:
    return Money(amount_minor, Currency.SGD)


def _capture(key: str, merchant_account: Account = MERCHANT_1) -> JournalEntry:
    """A synthetic SGD capture shaped like plan-architecture.md §9: goods
    SGD 100.00 to the merchant, fee and margin on top, shipping, 9% GST."""
    return build_entry(
        idempotency_key=key,
        description="capture",
        effective_at=NOW,
        actor=SYSTEM,
        lines=[
            debit(CLEARING, _sgd(13_386)),
            credit(merchant_account, _sgd(10_000)),
            credit(PSP_FEE, _sgd(340)),
            credit(MARGIN, _sgd(500)),
            credit(SHIPPING, _sgd(1_440)),
            credit(GST_SG, _sgd(1_106)),
        ],
        now=NOW,
    )


async def _record_and_commit(url: str, entry: JournalEntry) -> object:
    async with await connect(url) as conn:
        result = await record_entry(conn, entry)
        await conn.commit()
    return result


async def _balance(url: str, account: Account) -> Money:
    async with await connect(url) as conn:
        return await balance(conn, account, Currency.SGD)


async def _execute_and_commit(url: str, *statements: bytes) -> None:
    async with await connect(url) as conn:
        for statement in statements:
            await conn.execute(statement)
        await conn.commit()


def test_recorded_entry_shows_in_each_accounts_balance(database_url: str) -> None:
    entry = _capture("capture-1")

    result = asyncio.run(_record_and_commit(database_url, entry))

    assert result == Recorded(entry.id)
    assert asyncio.run(_balance(database_url, CLEARING)) == _sgd(13_386)
    assert asyncio.run(_balance(database_url, MERCHANT_1)) == _sgd(-10_000)
    assert asyncio.run(_balance(database_url, GST_SG)) == _sgd(-1_106)


def test_scope_keeps_merchants_apart(database_url: str) -> None:
    asyncio.run(_record_and_commit(database_url, _capture("c-1", MERCHANT_1)))
    asyncio.run(_record_and_commit(database_url, _capture("c-2", MERCHANT_1)))
    asyncio.run(_record_and_commit(database_url, _capture("c-3", MERCHANT_2)))

    assert asyncio.run(_balance(database_url, MERCHANT_1)) == _sgd(-20_000)
    assert asyncio.run(_balance(database_url, MERCHANT_2)) == _sgd(-10_000)
    assert asyncio.run(_balance(database_url, CLEARING)) == _sgd(3 * 13_386)


def test_account_without_lines_has_zero_balance(database_url: str) -> None:
    assert asyncio.run(_balance(database_url, MARGIN)) == _sgd(0)


def test_same_entry_recorded_twice_counts_once(database_url: str) -> None:
    first = _capture("capture-1")
    # A retry builds the entry again: a new id, the same key and content.
    retry = _capture("capture-1")

    asyncio.run(_record_and_commit(database_url, first))
    result = asyncio.run(_record_and_commit(database_url, retry))

    assert result == AlreadyRecorded(first.id)
    assert asyncio.run(_balance(database_url, CLEARING)) == _sgd(13_386)


def test_same_key_for_different_content_is_a_bug(database_url: str) -> None:
    asyncio.run(_record_and_commit(database_url, _capture("capture-1", MERCHANT_1)))

    with pytest.raises(IdempotencyKeyReused):
        asyncio.run(_record_and_commit(database_url, _capture("capture-1", MERCHANT_2)))
    assert asyncio.run(_balance(database_url, MERCHANT_2)) == _sgd(0)


def test_same_key_from_two_connections_at_once_records_once(
    database_url: str,
) -> None:
    # The second insert waits on the unique key until the first commits, then
    # finds it: no check-then-insert window.
    async def both() -> list[object]:
        return list(
            await asyncio.gather(
                _record_and_commit(database_url, _capture("capture-1")),
                _record_and_commit(database_url, _capture("capture-1")),
            )
        )

    results = asyncio.run(both())

    kinds = sorted(type(r).__name__ for r in results)
    assert kinds == ["AlreadyRecorded", "Recorded"]
    assert asyncio.run(_balance(database_url, CLEARING)) == _sgd(13_386)


def _raw_entry_sql(entry_id: uuid.UUID) -> bytes:
    return (
        "INSERT INTO journal_entry (id, idempotency_key, description,"
        " effective_at, recorded_at, actor_kind, actor_id)"
        f" VALUES ('{entry_id}', 'raw-{entry_id}', 'raw', now(), now(),"
        " 'system', 'test')"
    ).encode()


def _raw_line_sql(entry_id: uuid.UUID, line_no: int, amount: int) -> bytes:
    return (
        "INSERT INTO journal_line (entry_id, line_no, account_kind,"
        " account_scope, amount_minor, currency)"
        f" VALUES ('{entry_id}', {line_no}, 'psp_clearing', '', {amount}, 'SGD')"
    ).encode()


def test_database_refuses_an_unbalanced_entry_at_commit(database_url: str) -> None:
    # Bypassing build_entry, as a buggy code path or a hand-written fix would.
    entry_id = uuid.uuid7()
    with pytest.raises(psycopg.errors.RaiseException, match="does not balance"):
        asyncio.run(
            _execute_and_commit(
                database_url,
                _raw_entry_sql(entry_id),
                _raw_line_sql(entry_id, 1, 500),
                _raw_line_sql(entry_id, 2, -499),
            )
        )
    assert asyncio.run(_balance(database_url, CLEARING)) == _sgd(0)


def test_database_refuses_an_entry_without_lines_at_commit(
    database_url: str,
) -> None:
    with pytest.raises(psycopg.errors.RaiseException, match="needs at least 2"):
        asyncio.run(_execute_and_commit(database_url, _raw_entry_sql(uuid.uuid7())))


def test_database_refuses_changing_or_deleting_recorded_entries(
    database_url: str,
) -> None:
    asyncio.run(_record_and_commit(database_url, _capture("capture-1")))

    for statement in [
        b"UPDATE journal_line SET amount_minor = amount_minor * 2",
        b"DELETE FROM journal_line",
        b"UPDATE journal_entry SET description = 'edited'",
        b"DELETE FROM journal_entry",
    ]:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            asyncio.run(_execute_and_commit(database_url, statement))

    assert asyncio.run(_balance(database_url, CLEARING)) == _sgd(13_386)

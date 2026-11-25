import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from mor.audit import Actor
from mor.ids import JournalEntryId, JournalKey
from mor.ledger.account import Account
from mor.money.amount import Money
from mor.money.currency import Currency


class UnbalancedEntry(Exception):
    """An entry that breaks double-entry rules: a bug in the code that built
    it. The database refuses it too, at commit."""


@dataclass(frozen=True)
class Line:
    """Debit positive, credit negative. Built with `debit` and `credit`, so
    the sign is never written by hand."""

    account: Account
    amount: Money


@dataclass(frozen=True, kw_only=True)
class JournalEntry:
    id: JournalEntryId
    idempotency_key: JournalKey
    description: str
    effective_at: datetime
    recorded_at: datetime
    actor: Actor
    lines: tuple[Line, ...]


def debit(account: Account, amount: Money) -> Line:
    if amount.amount_minor <= 0:
        raise UnbalancedEntry(f"debit of {amount} must be positive")
    return Line(account, amount)


def credit(account: Account, amount: Money) -> Line:
    if amount.amount_minor <= 0:
        raise UnbalancedEntry(f"credit of {amount} must be positive")
    return Line(account, Money(-amount.amount_minor, amount.currency))


def _require_utc(name: str, moment: datetime) -> None:
    if moment.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be aware UTC, got {moment!r}")


def build_entry(
    idempotency_key: JournalKey,
    description: str,
    effective_at: datetime,
    actor: Actor,
    lines: Sequence[Line],
    now: datetime,
) -> JournalEntry:
    """A balanced entry: at least two lines, summing to zero per currency.
    `effective_at` is when the money moved (a capture's time); `now` is when
    we record it."""
    _require_utc("effective_at", effective_at)
    _require_utc("now", now)
    if len(lines) < 2:
        raise UnbalancedEntry(f"{idempotency_key}: needs at least two lines")
    totals: dict[Currency, int] = {}
    for line in lines:
        if line.amount.amount_minor == 0:
            raise UnbalancedEntry(f"{idempotency_key}: zero line on {line.account}")
        currency = line.amount.currency
        totals[currency] = totals.get(currency, 0) + line.amount.amount_minor
    for currency, total in totals.items():
        if total != 0:
            raise UnbalancedEntry(f"{idempotency_key}: {currency} is off by {total}")
    return JournalEntry(
        id=JournalEntryId(uuid.uuid7()),
        idempotency_key=idempotency_key,
        description=description,
        effective_at=effective_at,
        recorded_at=now,
        actor=actor,
        lines=tuple(lines),
    )

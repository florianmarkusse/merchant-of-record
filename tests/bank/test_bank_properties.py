"""Properties of the bank's pure parts (docs/plan-payments.md §7, the bank
statement): an entry and a match balance and move what they should, the
matching rule pairs only by a whole reference with one candidate on each
side, and the decoder turns any bytes into transactions or a typed error,
reading the bank's decimals exactly."""

import uuid
from datetime import UTC, date, datetime, timedelta

import msgspec
from hypothesis import given, strategies

from mor.audit import Actor, ActorKind
from mor.bank.entries import (
    Pair,
    UnmatchedCredit,
    UnmatchedPayout,
    bank_entry,
    match_entry,
    pairs,
    tokens,
)
from mor.bank.fake_client import decode_transactions
from mor.bank.port import BankAccount, BankProvider, BankTransaction
from mor.fakes.bank import Page, Transaction, decimal_text, iso_exponent
from mor.ids import (
    BankAccountId,
    BankEntryId,
    BankTransactionId,
    PspAccountId,
    PspPayoutId,
    SettlementPayoutId,
)
from mor.ledger.account import Account, Bank, BankSuspense, PayoutInTransit
from mor.ledger.entry import JournalEntry
from mor.money.amount import Money
from mor.money.currency import Currency, exponent
from mor.provider_accounts import ProviderAccountKind
from mor.vendor_failures import UndecodableBody


BANK = BankAccountId(uuid.UUID("01a1fac0-0000-7000-8000-0000000000b1"))
PSP = PspAccountId(uuid.UUID("01a1fac0-0000-7000-8000-000000000001"))
ACTOR = Actor(ActorKind.SYSTEM, "test")
NOW = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)
DAY = date(2026, 11, 25)


def _account(currency: Currency) -> BankAccount:
    return BankAccount(
        id=BANK,
        name="bank",
        provider=BankProvider.FAKE,
        kind=ProviderAccountKind.FAKE,
        base_url="http://bank",
        timeout=timedelta(seconds=5),
        external_ref="FAKE-0001",
        currency=currency,
        statement_interval=timedelta(hours=1),
    )


def _moved(entry: JournalEntry, account: Account) -> int:
    total = 0
    for line in entry.lines:
        if line.account == account:
            total += line.amount.amount_minor
    return total


_amounts = strategies.integers(-(10**12), 10**12).filter(lambda amount: amount != 0)


@given(amount=_amounts)
def test_an_entry_puts_what_the_bank_shows_in_bank_against_suspense(
    amount: int,
) -> None:
    entry = bank_entry(
        BANK, BankTransactionId("txn-1"), Money(amount, Currency.SGD), DAY, ACTOR, NOW
    )
    assert _moved(entry, Bank(BANK)) == amount
    assert _moved(entry, BankSuspense(BANK)) == -amount


@given(arrived=strategies.integers(1, 10**12))
def test_a_match_moves_what_arrived_from_suspense_to_clear_the_transit(
    arrived: int,
) -> None:
    entry = match_entry(
        BANK,
        BankTransactionId("txn-1"),
        PSP,
        PspPayoutId("po-1"),
        Money(arrived, Currency.SGD),
        DAY,
        ACTOR,
        NOW,
    )
    assert _moved(entry, BankSuspense(BANK)) == arrived
    assert _moved(entry, PayoutInTransit(PSP)) == -arrived


_ids = strategies.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=12
)
_texts = strategies.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_- .,/:", max_size=60
)


@strategies.composite
def _books(
    draw: strategies.DrawFn,
) -> tuple[list[UnmatchedCredit], list[UnmatchedPayout]]:
    """Payouts with distinct ids; credits whose texts name some of them, or
    parts of them, or nothing."""
    payout_ids = draw(strategies.lists(_ids, max_size=5, unique=True))
    payouts: list[UnmatchedPayout] = []
    for number, payout_id in enumerate(payout_ids):
        payouts.append(
            UnmatchedPayout(
                SettlementPayoutId(uuid.UUID(int=number + 1)), PspPayoutId(payout_id)
            )
        )
    credits_drawn: list[UnmatchedCredit] = []
    for number in range(draw(strategies.integers(0, 5))):
        words: list[str] = []
        for _ in range(draw(strategies.integers(0, 3))):
            if payout_ids and draw(strategies.booleans()):
                words.append(draw(strategies.sampled_from(payout_ids)))
            else:
                words.append(draw(_texts))
        credits_drawn.append(
            UnmatchedCredit(BankEntryId(uuid.UUID(int=100 + number)), " ".join(words))
        )
    return credits_drawn, payouts


@given(books=_books())
def test_a_pair_is_a_whole_reference_with_one_candidate_on_each_side(
    books: tuple[list[UnmatchedCredit], list[UnmatchedPayout]],
) -> None:
    unmatched_credits, payouts = books
    found = pairs(unmatched_credits, payouts)
    names: dict[BankEntryId, frozenset[str]] = {}
    for unmatched in unmatched_credits:
        names[unmatched.entry_id] = tokens(unmatched.remittance)
    candidates: dict[BankEntryId, set[SettlementPayoutId]] = {}
    claimants: dict[SettlementPayoutId, set[BankEntryId]] = {}
    for unmatched in unmatched_credits:
        for payout in payouts:
            if payout.vendor_payout_id in names[unmatched.entry_id]:
                candidates.setdefault(unmatched.entry_id, set()).add(payout.payout_id)
                claimants.setdefault(payout.payout_id, set()).add(unmatched.entry_id)
    expected: list[Pair] = []
    for unmatched in unmatched_credits:
        mine = candidates.get(unmatched.entry_id, set())
        if len(mine) == 1:
            (payout_id,) = mine
            if claimants[payout_id] == {unmatched.entry_id}:
                expected.append(Pair(unmatched.entry_id, payout_id))
    assert found == expected


@given(payout_id=_ids, cut=strategies.integers(1, 11))
def test_a_reference_cut_short_matches_nothing(payout_id: str, cut: int) -> None:
    shortened = payout_id[: max(len(payout_id) - cut, 0)]
    payout = UnmatchedPayout(
        SettlementPayoutId(uuid.UUID(int=1)), PspPayoutId(payout_id)
    )
    credit_seen = UnmatchedCredit(
        BankEntryId(uuid.UUID(int=2)), f"PAYOUT {shortened}X {shortened}"
    )
    if shortened == payout_id:
        return
    assert pairs([credit_seen], [payout]) == []


@given(
    minor=strategies.integers(-(10**12), 10**12).filter(lambda amount: amount != 0),
    currency=strategies.sampled_from(list(Currency)),
)
def test_the_banks_decimals_are_read_exactly_or_refused(
    minor: int, currency: Currency
) -> None:
    """The fake shows ISO 4217's minor units (IDR has 2); ours may have
    fewer, and an amount that is not whole in ours is refused, never
    rounded."""
    page = Page(
        [
            Transaction(
                transaction_id="txn-1",
                amount=decimal_text(minor, currency.value),
                currency=currency.value,
                balance_after_mutation=decimal_text(minor, currency.value),
                description_lines=["PAYOUT po-1"],
                counter_party_name="Fake PSP",
                book_date=DAY,
            )
        ],
        "1",
        False,
    )
    decoded = decode_transactions(_account(currency), msgspec.json.encode(page))
    scale = 10 ** (iso_exponent(currency.value) - exponent(currency))
    if minor % scale != 0:
        assert isinstance(decoded, UndecodableBody)
        return
    assert not isinstance(decoded, UndecodableBody), decoded
    (transaction,) = decoded
    assert transaction.amount == Money(minor // scale, currency)


@given(body=strategies.binary(max_size=400))
def test_any_bytes_decode_to_transactions_or_a_typed_error(body: bytes) -> None:
    decoded = decode_transactions(_account(Currency.SGD), body)
    if not isinstance(decoded, UndecodableBody):
        for transaction in decoded:
            assert isinstance(transaction, BankTransaction)

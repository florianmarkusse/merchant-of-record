# Settlement as vendors send it: lines and payouts in any order

Status: agreed and built 23-Nov-2026 (answers in §6), not committed.
Amends `plan-payments.md` §7.1 (5c-1); comes before 5c-2 (the bank
statement), which it feeds. §7 holds what building settled, §8 how it was
checked.

## 1. Why

Dropping delivery order (`plan-messaging-rework.md` §2) broke one
consumer that relied on it without saying so. An API batch is read page by
page; each page is decoded by its own `psp_inbox_decode` delivery, and the
last page's decode sends `settlement_batch_received`. Decoded last page
first, `book_payout` checks the net against the lines stored so far and
opens a false `net_differs` exception.
`test_a_batch_read_by_pages_is_booked_whichever_page_is_decoded_first`
(`tests/settlement/test_settlement.py`) fails this way today.

Fixing it with page bookkeeping would model what our fake sends, and no
vendor sends that. So this plan starts from what real PSPs send.

## 2. What real PSPs send (read 23-Nov-2026)

**Adyen: one file per payout batch.** "The default setting is to
generate the report per merchant account, per batch", named
`settlement_detail_report_batch_<N>.csv`, "generated every time we make a
payout to your bank account". A `REPORT_AVAILABLE` webhook carries the
file name and a download URL (a thin push). Rows by journal type:
`Settled`, `Refunded`, `Chargeback`, `Fee`, one `MerchantPayout` (the
amount sent to the bank), and `Balancetransfer`, "a balance transfer to or
from subsequent payout batches": Adyen "can adjust the amount paid out to
your bank account by transferring funds between settlement batches". No
total or count row; Adyen says to add up the rows. Nothing found on a
batch's report being reissued with changes; corrections show up as
transfers into later batches.

**Stripe: the payout and its lines are separate objects.** The payout
object has `amount` ("the amount … that transfers to your bank account"),
`currency`, `arrival_date`, `status`, and no count or totals. Its lines are
balance transactions listed with `payout=po_…`, cursor-paged
(`starting_after`, at most 100, newest first, `has_more`). The payout's
`reconciliation_status`: `in_progress` ("you can query … soon"),
`completed` ("you can use the Balance Transactions API to list all balance
transactions that are paid out in this payout"), `not_applicable` (manual
payouts: "Stripe can't identify which transactions are included").
There is also a `payout.reconciliation_completed` event.

**Xendit: no batch.** A transaction's `settlement_status` goes `PENDING`
to `SETTLED`, which means it reached the merchant's Xendit balance.
Money reaches the bank by a withdrawal, manual or automatic (daily,
weekly or monthly, "based on the previous day's closing balance", above
an optional minimum). The documented signals are emails of completed
withdrawals and reports (the balance report, by API too; the
auto-withdrawal report, by dashboard, email or SFTP), not a withdrawal
API or webhook. A withdrawal's amount comes from the balance, not from a
listed set of transactions.

Sources: [Stripe payout reconciliation](https://docs.stripe.com/payouts/reconciliation),
[Stripe balance transactions list](https://docs.stripe.com/api/balance_transactions/list),
[Stripe payout object](https://docs.stripe.com/api/payouts/object),
[Adyen settlement details report](https://docs.adyen.com/reporting/settlement-reconciliation/transaction-level/settlement-details-report),
[Adyen batch-level reconciliation](https://docs.adyen.com/reporting/settlement-reconciliation/batch-level),
[Adyen automatically get reports](https://docs.adyen.com/reporting/automatically-get-reports),
[Xendit transaction status](https://docs.xendit.co/docs/transaction-status.md),
[Xendit reconciliation](https://docs.xendit.co/docs/reconciliations),
[Xendit auto-withdrawal](https://docs.xendit.co/docs/auto-withdrawal.md),
[Xendit auto-withdrawal report](https://docs.xendit.co/docs/auto-withdrawal-report.md).

This corrects `plan-payments.md` §7.1 "What real PSPs send", which has
Xendit sending batches through a report API.

## 3. The atomic things

Each has the vendor's own id, so each is stored once, whatever order it
arrives in and however often:

1. **A line settled**: an Adyen row, a Stripe balance transaction, a
   Xendit transaction turning `SETTLED`. Today's `settlement_line`.
2. **A payout reported**: the PSP says it sent this net to the bank. An
   Adyen `MerchantPayout` row, a Stripe payout object, a Xendit
   withdrawal. Today's `settlement_batch`, booked on its own.
3. **A line belongs to a payout**: Adyen by file, Stripe by the payout
   filter, Xendit not at all.

Pages are not one of them: a page is how far our reader got through a
list.

## 4. Design

### 4.1 Facts and booking, each on its own

- **A line** is stored once by `(account, vendor_line_id)` and names its
  payout by the vendor's id (`vendor_payout_id`), not by a row of ours,
  so a line can come before its payout. Booking it
  (`settlement_line_book`) does not change.
- **A payout** is stored once by `(account, vendor_payout_id)`, and
  `settlement_payout_reported` (new, replaces
  `settlement_batch_received`) is sent once, when the row is first
  inserted. Booking it (`settlement_payout_book`) books the net as
  reported, as today's payout entry does, without looking at the lines:

  ```
  Dr payout_in_transit:{account}   the reported net
  Cr psp_clearing:{account}        the same
  ```

  It is today's entry minus its `rounding` line, which needs the lines
  and moves to §4.2.
- **A balance transfer** (Adyen) is a line type `balance_transfer` that
  books nothing (it moves money inside the PSP's balance, which is
  `psp_clearing` for us) but counts towards its payout's sum.

### 4.2 Reconciling a payout, from whatever has arrived

A new local consumer, `settlement_payout_reconcile`, gets
`settlement_line_received` (for a line naming a payout) and
`settlement_payout_reported`. Under the payout's row lock it compares the
reported net with its lines' exact `gross − fee` sum, and records one of:

- **No payout row yet**: nothing to do. The payout's own fact runs this
  again once it arrives.
- **Balanced**: the net is the exact sum rounded either way to our
  exponent. Book the rounding remainder once, as its own entry keyed
  `payout-rounding:{account}:{vendor payout id}`, and mark the payout
  `balanced`.
- **Not balanced yet**: store the difference and when it was first seen.

It converges because every stored line sends a fact after its commit, so
the last run sees every line. Two runs at once serialize on the payout's
row lock. A line inserted while a run holds the lock commits after that
run reads, and its own fact runs the check again. Nothing depends on the
order of pages, lines or payout.

A payout can balance by coincidence before its last line arrives (a
later pair of lines that cancel out, or a transfer in and out). A line
for a `balanced` payout runs the check again. If the sum no longer
fits, the payout goes back to open and the rounding entry is reversed
by a new entry, never edited. Rare, but it must not be wrong.

This follows the shared rules: the payout row lock guards the
read-then-write; the rounding entry is booked once through its key; time
does not decide.

### 4.3 When a difference needs a person

"Not balanced yet" is normal for a while: Stripe lists the lines only
once `reconciliation_status` is `completed`, pages arrive one by one, and
an Adyen file is read in one go but its payout may sit beside transfers
from other batches. The question is when an open difference becomes a
person's job. Recommended (question 1): **it is shown, not raised**. The
staff settlement page lists payouts not balanced, with the difference and
how long it has been open. Once the bank statement import (5c-2) has
matched the payout's credit, a payout still not balanced is flagged on
that page. Today's append-only `net_differs` exception, raised once at
booking, goes away; the resolve-with-a-note form applies to a flagged
payout as it does to a line exception.

The other choice raises `net_differs` once a trigger fires (bank credit
matched, or a deadline after `arrival_date`), decided under the payout's
lock. It is an exception row like the others, but a line arriving after
the trigger leaves a stale exception for a person to resolve.

### 4.4 The fake PSP, made to look like the vendors

- **`api` mode: Stripe's shape.** `GET A/payouts?after=` lists payout
  ids, oldest first. `GET A/payouts/{id}` returns the object: id, amount,
  currency, arrival date, `reconciliation_status`, which stays
  `in_progress` for a while after the cut. `GET A/payouts/{id}/lines?starting_after=`
  returns newest first, two a page as now, with `has_more`. The fetch
  stores the payout object and each page raw, as now, and reads a
  payout's lines only once it is `completed`.
- **`file` mode: Adyen's shape.** One CSV per batch with its payout as a
  `merchant_payout` row instead of a header, and a new cut fault
  `balance_transfer` that moves part of one batch's net into the next.
  It stays listed by our poll; the `REPORT_AVAILABLE` thin push is a later
  capability, like the PSP's thin webhooks.
- Xendit's shape (a balance, withdrawals reported by email or report) is
  not modelled until a Xendit module exists (question 2).

### 4.5 Our side

- `psp_settlement_fetch` keeps one look per delivery. In `api` mode a
  look is one of: list payouts after the cursor, fetch a payout object,
  or fetch one page of a `completed` payout's lines. The cursor stores
  the payout being read and the line cursor (`starting_after`), not a
  page number.
- `psp_inbox_decode` decodes a payout object, a page of lines or a file,
  stores each atomic thing once, and sends one fact per new row. No
  `last` and no page bookkeeping.

### 4.6 Tables and code (one migration)

- `settlement_batch` becomes `settlement_payout`. `vendor_batch_id`
  becomes `vendor_payout_id`, plus `reconciliation` (enum `open`,
  `balanced`, `flagged`), `difference_minor`, `open_since`.
  `booked_at` stays for the payout entry.
- `settlement_line`: `vendor_payout_id text NOT NULL` in place of
  `batch_id`. The join is by `(account_id, vendor_payout_id)`, so a line
  needs no payout row to exist.
- New line type `balance_transfer`.
- Facts: `settlement_payout_reported` V1 replaces
  `settlement_batch_received`. That is a retired type, so its decode case
  stays until prod has no undelivered one. Prod has never run settlement,
  so it can go in the same change, if the user confirms there is no prod
  data.
- Consumer: `settlement_payout_reconcile`, local.

## 5. Tests

- Through the edge, per mode: the failing test above, reworded to
  "pages, lines and payout in any order", passes; a Stripe payout read
  before its lines are `completed` waits, then balances; an Adyen file
  with a balance transfer balances both batches; a payout whose lines never
  add up is listed open and flagged after its bank credit (with 5c-2).
- Properties: for any order of a payout's facts, the reconciliation ends
  in the same state with the same entries (a pure function of the set).
- The settlement fuzzer: delivery order is random already; add
  milestones "a payout reported before its last line", "a line before its
  payout", "a payout balanced by a later line"
  (`tests/flow/reach.py`), so the soak shows the orders are reached.
  The planted bug for the old design (net checked at the last page) must
  be caught.

## 6. Questions (my recommendation first)

1. **An open difference**: shown on the staff page and flagged once the
   bank credit is matched, or raised as a `net_differs` exception at a
   trigger (§4.3). **Answered 23-Nov-2026: shown on the staff settlement
   page.** The flag comes with 5c-2.
2. **Scope**: Stripe's and Adyen's shapes now; Xendit's balance shape
   with the first Xendit module. **Answered 23-Nov-2026: Stripe and Adyen
   now.**
3. **Order of work**: this before committing the messaging rework's
   §3.1 and §3.2, which are done but would land the bug of §1.
   **Answered 23-Nov-2026: agreed; settlement is committed first.**
4. **Prod data**: retiring `settlement_batch_received` and `net_differs`
   outright needs prod to hold no settlement data. **Answered
   23-Nov-2026: there is no prod data.** So the enum values are renamed
   or dropped in the migration, with no transitional decode case.
5. **5c-2 after this**: the bank statement import matches a credit to a
   `settlement_payout` and flags it if not balanced. Designed after this
   is built, as planned.

## 7. Settled while building

- **What we asked for is stored with the raw bytes.** A Stripe-shaped
  line page does not name its payout (the filter was in our request), so
  `psp_inbox` gets `request_ref`, the payout whose lines the page lists.
  That records our request; it is not decoded from the body.
- **The read cursor** (`psp_settlement_cursor`) in `api` mode is a phase:
  `payout` (fetch the payout object; once it is `completed`, go on) or
  `lines` with the last line id read (`starting_after`). It holds no page
  number. A payout still `in_progress` is looked at again after the
  account's interval.
- **A balance transfer line** gets the state `counted`: it books nothing
  and counts towards its payout.
- **The rounding entry follows the set.** Each reconciliation sets the
  payout's booked rounding to its target (the rounding when balanced,
  zero when open) with a new entry for the change, keyed
  `payout-rounding:{account}:{payout}:{n}`. A payout that balanced by
  coincidence and then reopens is reversed by a new entry, never edited.
- **A line's entry is dated by the line** (`settled_at`, from the vendor),
  no longer by its batch's payout date, since the payout may not be
  stored yet.
- **A line in another currency than its payment** is still
  `currency_differs`; one in another currency than its payout is left
  out of the payout's sum, so the payout stays open and shows it.
- **The fake** (`src/mor/fakes/psp.py`, `migrations_fake/0009`): a
  payout's lines can be listed 10 minutes after its cut (409 before); a
  `balance_transfer` fault holds 3.00 back for the account's next batch
  in that currency, which pays it with a transfer line.
- **Old rows are refused, not converted.** `0026` stops on a database
  with settlement rows of the old shape. `mor_dev` had none; `mor_sandbox`
  had one batch from the 5c-1 check and must be recreated (migrate, fakes
  migrate, seed) before it runs this code.

## 8. How it was checked (23-Nov-2026)

- Through the edge (`tests/settlement/`): per mode, a cut settles every
  payment and the payout balances; a net that is off is booked as paid
  and shown open by its gap, in both modes, with no exception; a balance
  transfer leaves both batches balanced and `psp_clearing` at zero once
  the next batch pays it; a Stripe-shaped payout read before its lines
  are listable is booked, stays open, and balances at the next look; a
  payout and its two pages decoded in either order end balanced (the
  test that failed with the bug of §1); facts met again book nothing.
- Properties: payout plus rounding entries clear exactly what the lines
  left; a rounding change and its reversal leave nothing; the decoders on
  any bytes and any CSV; a page's lines belong to the payout asked for.
- The settlement fuzzer's deep start fetches the first cut whole and
  decodes its documents in a drawn order, booking between decodes as
  drawn. At 600 examples (685 runs) every milestone is above the floor:
  a line stored before its payout 22.8%, an older page before a newer one
  7.4%, a payout open while its lines arrive 29.2%. Without that deep
  start the first two were reached by no run.
- Planted bug: lines not triggering the reconciliation (only the payout
  does). Four edge tests fail, all in `api` mode, and the fuzzer fails at
  100 examples on payouts left open after the drain.

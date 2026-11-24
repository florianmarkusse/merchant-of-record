# Messaging rework: easier to follow, same guarantees

Status: 24-Nov-2026: §1, §3.1 and §3.2 built (commit 83a9a04). §3.3,
the one table for every consumer, agreed 24-Nov-2026: the test seam and
every effect consumer but echo's built 24-Nov-2026 (§1); next, the local
consumers' locks into the table.
Amends `plan-messaging.md` and the worker loop in
`plan-architecture.md` §5.

The user reviewed the messaging core and found it reliable but hard to
follow. The goal is the same guarantees (outbox, at-least-once,
idempotent consumers, no double vendor calls), with less to keep in mind
when following a fact through the code.

## 1. Done

- 23-Nov-2026: each fact record names its own `message_type` and
  `schema_version` as `ClassVar`s; `fact_type` is gone
  (`src/mor/messaging/facts.py`, checked by
  `tests/messaging/test_facts.py`).
- 23-Nov-2026: §3.1 and §3.2 built (commit 83a9a04), with settlement
  reworked to take lines and payouts in any order
  (`plan-settlement-reconciliation.md`).
- 23-Nov-2026: `session_payment_ended` acts only on the session's newest
  attempt. Found while checking each local consumer for lock-first: an
  earlier attempt's end appended again (a producer retrying) set a session
  back to `quoted` under its open second attempt
  (`test_an_earlier_attempts_end_met_again_leaves_the_open_one_paying`).
  The payment fuzzer's meet-again rule could reach this but did not: a
  second attempt open while the first's end is met again is rare there.

- 24-Nov-2026, §3.3 begun. The test seam: `RefusingTransport`
  (`tests/helpers.py`) crashes the worker before a request is sent or
  after its answer came, and `crash_around_a_call` runs the real
  `run_one`; every fuzzer that crashed around a call uses it (the echo
  ones keep the old helpers until echo is converted). Measured on the
  duty and payment fuzzers: both crash points fire for every effect
  consumer they reach. Then `duty_rate_ask`: `mor.messaging.delivery`
  (the delivery plumbing out of `worker.py`, and `run_steps`), the table's
  `run_effect` and `expect`, and `prepare_ask`, `ask`, `complete_ask` in
  `src/mor/duties/lookups.py`. Measured: the consumer went from 36 to 23
  lines (its `_lookup_id` and `AskAnswer` gone); `run_steps` is 30 lines
  of code. Pyright refuses a call step that takes a connection or a
  prepare step that takes the HTTP client (tried on a throwaway file).
  The suite passes, and the duty fuzzer passed 600 runs on the soak
  profile (it records no reach milestones). The other effects still run
  through the old driver, moved into `consumers.py` and marked
  transitional, so `consumers.py` is larger until they move.

- 24-Nov-2026, §3.3 continued: `shipping_price_ask`, the five payment
  consumers and `psp_settlement_fetch` run through `run_steps`. Create
  and capture make one of two calls (send, or look up after a lost
  answer under guard `none`), so their call step says which it made:
  `CreateSent`, `CaptureSent` or `Looked`, three small records where five
  `*Answer` records were. Poll and fetch share `prepare_poll` and
  `fetch_events`; only `complete_poll` schedules the next poll (it
  matched on the fact before). Settlement makes one of four reads whose
  `complete` needs that read typed, so its four answer records stay as
  its call step's result; the `call` `run_steps` also hands it goes
  unused. The two tests that held a step open by hand
  (`test_quote_concurrency`, `test_concurrent_workers`) now call the
  consumer's own steps. Left on the old driver: the three echo
  consumers.

## 2. Decided: drop ordering per consumer and aggregate

Decided 23-Nov-2026 by the user. Today a delivery waits until every
older unhandled delivery for the same consumer and aggregate is handled
(the `seq <` check in `_open_delivery`, `src/mor/messaging/worker.py`).
It guarantees nothing a consumer relies on:

- Every consumer that gets several facts for one aggregate already takes
  them in any order: `payment_event_apply` only moves an attempt forward
  (`src/mor/payments/transitions.py`, `Stale` otherwise), `echo_event_apply`
  derives the status from all recorded events and compares ranks,
  `quote_build` re-checks the session under its lock, a poll re-reads the
  attempt.
- `settlement_line_book` gets its facts from three aggregates (order,
  payment attempt, settlement line), so the check never ordered them.
- Order across consumers (a cancel and a create on one attempt) was never
  kept; the advisory lock keeps them from overlapping.

What it did cost: a delivery scheduled for later held back every later one
for its pair (§3.1), and a delivery that raised held back everything
behind it.

The shared `messaging.md` line "Ordering holds per aggregate, never
globally" becomes, once this is built: "No ordering: every consumer takes
facts in any order, late and twice (a state that only moves forward, or a
re-check under the row lock). Fuzzers deliver in random order to prove
it."

## 3. Design (for review; nothing built)

### 3.1 Claiming without order

- `find_candidates` returns due, unhandled deliveries oldest first, with
  no `DISTINCT ON`. `_open_delivery` loses the `not_next` check and
  `Skipped("not_next")`. `next_retry_at` becomes the earliest future
  `not_before` of any unhandled delivery.
- Scheduled deliveries stay where they are (`not_before` in the future);
  with nothing waiting behind them they block nothing, so no separate
  table. `payment_events_wanted` keeps its own consumer only if the code
  needs it; otherwise it folds back into `psp_payment_poll` (its comment
  in `consumers_for` goes either way).
- Claiming stays read, then lock: the session advisory lock (effect) and
  the delivery's row lock taken by `_open_delivery` with `FOR UPDATE SKIP
  LOCKED`, so a second worker gets `busy` at once instead of waiting
  behind the first's transaction. A crashed holder frees it with its
  connection. The old index for the ordered query is replaced by one on
  `not_before` (`migrations/0024_unordered_deliveries.sql`).
- Fuzzers: they already draw a candidate from `find_candidates`; with
  every due delivery in that list they shuffle order with no change. The
  probe fuzzer's "crash holding the aggregate's lock" became "crash
  holding a delivery's row lock"; tests that took the advisory lock
  before a local step no longer do.
  Reach checks (`tests/flow/reach.py`) show whether the new orders get
  covered. Expect fuzzer findings: they are the point.

### 3.2 Which lock, stated per kind

- **Effect consumer**: the worker holds the session advisory lock on the
  fact's aggregate from `prepare` to `complete`. That is the shared rule's
  "work spanning commits": without it a second worker's `prepare` sees a
  request marked `sent`, takes it for a dead run and asks or sends again
  while the first call is still in flight. For every effect consumer
  today the fact's aggregate is the row its `prepare` locks (attempt,
  duty lookup, shipping price lookup, PSP account's settlement cursor,
  echo job or account). `aggregate_of` keeps its name (it also records
  every fact's aggregate in `outbox`); its docstring says what the
  aggregate is used for: "the thing the fact is about; an effect consumer
  holds its lock across the vendor call", in place of "its order is
  kept per aggregate".
- **Local consumer**: no advisory lock. One transaction; it locks the
  rows it changes (`lock_attempt`, `lock_session`, the settlement line),
  and the delivery's row lock plus the inbox stop a second run.
- What this changes: today a local consumer on attempt X waits while an
  effect on X is mid-call (a webhook's `payment_event_apply` while the
  capture call is out). Afterwards it runs, as a web handler already can
  (`pay_another_way`). Each effect's `complete` already re-locks the row
  and checks the state, because a handler can move it.
  `test_a_webhook_is_applied_while_the_capture_call_is_out`
  (`tests/flow/test_concurrent_workers.py`) applies the PSP's `captured`
  webhook while our capture call is out, then completes the capture: one
  charge, order and entry. It failed on the old worker (`busy`).
- `outbox_delivery.aggregate_type`/`aggregate_id` stay (the worker names
  the lock from them without decoding the fact); no migration.

### 3.3 Design: one table that narrows, locks and runs (agreed 24-Nov-2026)

Replaces the two separate proposals made on 23-Nov-2026 (one function per
effect consumer; a declaration of each local consumer's lock), which
turned out to be one change. Still decided: the worker locks a local
consumer's row before any of its code runs (option 1 of three; a fuzzer
rule and an ast-grep rule were rejected).

**Today**, a fact goes through four places before anything happens:
`consumers.handle` or `consumers.prepare` picks a function by consumer
name; that function matches the fact again to find its id (26 `raise
WrongFact`, 10 `_x_id(fact)` helpers) and locks its row somewhere inside;
for an effect, `consumers.call` and `consumers.complete` dispatch a second
time on the call and answer records, and `payment_flow.complete` matches
the answer a third time.

**The table**: `mor.messaging.consumers`, one `match` on the consumer
name per kind. Each case does, in this order, what today is spread out:

1. **Narrow** the fact to what the consumer needs (an id, or the typed
   fact when it carries data) with `expect(fact, Record)`, an `isinstance`
   check that raises `WrongFact`; a consumer that gets several fact types
   matches them in its case. This is the only place that raises
   `WrongFact`: consumer functions take ids and typed facts, never `Fact`.
2. **Lock** (local consumers): the row the consumer changes, with the
   module's existing lock function, passed to the consumer as an
   argument. Where the row is found through another (a settlement line's
   attempt), the lookup reads a column that never changes and sits in the
   same case. A consumer that locks nothing says why in a comment on its
   case (the inbox decoders read an inbox row that never changes;
   `quote_wait_notify` only appends facts).
3. **Run** it: a local consumer in the worker's transaction; an effect
   consumer through `run_steps`.

**An effect consumer is three functions**, side by side in its module,
each given only what it may use:

```python
async def prepare_ask(conn, lookup_id, now) -> NoCall | AskCall          # database only
async def ask(http, environment, call: AskCall) -> RatesResult           # vendor only
async def complete_ask(conn, lookup_id, call, result, now) -> StepResult  # database only

case ConsumerName.DUTY_RATE_ASK:
    lookup_id = expect(fact, DutyRateNeededV1).duty_lookup_id
    return await run_steps(conn, http, environment, candidate, lookup_id, now,
                           prepare=mor.duties.lookups.prepare_ask,
                           call=mor.duties.lookups.ask,
                           complete=mor.duties.lookups.complete_ask)
```

`run_steps` (`mor.messaging.delivery`) is generic over the subject, the
call and the vendor's result, so pyright checks the three fit together.
It owns every transaction: `prepare` in one (a `NoCall` ends the delivery
there), the call with none open, `complete` in a new one that also ends
or defers the delivery, and `prepare` again while `complete` says
`Again` (a payment's duplicate to cancel, a settlement file per
listing), with the bound the worker has today.

**Why by design, not by a check**: no row lock can be held across a
vendor call, because no code has a connection and the HTTP client at
once except `run_steps`. The call function gets no connection; the steps
get no HTTP client. A runtime check (every registry call asserting no
open transaction) was considered and dropped: with this shape it has
nothing left to catch.

**The worker** keeps what it does now and loses the effect driver and
its dispatch: it picks due deliveries, opens one (the delivery's row
lock, `handled_at`, `not_before`, decoding), and calls the table's local
case in the same transaction or, holding the aggregate's advisory lock,
its effect case. For an effect it opens the delivery in a short
transaction of its own: the advisory lock keeps every other worker off
the delivery until `run_steps` ends it, as it does across commits today.
The delivery plumbing the table needs (`Candidate`, opening, finishing,
deferring, `run_steps`) moves from `worker.py` to
`mor.messaging.delivery`, so the table does not import the worker.

**Gone**: `consumers.prepare`, `call` and `complete`; the `EffectCall`
and `EffectAnswer` unions and every `*Answer` record (`run_steps` hands
the call and the result to `complete`); the worker's `prepare_delivery`,
`complete_delivery`, `run_effect` and `Prepared`;
`payment_flow.complete`'s second match; the `_x_id(fact)` helpers and the
`WrongFact` checks inside consumer modules. `NoCall`, `Done`,
`RetryLater` and `Again` stay as the steps' results.

**Tests**: the fuzzers crash a worker between an effect's commits by
calling `prepare_delivery` and `consumers.call` themselves. Instead, the
tests' network (`RefusingTransport`, `tests/helpers.py`), which already
refuses connections on demand, can crash the worker before a request is
sent or after its answer came back; the crash helpers run the real
`run_one` and then close the connection as a dead worker would. Fault in
the network, the code path real, and the same for converted and
unconverted consumers, so the tests move first.

**Rules applied**: explicit over clever (one `match`, jump-to-definition
lands on what runs); data-oriented (plain functions taking what they
need: ids, typed facts, locked rows); locks, not clocks (row locks only
inside a step's transaction, the advisory lock across the vendor call);
errors as values (vendor results matched in `complete`).

**Size**: about 230 lines of dispatch go (consumers.py 80, worker 75,
payment call/complete 55, answer records); `run_steps` (about 30) and
the 16 local cases' lock lines come in. Measured on the first
conversion.

**Order of building**: the test seam first. Then `duty_rate_ask`, with
`run_steps` and `mor.messaging.delivery`, the other consumers still on
the old path; measured (lines, the suite, the duty fuzzer's reach). Then
the other effects (shipping price, the five payment consumers,
settlement fetch), then the 16 local consumers' locks into the table,
then the old dispatch removed. The echo consumers are transitional:
converted last, or retired with the echo flow if the shipment slice
comes first. No fact changes shape, so the fact-schema snapshot guards
each step.

**Open**: whether a locked row gets a type of its own (`Locked[Attempt]`)
so a consumer cannot be handed one from `read_attempt`. Proposed: no; the
table is the one place that calls the consumer, so review sees it.

### 3.4 Order of work

1. §3.1 and §3.2 together (worker, `aggregate_of`'s docstring, the
   `messaging.md` line, the worker loop in `plan-architecture.md` §5
   step 3, the concurrent case), then a soak run.
2. §3.3 on `duty_rate_ask`, measured, then the rest in the order §3.3
   gives.

### 3.5 Smaller items

- **The message's own uuid.** `outbox.id` identifies the message (one
  probe has two facts), but `outbox.seq` already does. Dropping `id`
  touches migrations, the inbox and deliveries for little gain: low
  priority.
- **Version draining.** All facts are V1 today. When the first V2 lands:
  a CLI or staff page listing `(message_type, schema_version, open
  deliveries)`, and a test that each old version's `decode` case carries
  its "transitional until" comment.
- **Replay.** Facts are kept forever, but a new consumer gets no old
  facts. A backfill command (append deliveries for past facts of a type)
  covers it when first needed.

## 4. Kept, with the reason

- **Push, with producers not knowing consumers.** `append_fact` looks
  consumers up in the one table in `routing.py`, a subscription list.
  A pull design (a cursor per consumer on the log) loses per-message
  retry and backoff, and one failing message stops the cursor.

## 5. Outside messaging, also open from the same review

- Ledger: a typed scope per account kind (`MerchantPayable(merchant_id)`,
  `TaxPayable(country)`) instead of a bare `str`; `_compare_with_recorded`
  (`src/mor/ledger/store.py`) as one query comparing the lines.
- `src/mor/markets/source.py` decoded with msgspec structs instead of
  hand-parsed YAML/JSON with `isinstance` checks.

# Messaging rework: easier to follow, same guarantees

Status: 23-Nov-2026: §1, §3.1 and §3.2 built but not committed: they
break settlement's payout check, which relied on page order
(`plan-settlement-reconciliation.md` §1). That plan lands first; then
these, then §3.3. Amends `plan-messaging.md` and the worker loop in
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

### 3.3 One place per effect consumer

Today `prepare` dispatches on the consumer name, `call` and `complete`
on the call and answer types (`src/mor/messaging/consumers.py`), then
`payment_flow.complete` matches the answer again and each step re-reads
the id from the fact.

Proposed: each effect consumer is one function that reads top to bottom,
owning its transactions, and the worker calls it by one `match` on the
consumer name:

```python
async def run_capture(conn, http, environment, delivery, fact, now) -> EffectEnd:
    attempt_id = _authorised_id(fact)
    while True:
        async with conn.transaction():
            prepared = await prepare_capture(conn, attempt_id, now)
            if isinstance(prepared, NoCall):
                return await finish_delivery(conn, delivery, prepared.outcome, now)
        result = await registry.capture(http, environment, ...)  # no transaction
        async with conn.transaction():
            step = await complete_capture(conn, attempt_id, prepared, result, now)
            match step:
                case Done(outcome=outcome):
                    return await finish_delivery(conn, delivery, outcome, now)
                case RetryLater(reason=reason):
                    return await defer_delivery(conn, delivery, reason, now)
                case Again():
                    pass
```

- Gone: `consumers.prepare/call/complete`, the `EffectCall`/`EffectAnswer`
  unions, the `*Answer` records (the result is passed directly), the
  worker's `prepare_delivery`, `complete_delivery`, `run_effect` and
  `Prepared`, and `payment_flow.complete`'s second match.
- Kept: the worker's advisory lock around the call to `run_*`, and
  `finish_delivery`/`defer_delivery` (today `_finish_delivery`,
  `_defer`) as plain functions the consumer calls in its last
  transaction, so the effect and `handled_at` still commit together.
- Risks: the transaction boundaries are written in each consumer, so one
  could call a vendor inside a transaction; and a consumer could forget
  `finish_delivery`. Against the first, `registry` calls go through a
  check that the worker's connection has no open transaction; against
  the second, `EffectEnd` is `Handled | Deferred`, records only those two
  functions return.
- Size (shared conventions, Duplication): the dispatch and driver code
  that goes is about 230 lines (consumers.py 80, worker 75, payment
  call/complete 55, answer records); 11 drivers of about 20 lines each
  come in. About even; the gain is that one function shows a consumer's
  whole run. Converted first: `duty_rate_ask`, measured, then the rest;
  the echo consumers are transitional and go last (or with the echo
  flow).

### 3.4 Order of work

1. §3.1 and §3.2 together (worker, `aggregate_of`'s docstring, the
   `messaging.md` line, the worker loop in `plan-architecture.md` §5
   step 3, the concurrent case), then a soak run.
2. §3.3 on `duty_rate_ask`, line count compared, then the rest.

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

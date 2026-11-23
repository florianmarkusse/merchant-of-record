# Messaging rework: easier to follow, same guarantees

Status: open, 23-Nov-2026. Nothing below is designed or built yet except
§1. The next step is the design of §3, written up and agreed before any
code (open question in §2 first). Amends `plan-messaging.md` once agreed.

The user reviewed the messaging core and found it reliable but hard to
follow. The goal is the same guarantees (outbox, at-least-once,
idempotent consumers, no double vendor calls), with less to keep in mind
when following a fact through the code.

## 1. Done

- 23-Nov-2026: each fact record names its own `message_type` and
  `schema_version` as `ClassVar`s; `fact_type` is gone
  (`src/mor/messaging/facts.py`, checked by
  `tests/messaging/test_facts.py`).

## 2. Open decision: keep ordering per consumer and aggregate?

Today a delivery waits until every older unhandled delivery for the same
consumer and aggregate is handled (the `seq <` check in
`_open_delivery`, `src/mor/messaging/worker.py`). The shared
`messaging.md` says "ordering holds per aggregate".

The user's point: our consumers already accept facts in any order (a
webhook and a vendor's answer race, a `delivered` can arrive before an
`underway`), so the check may only add blocking. Two options:

- **Drop it.** Claiming becomes a plain `FOR UPDATE SKIP LOCKED` over due
  deliveries; a scheduled delivery holds nothing back. Needs: every
  consumer checked for order tolerance, and the fuzzers made to shuffle
  delivery order to prove it. Changes the shared `messaging.md`, so it is
  the user's call.
- **Keep it, fix the blocking.** Scheduled work (§3.1) stops sitting in
  the ordered queue, so a delivery due tomorrow cannot hold back one due
  now.

## 3. To design (one design: the parts depend on each other)

### 3.1 Scheduled deliveries block later ones

A delivery with `not_before` in the future (a poll's next turn, a session
expiry) is the oldest for its consumer and aggregate, so every later
delivery for that pair waits behind it. Storing many is cheap; the
blocking is the problem. It was worked around once by giving
`payment_events_wanted` its own consumer (comment in
`consumers_for`, `src/mor/messaging/routing.py`). Options: scheduled work
in its own table that appends the fact when due, or the ordering dropped
(§2).

### 3.2 The lock is on the fact's aggregate, not what the consumer changes

`run_one` (`src/mor/messaging/worker.py`) takes a session advisory
lock on the fact's aggregate. For an effect consumer (vendor call
between two commits) that is the shared rule's lock for work spanning
commits. But the aggregate is the fact's,
not what the consumer writes: `settlement_line_book` runs on
`order_paid` and books settlement lines. A local consumer runs in one
transaction and locks its rows anyway, so the advisory lock adds nothing
there. Together with `aggregate_of` this is the part the user found most
confusing. To design: what each consumer locks, stated next to it.

### 3.3 One dispatch per effect consumer

Following an effect consumer means three dispatches: `prepare`
dispatches on the consumer name, `call` and `complete` on the call or
answer type (`src/mor/messaging/consumers.py`), then
`payment_flow.complete` matches the answer again, and each step re-reads
the attempt id from the fact. Wanted: one place per consumer where its
prepare, call and complete are visible together, keeping the answer
types narrow. Measure the line count of any shared machinery before
proposing it (shared conventions, Duplication).

### 3.4 Smaller items

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

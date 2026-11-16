# Plan: slice 2, messaging

Status: 2a built 16-Nov-2026 (probe kept, inbox kept, msgspec added); 2b
and 2c are still to be designed. What building 2a showed is in §9. It details slice 2
of `plan-architecture.md` §14 and follows its §4 and §5; where this plan
says more, it does not change them unless §6 below names the change.

## 1. Scope, in three steps

Slice 2 is large, so it is built in three steps, each ending with the
checks clean and its part fuzzed:

- **2a. Facts, deliveries, the worker, local consumers.** The `outbox`
  and `outbox_delivery` tables, versioned fact records, the consumer
  table, the worker loop with the advisory lock per aggregate, and the
  `probe` flow (§3) to prove it: a Hypothesis state machine (layer 2) and
  a real-concurrency suite (layer 3). New dependency: msgspec.
- **2b. Effect consumers.** `prepare` / `call` / `complete`, the vendor
  request row, the duplicate guards (`vendor_constraint`, `compensable`,
  `none`), the exceptions list, and the fake-vendor frame (a Starlette app
  reached through httpx's ASGI transport). New dependencies: starlette,
  httpx. Designed in detail when 2a is done.
- **2c. Inbound.** `push`, `poll` and `file` on one path into a vendor
  inbox. Designed in detail when 2b is done.

This document details 2a.

## 2. Tables (`0002_messaging.sql`)

```sql
CREATE TYPE aggregate_type AS ENUM ('probe');
CREATE TYPE message_type   AS ENUM ('probe_started', 'probe_echoed');
CREATE TYPE consumer_name  AS ENUM ('probe_echo', 'probe_finish');

CREATE TABLE outbox (                          -- facts; append-only by trigger
  seq             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id              uuid NOT NULL UNIQUE,        -- uuid7
  aggregate_type  aggregate_type NOT NULL,
  aggregate_id    uuid NOT NULL,
  message_type    message_type NOT NULL,
  schema_version  int NOT NULL,
  payload         jsonb NOT NULL,              -- the fact record, by msgspec
  created_at      timestamptz NOT NULL,
  actor_kind      actor_kind NOT NULL,
  actor_id        text NOT NULL
);

CREATE TABLE outbox_delivery (                 -- one row per consumer of a fact
  message_id      uuid NOT NULL REFERENCES outbox (id),
  consumer        consumer_name NOT NULL,
  aggregate_type  aggregate_type NOT NULL,     -- copied, for per-aggregate order
  aggregate_id    uuid NOT NULL,
  seq             bigint NOT NULL,             -- the fact's outbox seq
  not_before      timestamptz NOT NULL,        -- time only triggers a retry
  attempts        int NOT NULL DEFAULT 0,
  last_error      text,
  handled_at      timestamptz,
  PRIMARY KEY (message_id, consumer)
);
CREATE INDEX outbox_delivery_pending
  ON outbox_delivery (consumer, aggregate_type, aggregate_id, seq)
  WHERE handled_at IS NULL;

CREATE TABLE inbox (                           -- what each consumer has handled
  consumer    consumer_name NOT NULL,
  message_id  uuid NOT NULL REFERENCES outbox (id),
  handled_at  timestamptz NOT NULL,
  PRIMARY KEY (consumer, message_id)
);
```

- The payload is jsonb, not columns: facts of every type share the
  outbox, and each type's shape is a typed record checked at both ends
  (§4). This is the one place a serialized document is the right shape.
- The inbox is kept although `outbox_delivery` could dedupe alone
  (decided 16-Nov-2026). The consumer's duplicate check goes through the
  inbox (insert, a conflict means already handled), exactly as it will
  when a broker delivers instead of our table; then only the transport
  changes. The inbox row and `handled_at` are written in one transaction
  with the effect, so they cannot disagree. Cost: one small insert per
  handled delivery.
- `handled_at` is set once, under the delivery row's lock, in the same
  transaction as the consumer's effect. A trigger refuses to change it
  back to NULL, or to change anything but `not_before`, `attempts`,
  `last_error` and `handled_at`.

## 3. The probe: the two-step flow that proves the machinery

The plan calls for a toy flow. Instead of a test-only toy, the probe is
real and stays: a tiny aggregate that walks through two local consumers.

```
probe (id, state: started | echoed | finished, created_at, finished_at?)

start_probe (CLI)  → probe 'started'  + fact probe_started
probe_echo         ← probe_started:  lock probe, started → echoed,
                                      fact probe_echoed
probe_finish       ← probe_echoed:   lock probe, echoed → finished
```

Why keep it: after a deploy, `python -m mor.probe` starts one
and waits for `finished`, which proves the worker runs and reaches the
database. That is the health check slice 3's deploy needs. A test-only toy
would need test-only enum values and tables in `mor_test`, which the real
migrations would then not match.

## 4. Code (`src/mor/messaging/`)

- `facts.py`: one frozen msgspec `Struct` per fact and version
  (`ProbeStartedV1`). `encode(fact) -> (MessageType, version, bytes)` and
  `decode(message_type, version, bytes) -> Fact | UndecodableFact` with an
  exhaustive `match`. A new shape is a new version, never an edit.
- `routing.py`: `consumers_for(message_type) -> tuple[ConsumerName, ...]`,
  the one table of who gets which fact, an exhaustive `match`.
- `store.py`: `append_fact(conn, aggregate, fact, actor, now)` writes the
  outbox row and one delivery row per consumer in the caller's
  transaction. Never commits.
- `consumers.py`: `handle(conn, consumer, message, now) -> Handled |
  Stale`, the one dispatch table (`match` on the consumer). `Stale` means
  the aggregate has already moved past this fact (a redelivery): the
  delivery is marked handled with no effect.
- `worker.py`: the loop of plan-architecture §5, on a dedicated
  connection:
  1. find candidates: per (consumer, aggregate), the oldest unhandled
     delivery, kept if its `not_before` has passed; oldest first;
  2. `pg_try_advisory_lock` on the aggregate (a 64-bit hash of type and
     id; a collision only makes two aggregates wait for each other, never
     breaks correctness); not acquired: next candidate;
  3. in one transaction: re-read that delivery `FOR UPDATE`, check it is
     still the oldest unhandled one and still due, decode, `handle`, set
     `handled_at`, commit;
  4. release the advisory lock.

  A consumer that raises is a bug: the transaction rolls back, a second
  short transaction records `attempts`, `last_error` and a later
  `not_before` (backoff, capped), and the loop goes on. The aggregate's
  later facts wait behind it, which keeps the order; a stuck delivery
  shows up in a query of `last_error IS NOT NULL`.
- `python -m mor.messaging.worker`: runs the loop. It sleeps a short while only
  when nothing is due (time only triggers). LISTEN/NOTIFY to wake at once
  can come later.

## 5. Testing

- Layer 1: `decode(encode(fact))` round-trips for every fact; any bytes
  decode to a fact or `UndecodableFact`.
- Unit: `append_fact` writes one delivery per routed consumer; a
  delivery is handled once; order per aggregate holds while two
  aggregates proceed independently; a raising consumer is retried after
  `not_before` and blocks only its own aggregate.
- Layer 2 (`tests/flow/`): a Hypothesis state machine against `mor_test`.
  Actions: start a probe; run one delivery fully; run a delivery that
  crashes before commit; hand the same delivery to the consumer twice;
  move the clock. Model: the state each probe should be in. After every
  action: nothing raised outside the planted crash, every probe in a state
  the model allows, no advisory lock held. After draining: every probe
  `finished`, exactly one `probe_echoed` per probe, no unhandled delivery.
- Layer 3: several worker tasks on their own connections drain a few
  hundred probes at once; the same end checks, plus "two workers never
  ran one aggregate at the same time", recorded by each step.
- Check the fuzzer: remove the `FOR UPDATE` re-read and the advisory
  lock, one at a time, and confirm a test fails.

## 6. Changes to the earlier plans

| Plan | Change | Why |
|---|---|---|
| skeleton §7: `published_at` on the outbox | dropped; the outbox is append-only | delivery state lives in `outbox_delivery` |
| architecture §14: a "toy" flow | the `probe`, kept as the deploy health check | real migrations, no test-only schema |

## 7. Rules this applies

Transactional outbox (facts and state commit together); facts in the past
tense with a schema version; ordering per aggregate; idempotent consumers
through a unique key and a row lock; a lock guards, time only triggers
(`not_before`, the idle sleep); a session advisory lock for the worker's
claim, freed when its connection dies; one explicit table for routing and
one for dispatch; errors as values (`Stale`, `UndecodableFact`), raise
only on bugs.

## 8. Decisions (16-Nov-2026)

- The probe is the toy flow and stays as the deploy health check.
- The inbox is kept (§2), for the reason given there.
- msgspec 0.22.0 is added (BSD-3-Clause, Python 3.14 wheels).

## 9. What building 2a showed (16-Nov-2026)

- Each guard was checked by planting its bug. Caught: no `handled_at`
  check (unit, concurrency suite, fuzzer); a consumer that is not
  idempotent (unit, fuzzer); the advisory lock never taken (unit, the
  "busy" test); no per-aggregate order check (unit only).
- The `FOR UPDATE` on the delivery row is redundant today: the advisory
  lock already keeps a second worker off the aggregate, so removing it
  fails no test. It stays as a second guard for any future path that runs
  a delivery without the advisory lock (an admin "retry now").
- The fuzzer cannot see an order violation in the probe, because its two
  echo deliveries give the same end state in either order. The unit test
  covers order; a flow where order changes the outcome must add it to the
  fuzzer's model.
- 20 runs of the fuzzer in a row passed: a dead connection's advisory
  lock was always gone by the next check.
- End to end on `mor_dev`: `python -m mor.messaging.worker` plus
  `python -m mor.probe` finishes a probe; with the worker stopped, the
  probe exits 1 after its wait.

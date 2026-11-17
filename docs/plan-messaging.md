# Plan: slice 2, messaging

Status: 2a built 16-Nov-2026 (probe kept, inbox kept, msgspec added);
LISTEN/NOTIFY added 17-Nov-2026. What building 2a showed is in §9. 2b is
designed in §10, confirmed and built 17-Nov-2026; what building it showed
is in §11. 2c is still to be designed. It details slice 2
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
  and httpx or its maintained fork httpx2 (§10.8). Designed in §10.
- **2c. Inbound.** `push`, `poll` and `file` on one path into a vendor
  inbox. Designed in detail when 2b is done.

This document details 2a in §2 to §9 and 2b in §10.

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
- `python -m mor.messaging.worker`: runs the loop. When a drain ran
  nothing, the worker waits for a NOTIFY on `outbox_delivery`, for the
  next retry among deliveries that could run next, or at most 5 s, the
  fallback poll (added 17-Nov-2026, `0003_delivery_notify.sql`). A
  statement trigger on `outbox_delivery` sends the NOTIFY, so no code
  path can forget it; it is delivered only on commit and has no payload.
  The fallback stays because a notification sent while no worker listens
  is not kept. Watch `pg_notification_queue_usage()`: if the queue ever
  filled (a listener stuck in a long transaction), commits that write
  deliveries would fail. Workers never hold long transactions.

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
- For 2b (decided 16-Nov-2026): the effect machinery is proven on a
  dev/test-only aggregate that calls a fake "echo" vendor. It is
  transitional: once the fake PSP exercises the same machinery (slice 5),
  the echo vendor and its aggregate are removed, unless they serve
  something else by then. The fakes' own tables get a separate set of
  migrations, applied only outside prod. starlette and httpx are
  approved as dependencies.

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
- Scaling is by running more worker processes: aggregates run in
  parallel, one aggregate on one worker at a time. Every worker asks for
  the same oldest candidates, so at many workers most lock attempts
  return "busy": correct but wasted round trips. If it ever shows, start
  each worker at a random offset or split aggregates by a hash of the id.
- 20 runs of the fuzzer in a row passed: a dead connection's advisory
  lock was always gone by the next check.
- With LISTEN/NOTIFY, a probe on `mor_dev` finishes in under 0.3 s,
  start of the CLI included. Planted bugs: a worker that never listens,
  and a next-retry query over every open delivery (which would make an
  idle worker spin), each fail a test.
- End to end on `mor_dev`: `python -m mor.messaging.worker` plus
  `python -m mor.probe` finishes a probe; with the worker stopped, the
  probe exits 1 after its wait.

## 10. Step 2b: effect consumers (design, 17-Nov-2026)

### 10.1 The echo flow

A dev/test-only aggregate, `echo_job`, proves the machinery on a fake
"echo" vendor that stores a message and returns an id. It is transitional
(§8): it goes once the fake PSP uses the same machinery (slice 5).

```
echo_job: requested → echoed | refused | needs_person

create_echo_job (test or dev CLI) → 'requested' + fact echo_job_requested
echo_send (effect consumer) ← echo_job_requested:
    prepare  → request row 'sent'           (commit)
    call     → the echo vendor, no transaction open
    complete → echoed + echo_job_echoed   |  refused + echo_job_refused
             | still requested, retry later (Unavailable, OutcomeUnknown)
             | needs_person + echo_job_needs_person (guard `none`, §10.4)
```

The three completion facts have no consumer yet: they are what a later
stage would react to. The test that every message type has a consumer
goes; a fact with no consumer writes no delivery.

### 10.2 Tables (`0004_echo.sql`)

```sql
CREATE TYPE echo_provider AS ENUM
  ('fake_keyed', 'fake_cancellable', 'fake_plain');   -- one per guard, §10.4
CREATE TYPE echo_job_state AS ENUM
  ('requested', 'echoed', 'refused', 'needs_person');
CREATE TYPE echo_operation AS ENUM ('send', 'cancel');
CREATE TYPE vendor_request_state AS ENUM
  ('sent', 'succeeded', 'refused', 'not_sent');

CREATE TABLE echo_account (
  id         uuid PRIMARY KEY,
  provider   echo_provider NOT NULL,
  base_url   text NOT NULL,
  timeout_ms int NOT NULL
);

CREATE TABLE echo_job (
  id              uuid PRIMARY KEY,
  account_id      uuid NOT NULL REFERENCES echo_account (id),
  message         text NOT NULL,
  state           echo_job_state NOT NULL,
  vendor_echo_id  text,
  refusal         text,
  created_at      timestamptz NOT NULL,
  updated_at      timestamptz NOT NULL
);

-- One row per call we may have made: the "we are about to ask" mark.
CREATE TABLE echo_request (
  id               uuid PRIMARY KEY,
  echo_job_id      uuid NOT NULL REFERENCES echo_job (id),
  operation        echo_operation NOT NULL,
  number           int NOT NULL,                 -- 1, 2, … per operation
  idempotency_key  text NOT NULL UNIQUE,         -- kept for good
  state            vendor_request_state NOT NULL,
  vendor_echo_id   text,                         -- what a cancel targets
  sent_at          timestamptz NOT NULL,
  completed_at     timestamptz,
  UNIQUE (echo_job_id, operation, number)
);
```

- A table per port, as plan-architecture §7 does for `courier_request`;
  the PSP and courier slices add their own, with the same shape.
- The exceptions list is a state, not a table: a job in `needs_person`
  with its request still `sent`. An admin page later lists such rows per
  port. A generic exceptions table would need a foreign key to any port's
  request table, which SQL cannot express.
- Removing the echo flow later means dropping its tables and leaving its
  values in the shared enum types (`message_type`, `consumer_name`,
  `aggregate_type`): PostgreSQL cannot drop an enum value without
  rebuilding the type. The `Literal`/`StrEnum` in code loses them; the
  dead values stay in the database, harmless. Rebuilding the types is
  possible later if they bother us.

### 10.3 The effect consumer in code

```
mor/echo/port.py       EchoAccount, SendRequest, CancelRequest, and the
                       result union: Sent(echo_id) | Refused(reason)
                       | Unavailable | OutcomeUnknown; FindResult
mor/echo/registry.py   send / cancel / find_by_reference: an exhaustive
                       match on the provider, refusing a fake in prod
mor/echo/fake_client.py   httpx client for the fake's made-up API
mor/echo/capabilities.py  the duplicate guard per provider and operation
mor/echo/flow.py       create_echo_job; prepare, call, complete
```

- The consumer table gains a kind per consumer, `local` or `effect`,
  as an exhaustive `match`. The worker runs an effect consumer as plan-
  architecture §5 describes: under the aggregate's advisory lock,
  transaction 1 `prepare`, no transaction during `call`, transaction 2
  `complete`, which also marks the delivery handled or sets a later
  `not_before`.
- The HTTP client is an argument to `call` (`http: AsyncClient`): the
  worker passes a real one; tests pass one whose transport is the fakes'
  app in-process (ASGI). Explicit, no injection.
- `prepare(conn, fact, now) -> NoCall | SendCall | FindCall`: lock the
  job; if it is no longer `requested`, `NoCall`. Otherwise find the
  latest send request: none, or `not_sent` → create or reuse it, mark it
  `sent`. A `sent` one with no result means an earlier run died
  mid-call or got no answer: the guard decides (§10.4).
- `call` maps the transport to the result union:
  - connection refused or a DNS failure: `Unavailable` (provably never
    left);
  - a timeout, a dropped connection or a 5xx: `OutcomeUnknown` (the
    vendor may have done the work);
  - a documented refusal (4xx with an error code): `Refused`.

  The client bounds the whole call with `asyncio.timeout(account
  timeout)`, which works the same on a socket and in-process.
- `complete(conn, fact, call, result, now)`: lock the job, apply the
  result to the state as it is now: `Sent` → `echoed` and the request
  `succeeded`; `Refused` → `refused`; `Unavailable` → the request becomes
  `not_sent` and the delivery is retried later; `OutcomeUnknown` → the
  request stays `sent` and the delivery is retried later.

### 10.4 The three guards, offered by the fake

| Provider | What the fake promises | After a `sent` with no result |
|---|---|---|
| `fake_keyed` (`vendor_constraint`) | a request with a known `Idempotency-Key` returns the first answer, for good | send again with the same key |
| `fake_cancellable` (`compensable`) | nothing on resend, but an echo can be cancelled | send again; then find by our reference, and cancel every echo but the one we keep (a `cancel` request each) |
| `fake_plain` (`none`) | nothing, no cancel | never resend; find by our reference: found → echoed; not found → `needs_person` |

Our reference on every send is the job id, so `find_by_reference` works on
every flavour. Each provider's guard is one constant in
`capabilities.py`, with its test.

### 10.5 The fake vendor

- `mor/fakes/echo.py`: a Starlette app, routes under
  `/_fake/echo/{flavour}/…`. A made-up API: `POST /echoes` (body: our
  reference and message; `Idempotency-Key` honoured only by `keyed`),
  `GET /echoes?reference=…`, `POST /echoes/{id}/cancel` (only
  `cancellable`).
- Its state lives in its own schema, `fake_vendor`, on its own
  connection, as a third party's would: `fake_vendor.echo` (every echo it
  ever made, cancelled or not) and `fake_vendor.echo_fault` (scripted
  faults for the next call).
- Faults, set through `POST /_fake/echo/faults` or a test helper:
  `fail_500` (before the work), `fail_500_after_work`,
  `timeout_after_work` (does the work, commits, then hangs past the
  client's timeout), `refuse`. "Connection refused" cannot come from an
  app; tests get it from a transport wrapper in `tests/` that refuses the
  next request: a fake of the network, not of our code.
- The fake's tables come from a separate set of migrations,
  `migrations_fake/`, applied by the same runner on a connection whose
  `search_path` is `fake_vendor`, so its own `schema_migration` record
  lives there and no SQL is composed. `python -m mor.fakes.migrate`
  refuses to run when `ENVIRONMENT=prod`. Tests apply both sets.
- The fake's routes are not mounted anywhere yet: there is no web app
  until a later slice. In tests the app is called in-process; mounting
  it under `/_fake/` comes with the dev server.

### 10.6 Testing

- Unit, through the worker: each result kind; each guard after a crash
  between `prepare` and `call` and between `call` and `complete`; a
  cancelled duplicate; `needs_person`; a fake in prod is refused.
- Layer 2: a state machine like the probe's, over echo jobs on all
  three providers. Actions: create a job; run a delivery fully; run
  `prepare` and stop; run `prepare` and `call` and stop (each a crash at
  a commit boundary); script a fault for the next call; refuse the next
  connection; move the clock. Checks after every action: no step raised,
  every job in a state its provider allows, no advisory lock held, the
  fake never holds two live echoes for one job on `keyed` or `plain`.
  After draining with faults off: every job is `echoed`, `refused` or
  (on `plain` only, and only after a lost answer) `needs_person`; on
  `cancellable` exactly one live echo per echoed job; nothing unhandled.
- Check the fuzzer: plant "resend on `plain`" and "skip the cancel on
  `cancellable`", and confirm it fails.

### 10.7 Rules applied

A lock guards, time only triggers (the timeout yields `OutcomeUnknown`,
a value that decides nothing); the duplicate guard as capability data;
one module per vendor and one registry with an exhaustive `match`; a
fake that behaves like a vendor over HTTP, with faults; errors as values
(the result union); a table per thing; transitional code marked with
what must be true before it goes.

### 10.8 Dependencies

- starlette 1.10.0 (BSD-3-Clause, Python 3.14), for the fake's app now
  and our web layer later.
- httpx: its last release, 0.28.1, dates from 06-Dec-2024 and has no
  Python 3.14 classifier. `httpx2` 2.16.2 (BSD-3-Clause, Python 3.14,
  released 16-Nov-2026) is a fork of httpx 0.28.1 maintained by the
  Pydantic organisation, "with HTTPX itself seeing limited activity". Its
  public API is httpx's under the import name `httpx2`. fruggals already
  uses it, and Starlette's own extras list it. Proposed: httpx2, with
  the conventions' "httpx for outgoing HTTP" read as this fork.

## 11. What building 2b showed (17-Nov-2026)

Built as §10 designs, with these differences:

- The echo job has one more state, `deduplicating`, and a request row
  counts its `sends`. Only a request sent more than once on a
  `compensable` vendor can have left duplicates; such a send, answered or
  refused, moves the job to `deduplicating`. There it finds every echo by
  our reference, cancels each extra one by one (a `cancel` request
  each), and ends `echoed` with the echo it keeps, or `refused` if none
  is live. A resend that is refused keeps an echo an earlier send made.
- `complete` may answer `Again` (more to ask now); the worker then runs
  `prepare` again under the same lock, up to 50 rounds.
- The fake's keyed flavour replays a known key even when a `refuse`
  fault is scripted: refusing a replay would break its own promise.
- `consumers.py` has four dispatch tables: `handle` for local consumers,
  `prepare`, `call` and `complete` for effect consumers.
- The worker takes the HTTP client and the environment as arguments;
  tests of flows that never call out pass a client that fails any
  request.

Checked by planting each bug the guards exist to stop:

| Planted bug | Caught by |
|---|---|
| guard `none` resends after a lost answer | unit, fuzzer |
| duplicates on `compensable` never cancelled | unit, fuzzer |
| a `compensable` resend trusted without a lookup | unit, fuzzer |
| no `sent` mark before the call | unit, fuzzer |
| a refused connection treated as an unknown outcome | unit; the fuzzer only after its model was tightened (below) |
| no advisory lock around an effect | concurrency suite |

- The fuzzer first allowed `needs_person` for any `plain` job, so the
  fifth bug (which ends there) passed. Its model now allows it only after
  a crash on that job or a fault that loses answers scripted for `plain`.
- For effect consumers the advisory lock is essential, unlike for the
  probe: without it a second worker sees a `sent` request with no answer
  in the middle of someone else's call.
- 15 runs of the echo fuzzer in a row passed on the correct code.
- The fake's routes are not mounted anywhere yet (no web app exists), so
  the echo flow runs only in tests. Mounting under `/_fake/` comes with
  the dev server.

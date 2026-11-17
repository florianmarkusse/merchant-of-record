# Plan: slice 2, messaging

Status: 2a built 16-Nov-2026 (probe kept, inbox kept, msgspec added);
LISTEN/NOTIFY added 17-Nov-2026. What building 2a showed is in §9. 2b is
designed in §10, confirmed and built 17-Nov-2026; what building it showed
is in §11. 2c is designed in §12, confirmed 17-Nov-2026 with the
`none` mode and manual events added, and built the same day; what building
it showed is in §13. It details slice 2
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

## 12. Step 2c: inbound (design, 17-Nov-2026)

### 12.1 What the echo vendor now reports

After making an echo, the fake vendor later reports what happens to it,
as a carrier reports a parcel: `confirmed`, then `archived`. Events are
released by an explicit call (a test, a dev page later), never by a
timer, and can be released twice, late or out of order. This rehearses
shipment tracking (plan-architecture.md §9) on a flow that moves no money.

Each echo account has an event mode, capability data on the account:

| Mode | How events arrive | Started by |
|---|---|---|
| `push` | the vendor POSTs a signed webhook carrying the events | the vendor |
| `push_thin` | a signed webhook carrying an event id and the echo id, not what happened; we fetch its events | the vendor |
| `poll` | we fetch one page of events per job | a scheduled fact per job, until a final status |
| `file` | a report file of all events since the last one, per account | a scheduled fact per account |
| `none` | nothing: the vendor offers no events | only events recorded by hand |

**Any event can be recorded by hand, whatever the mode** (decided
17-Nov-2026): a person who sees on the vendor's website that something
happened records it, with themselves as the actor. It enters at the same
fact as a decoded vendor event (`echo_vendor_event_received`, source
`manual`), so it goes through the same apply step, unique key, status and
milestones. For a `none` account this is the only way its status moves.

Deferred to the shipment slice, where it has a business meaning (when the
merchant's money may be released): a check that starts when no event
arrived for N days, in any mode, and opens a case for a person. Time only
starts that check; a person or a rule decides.

How the fakes become visible on a local server (one app with the fakes
under `/_fake/`, a seed with one account per guard and mode, a page to
release events by hand) is deferred to slice 3's test setup, as
plan-architecture §6 and §14 have it.

### 12.2 One path for all four

```
push, push_thin ─▶ webhook handler: verify signature ┐
poll            ─▶ effect consumer: fetch a page     ├─▶ echo_inbox row (raw bytes,
file            ─▶ effect consumer: fetch the file   ┘    unique per account + sha256)
                                                          + fact echo_inbox_stored
                                                                │
                        consumer echo_inbox_decode (local, per account):
                        decode the raw bytes with msgspec into VendorEvent
                        records; for each, fact echo_vendor_event_received
                        (thin: fact echo_events_wanted, which starts a poll)
                                                                │
                        consumer echo_event_apply (local, per job):
                        insert into echo_job_event (unique per job + vendor
                        event id); status = derive_status(all events);
                        first time a status is reached: echo_job_milestone
                        row (unique) and fact echo_job_confirmed / _archived
```

- Every mode stores the raw bytes first, commits and stops; a webhook
  then answers 200. Decoding is a consumer's job, so a decoder bug never
  loses an event: the raw row stays, and a fixed decoder re-runs it.
- **The guard against duplicates is the vendor's id, never the bytes**
  (decided 17-Nov-2026). Every event has the vendor's event id, and the
  unique key on (job, vendor event id) records it once, whatever mode
  brought it. Every webhook notice carries an id too (a thin one the id
  of the event behind it), so two notices are never the same bytes; a
  report file has its name, which is also our cursor; a poll page is a
  read and needs none.
- The inbox's unique sha256 of the body only means "these exact bytes are
  stored already": a resend of one webhook, a page fetched twice, two
  empty report files. It decides nothing about events. Bytes that differ
  for one event (Adyen resends with a new `eventDate`) are stored twice
  and stopped by the event key; a vendor whose distinct notices could be
  the same bytes would lose the second, so a vendor module never relies
  on the hash (the fake's thin notices did, §13).
- Two hops, because one step locks one aggregate (plan-architecture §4):
  the inbox row belongs to the account (one webhook may carry events of
  several jobs), each event belongs to its job.
- `derive_status(events: frozenset[VendorEvent]) -> EchoVendorStatus` is
  pure: `none` < `confirmed` < `archived`, decided by the set, not the
  order of arrival. Duplicates and out-of-order delivery cannot corrupt
  it; an `archived` before its `confirmed` reaches `archived`, and the
  `confirmed` arriving later emits no milestone backwards.

### 12.3 Tables (`0005_echo_inbound.sql`)

```sql
CREATE TYPE echo_event_mode AS ENUM ('push', 'push_thin', 'poll', 'file', 'none');
CREATE TYPE echo_event_source AS ENUM ('vendor', 'manual');
CREATE TYPE echo_event_kind AS ENUM ('confirmed', 'archived');
CREATE TYPE echo_vendor_status AS ENUM ('none', 'confirmed', 'archived');
ALTER TABLE echo_account ADD COLUMN event_mode echo_event_mode NOT NULL
  DEFAULT 'push';                    -- the default only fills existing rows
ALTER TABLE echo_account ADD COLUMN webhook_secret text NOT NULL DEFAULT '';
ALTER TABLE echo_job ADD COLUMN vendor_status echo_vendor_status NOT NULL
  DEFAULT 'none';

CREATE TABLE echo_inbox (            -- raw, as received; append-only
  id           uuid PRIMARY KEY,
  account_id   uuid NOT NULL REFERENCES echo_account (id),
  mode         echo_event_mode NOT NULL,
  body_sha256  text NOT NULL,
  body         bytea NOT NULL,
  received_at  timestamptz NOT NULL,
  UNIQUE (account_id, body_sha256)
);

CREATE TABLE echo_job_event (        -- decoded, per job; append-only
  echo_job_id      uuid NOT NULL REFERENCES echo_job (id),
  vendor_event_id  text NOT NULL,
  kind             echo_event_kind NOT NULL,
  occurred_at      timestamptz NOT NULL,
  source           echo_event_source NOT NULL,
  inbox_id         uuid REFERENCES echo_inbox (id),   -- vendor events only
  recorded_at      timestamptz NOT NULL,
  PRIMARY KEY (echo_job_id, vendor_event_id),
  CHECK ((source = 'vendor') = (inbox_id IS NOT NULL))
);
-- Who recorded a manual event is the actor on its outbox fact.

CREATE TABLE echo_job_milestone (    -- a status first reached; once each
  echo_job_id  uuid NOT NULL REFERENCES echo_job (id),
  status       echo_vendor_status NOT NULL,
  reached_at   timestamptz NOT NULL,
  PRIMARY KEY (echo_job_id, status)
);

CREATE TABLE echo_report_cursor (    -- file mode: the last report fetched
  account_id   uuid PRIMARY KEY REFERENCES echo_account (id),
  last_report  text NOT NULL
);
```

The webhook secret sits on the account row because these accounts are
fakes with made-up secrets. A real port keeps a `credentials_ref` naming
a key in `.env` (AGENTS.md: secrets never in the database).

### 12.4 New facts and consumers

| Fact (v1) | Aggregate | Consumer |
|---|---|---|
| `echo_inbox_stored` | account | `echo_inbox_decode` (local) |
| `echo_vendor_event_received` | job | `echo_event_apply` (local) |
| `echo_events_wanted` (thin push) | job | `echo_poll` (effect) |
| `echo_poll_due` (scheduled) | job | `echo_poll` (effect) |
| `echo_report_due` (scheduled) | account | `echo_report_fetch` (effect) |
| `echo_job_confirmed`, `echo_job_archived` | job | none yet |

- `echo_job_echoed` gains a consumer, `echo_tracking_start` (local): on a
  `poll` account it appends the first `echo_poll_due`.
- A scheduled fact is an ordinary fact whose deliveries carry a later
  `not_before`: `append_fact` gains a `deliver_at` argument. Time only
  starts the work; the poll consumer decides under the job's lock
  whether a poll is still wanted (not if the status is final).
- `echo_poll` fetches one page and stores it in the inbox; then, unless
  the status is final, it appends the next `echo_poll_due` at now plus
  the account's interval. `echo_report_fetch` looks for files per
  account, with the cursor saying which report comes next. A look reads
  at most one report and then makes the next look due at once; a look
  that finds nothing makes the next one due after the interval. A
  backlog is read as looks that follow each other, each two steps.
- Polls and report fetches only read at the vendor, so their guard is
  "resend freely": a fetch repeated after a crash stores the same bytes,
  and the inbox key absorbs it. No request row is needed for them.

### 12.5 Our first web handler

- `mor/web/main.py` holds the route table (shared conventions: the whole
  table in one file); `mor/web/webhooks.py` holds
  `POST /webhooks/echo/{account_id}`: read the body, verify the HMAC-SHA256
  signature header with the account's secret (stdlib `hmac`, constant-time
  compare), insert the inbox row and its fact in one transaction, commit,
  answer 200. A bad signature: 401, nothing stored. A body already stored:
  200, nothing new. The handler never decodes events.
- Outside prod the fakes' routes are mounted in the same app under
  `/_fake/` (plan-architecture §6). A dev server with uvicorn is not
  part of 2c; tests call the app in-process.
- Webhooks take no cookies, so the conventions' cross-site rule is met by
  the signature, not a SameSite cookie: a forged POST has no valid
  signature.

### 12.6 The fake vendor's side

- The fake knows vendor-side accounts by a path segment:
  `/_fake/echo/{flavour}/{vendor_account}/…`, part of our account's
  `base_url`. A webhook subscription (URL, secret, full or thin) is
  registered per vendor account, as on a vendor's dashboard. The fake
  pushes only to subscribed accounts; it always offers poll and reports.
  Which mode we use is our account's `event_mode`.
- `fake_vendor.echo_event`: per echo, its pending and released events,
  and for push accounts whether the webhook went through.
- `POST /_fake/echo/{flavour}/events/release` with an echo id: releases
  that echo's next event (or a named one, so a test can release
  `archived` first). A push account's webhook goes out at once to the
  account's registered URL, through an HTTP client the fake app is given
  (in tests: our app, in-process). An answer other than 200 is recorded
  and `POST /_fake/echo/{flavour}/events/redeliver` sends it again.
- Faults: send a webhook twice; send it with a bad signature; poll and
  report requests that answer 500 or time out.
- A thin webhook carries the event's id, the echo id and our reference,
  as real thin webhooks carry an id (Stripe's thin events: the event id
  and the changed object).
- `GET /_fake/echo/{flavour}/echoes/{id}/events` (poll and thin),
  `GET /_fake/echo/{flavour}/reports/{name}` (file: all events released
  since the previous report, CSV).

### 12.7 Testing

- Layer 1: `derive_status` gives the same answer for every order and
  duplication of a set of events; every decoder (webhook JSON, poll page,
  report CSV) turns any bytes into events or a typed error.
- Unit, per mode: an event released at the vendor ends as a milestone,
  once; the same webhook twice stores one row; a bad signature stores
  nothing; a thin push leads to a poll; polling stops at `archived`;
  an undecodable body stays in the inbox, its delivery failing with the
  error, until a fixed decoder handles it.
- Layer 2: the echo fuzzer gains actions to release the next or a later
  event, send a webhook twice, fail a webhook, fail a poll, and run the
  scheduled facts. It draws each account's mode. After draining: every
  released event is recorded once, every job's status equals
  `derive_status` of what the vendor released, each milestone exists
  once, and no poll is left scheduled for a final job.
- Check the fuzzer: plant "status from the latest event instead of the
  set" and "no unique key on job events", and confirm it fails.

### 12.8 Build order

1. The inbox, the decode and apply consumers, `derive_status`,
   milestones, push with the webhook handler (and the route table), the
   `none` mode and manual events.
2. Poll and thin push, with scheduled facts.
3. File reports.

Each ends with the checks clean and its part fuzzed. No new dependency:
signatures use the standard library.

## 13. What building 2c showed (17-Nov-2026)

### Step 1: inbox, push, `none`, manual events

- The web app takes a `clock` argument (`make_app(settings, fakes,
  clock)`): the webhook handler stamps what it stores with it, so tests
  and the fuzzer control time there too. Serving passes
  `datetime.now(UTC)`.
- An event naming a job its account never sent fails its delivery
  (`UnknownReference`), which holds that account's later inbox rows until
  someone looks. Acceptable for the fake; a real port turns it into an
  exception row for a person instead.
- Tests that simulate a crashed worker wait until its server process is
  gone (`tests/helpers.py: crash`): PostgreSQL frees the advisory lock as
  that process exits, a moment after the client closes, and a check in
  that moment saw the lock. Production needs no wait: a worker meeting the
  lock then gets "busy" and tries later.
- The fuzzer found a bug in itself: its crash action ran `prepare` on any
  delivery, which raises for the new local consumers, and left the
  connection open holding the lock. Each consumer kind now crashes its
  own way.

Planted bugs:

| Planted bug | Caught by |
|---|---|
| status from the latest event, not the set | unit, fuzzer |
| signature not checked | unit |
| milestone without its unique key | nothing: a second guard (below) |
| a repeated event not answered `Stale` | nothing: not a guard (below) |
| a milestone fact on every event (both guards gone) | unit, fuzzer |

- A milestone fact is emitted only when the status moves up, under the
  job's row lock, so its unique key is a second guard, as the delivery
  row lock was in §9. The early `Stale` for a repeated event is not a
  guard at all: the event table's key keeps one row and the status comes
  out the same. The fuzzer now checks after every step that no milestone
  fact exists twice.

### Step 2: polling and thin webhooks

- A poll asks for events by our reference (the job id), not the vendor's
  echo id: a thin webhook can arrive before we recorded the vendor's
  answer, when we do not know its echo id yet.
- A poll stores the page raw in the inbox and decides on its next turn
  from the status before that page is applied, so it schedules one poll
  more than needed; that one finds the job final and stops.
- `echo_job_echoed` now has a consumer (`echo_tracking_start`), so every
  echoed job gets one more delivery, a no-op outside `poll` accounts.
- Planted bugs: polling that never stops at the final status (unit,
  fuzzer); a scheduled fact delivered at once instead of at its time
  (unit).

### Step 3: report files

- A `file` account looks for reports forever: each look lists the
  reports after its cursor, fetches and stores them one per `Again` round
  under the account's lock, then schedules the next look. The fuzzer
  therefore drains until a round of only report looks changes nothing,
  and then expects exactly one scheduled look per `file` account.
- The decoder property test found a real bug: the report decoder raised
  `csv.Error` on bytes such as `b"\r\x00"` instead of returning a typed
  error. Fixed, and pinned as an `@example`.
- Planted bugs: a report schedule that stops after one look, a cursor
  that never moves (unit, fuzzer).

### The fuzzer failure on correct code, explained (17-Nov-2026)

Once, a job ended `confirmed` although the vendor had released `archived`
too. The cause was a real bug in `push_thin`: a thin notice carried only
`{echo_id, reference}`, so every notice for one echo was the same bytes.
The inbox took the second for a resend of the first, no fetch followed (a
thin account has no poll schedule), and every event after the first was
lost. A two-step unit test shows it every time; driving the fuzzer's own
class through the same order gives the failure as first seen. The full
`push` path held up under 1,500 push-only fuzzer sequences.

Why it looked flaky: random single deliveries seldom finish one event
(decode, then fetch) before the vendor releases the next, so it was found
once in about 30 runs. Its saved example stopped replaying when the
fuzzer file was edited for debugging. The earlier note that the job was a
`push` job was a guess from timing, which a thin job fits as well; the
mode was not in the failure message then (it is now).

Fixes:

- Thin notices carry the id of the event behind them, as real ones do;
  the rule that the vendor's id, never the hash, is the duplicate guard
  is in §12.2. Pinned by
  `test_each_thin_webhook_of_one_echo_leads_to_its_own_fetch`.
- The fuzzer gains `the_worker_catches_up`: everything due, until
  nothing is. With the thin bug put back, the long run finds it in about
  four minutes and shrinks it to five steps (thin job, catch up, release,
  catch up, drain); the normal 30-sequence budget does not find it
  reliably, so the unit test is its guard in every run.

A second bug, found by the long run: a `file` account with 25 or more
waiting reports could not finish a look. A look read every waiting report
in one delivery, two steps each, past the worker's 50-step limit, and the
delivery failed as a bug (its progress kept, so a retry went on). A look
now reads at most one report and makes the next look due at once (§12.4).
Pinned by `test_a_backlog_of_reports_is_read_in_short_looks`.

The long run on the fixed code then found a bug in the fuzzer itself: its
drain stopped after a round of only report looks that stored nothing.
With one report per look, such a round can read an empty report already
stored (the same bytes as an earlier empty one) while the report with
the events waits one look further, so the drain stopped short and a
`file` job missed its events. The code was right: the next look runs at
once. The drain now catches up fully in each round (the same loop as
`the_worker_catches_up`) and stops after a round that changed nothing.

- The echo fuzzer's normal budget is 30 sequences (about 10 to 15 s); the
  soak profile runs it long.

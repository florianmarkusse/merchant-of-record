# Messaging: facts, deliveries, the worker and the consumers

Status: built (slice 2, with the consumer table and unordered
deliveries). How the outbox, the worker
and every consumer work. `plan-architecture.md` §4 and §5 give the lock
design and the duplicate guards this builds on.

## 1. What it guarantees

- A fact and the state change that caused it commit together
  (transactional outbox).
- Every consumer gets every fact routed to it at least once, and acts on
  it once: a redelivery finds the inbox row and changes nothing.
- No vendor is asked twice for the same thing unless its duplicate guard
  makes that safe (`plan-architecture.md` §5).
- No order: every consumer takes facts in any order, late and twice. A
  consumer that gets several facts for one aggregate either only moves a
  state forward or re-checks under the row lock.
- A crash at any point leaves committed state that the next run picks up;
  no clock decides whether work is still in progress.

## 2. Tables

```sql
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
  aggregate_type  aggregate_type NOT NULL,     -- names the advisory lock
  aggregate_id    uuid NOT NULL,
  seq             bigint NOT NULL,             -- the fact's outbox seq
  not_before      timestamptz NOT NULL,        -- time only triggers a retry
  attempts        int NOT NULL DEFAULT 0,
  last_error      text,
  handled_at      timestamptz,
  PRIMARY KEY (message_id, consumer)
);
CREATE INDEX outbox_delivery_due ON outbox_delivery (not_before)
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
  (§3). This is the one place a serialized document is the right shape.
- `handled_at` is set once, under the delivery row's lock, in the same
  transaction as the consumer's effect, together with the inbox row. A
  trigger refuses to set it back to NULL, or to change anything but
  `not_before`, `attempts`, `last_error` and `handled_at`.
- A statement trigger on `outbox_delivery` sends a NOTIFY (no payload,
  delivered on commit), so no code path can forget to wake the worker.
- The aggregate columns let the worker name an effect's advisory lock
  without decoding the fact.

## 3. Facts and routing (`src/mor/messaging/`)

- `facts.py`: one frozen msgspec `Struct` per fact and version
  (`ProbeStartedV1`). Each names its `message_type` and `schema_version`
  as `ClassVar`s. `encode(fact)` and `decode(message_type, version,
  bytes) -> Fact | UndecodableFact`, an exhaustive `match`.
- **A shape change is a new version, never an edit**: undelivered facts
  of the old shape must still decode. `tests/messaging/test_fact_schemas.py`
  compares each version's msgspec JSON schema with the one recorded in
  `tests/messaging/fact_schemas.json` and fails on a change;
  `uv run python -m tests.messaging.fact_schemas` records a new version
  and never rewrites an existing one.
- `routing.py`: `consumers_for(message_type)`, the one table of who gets
  which fact (an exhaustive `match`), and `consumer_kind`, which says
  whether a consumer is `Local` or `Effect`. Producers never know their
  consumers.
- `store.py`: `append_fact(conn, fact, actor, now, deliver_at=None)`
  writes the outbox row and one delivery row per routed consumer in the
  caller's transaction, and never commits. A fact with no consumer writes
  no delivery.
- **A scheduled fact** is an ordinary fact whose deliveries carry a later
  `not_before` (`deliver_at`). Time only starts the work: the consumer
  decides under the row lock whether it is still wanted (a poll for a
  final payment is not).

## 4. The worker

`python -m mor.messaging.worker`, on a dedicated connection (its advisory
locks must die with it, so never a pooled one):

1. Pick deliveries that are unhandled and due (`not_before` passed),
   oldest first (`find_candidates`). Nothing waits for an older delivery
   of the same consumer or aggregate.
2. An effect consumer: try the session advisory lock on the delivery's
   aggregate (`pg_try_advisory_lock`, a 64-bit hash of type and id;
   returns at once). Not acquired: another worker is mid-call on that
   aggregate, so skip it (`busy`). A local consumer takes no advisory
   lock: it locks the rows it changes.
3. Open the delivery: lock its row `FOR UPDATE SKIP LOCKED` (a row
   another worker holds is skipped, never waited for), check it is still
   open and due, decode the fact. A local consumer runs in this same
   transaction; an effect opens it in a short transaction of its own and
   then runs its steps (§6).
4. Release the advisory lock, if taken.

- **A consumer that raises is a bug.** Its transaction rolls back; a
  second short transaction records `attempts`, `last_error` and a later
  `not_before` (2, 4, 8 … seconds, capped at an hour). Only that delivery
  waits; a stuck one shows in `last_error IS NOT NULL`.
- **Idle**: after a drain that ran nothing, the worker waits for the
  NOTIFY, for the next delivery to come due, or at most 5 s (the fallback
  poll: a notification sent while no worker listens is not kept). Watch
  `pg_notification_queue_usage()`: a listener stuck in a long transaction
  would fill the queue and fail commits that write deliveries. Workers
  never hold long transactions.
- **Scaling** is more worker processes: aggregates run in parallel, one
  effect per aggregate at a time. Every worker asks for the same oldest
  candidates, so at many workers most lock attempts return `busy`:
  correct, but wasted round trips. If it ever shows, start each worker at
  a random offset or split aggregates by a hash of the id.
- **Why no ordering**: per consumer and aggregate order would guarantee
  nothing a consumer relies on (each takes facts in any order, and order
  across consumers is not kept), while a delivery scheduled for later
  would hold back every later one, and one that raised everything behind
  it.

## 5. The consumer table and local consumers

`mor.messaging.consumers` is the one place that says, per consumer, what
it gets, what it locks and what it runs: one `match` on the consumer name
in `run_local` and one in `run_effect`. Each case:

1. **Narrows** the fact to what the consumer needs, an id or the typed
   fact, with `expect(fact, Record)`, which raises `WrongFact` for a fact
   routing never sends. A consumer that gets several fact types matches
   them in its case. Consumer functions take ids, typed facts and locked
   rows, never a bare `Fact`.
2. **Locks** (local consumers): the row the consumer changes, with that
   module's lock function, and passes the locked row as an argument.
   Where the row is found through another (a settlement line's attempt),
   the lookup reads a column that never changes and is checked again
   under the lock. A consumer that locks nothing says why in its case
   (the inbox decoders read a row that never changes; `quote_wait_notify`
   only appends facts).
3. **Runs** it: a local consumer in the worker's transaction; an effect
   through `run_steps` (§6).

A local consumer is one transaction: lock, decide with a pure function,
write state, ledger entries and facts; the worker marks the delivery
handled and commits. It returns `Applied` or `Stale` (the aggregate has
already moved past this fact: handled, with no effect).

**A locked row has a type of its own**: `LockedAttempt`, `LockedSession`
and `LockedOrder` (a `NewType` over the record), or a record of its own
(`LockedProbe`, `LockedLine`, `LockedPayout`). Only the row's `lock_*`
function makes one (the ast-grep rule `locked-row-made-by-its-lock`), and
every function that changes the row takes it (`move`, `record_payment`,
`set_session_state`, `add_address`, `insert_attempt`, `issue_invoice`).
So pyright refuses a read-then-write without the row lock, in a consumer
or a web handler alike. It proves the row was locked when read, not that
the transaction holding the lock is still open: a value can outlive its
commit, as across `run_steps`.

## 6. Effect consumers

Anything that calls a vendor. Three plain functions, side by side in the
consumer's module, each given only what it may use:

```python
async def prepare_ask(conn, lookup_id, now) -> NoCall | AskCall          # database only
async def ask(http, environment, call: AskCall) -> RatesResult           # vendor only
async def complete_ask(conn, lookup_id, call, result, now) -> StepResult  # database only

case ConsumerName.DUTY_RATE_ASK:
    lookup_id = expect(fact, DutyRateNeededV1).duty_lookup_id
    return await run_steps(run, lookup_id,
                           prepare=mor.duties.lookups.prepare_ask,
                           call=mor.duties.lookups.ask,
                           complete=mor.duties.lookups.complete_ask)
```

`run_steps` (`mor.messaging.delivery`) is generic over the subject, the
call and the vendor's result, so pyright checks that the three fit. It
owns every transaction:

- `prepare` in one: lock the row, decide whether a call is still wanted
  (`NoCall` ends the delivery there), find or create the vendor request
  row and mark it `sent`, commit;
- the call with no transaction open;
- `complete` in a new one: lock the row again, apply the result to the
  state as it is now (a handler may have moved it), write state, ledger
  entries and facts, and end the delivery (`Done`) or defer it
  (`RetryLater`, a later `not_before`); `Again` runs `prepare` again (a
  payment's duplicate to cancel, a settlement file per listing), at most
  50 rounds.

`EffectRun` carries what every run shares: the connection, the HTTP
client, the environment, the delivery and `now`.

- **No row lock is held across a vendor call, by design**: no code holds
  a connection and the HTTP client at once except `run_steps`. The call
  function gets no connection; the steps get no HTTP client. There is no
  runtime check that a registry call has no open transaction: with this
  shape it would have nothing to catch.
- **Why the commit before the call**: it separates "we are about to ask"
  from "the vendor answered", so a crash cannot be mistaken for a call
  never made. A `sent` request with no result is an earlier run that died
  mid-call or got no answer; the operation's duplicate guard decides what
  happens next (`plan-architecture.md` §5).
- **The advisory lock spans the commits**: without it a second worker's
  `prepare` would see a `sent` request, take it for a dead run and ask
  again while the first call is still out. A local consumer may run on
  the same aggregate meanwhile (a webhook applied while our capture call
  is out); each `complete` re-checks the state, so it finds the attempt
  already captured and changes nothing more.
- **The call** maps the transport to a result union: connection refused
  or DNS failure is `Unavailable` (provably never left, always safe to
  send again); a timeout, a dropped connection or a 5xx is
  `OutcomeUnknown` (the vendor may have done the work); a documented
  refusal is a typed refusal; an answer we cannot read is
  `UndecodableBody`. These failures are one set of records for every
  port (`mor/vendor_failures.py`). The client bounds the whole call with
  `asyncio.timeout(account timeout)`.
- **A call step that can make one of several calls says which**: create
  and capture either send or look up after a lost answer, so their call
  returns `CreateSent`, `CaptureSent` or `Looked`.
- **Request rows**: one table per port (`echo_request`, the PSP's
  attempt columns, later `carrier_request`), unique on (aggregate,
  operation, number), holding the idempotency key for good.
- **The exceptions list is a state, not a table**: an aggregate in
  `needs_person` with its request still `sent`. A generic exceptions
  table would need a foreign key to any port's request table, which SQL
  cannot express.
- **Reads need no guard**: polls and report fetches only read at the
  vendor, so a repeat after a crash stores the same bytes and the inbox
  key absorbs it; they need no request row.

## 7. Inbound: one path for every mode

How a vendor's events reach us is capability data on the account, never a
branch in feature code:

| Mode | How events arrive | Started by |
|---|---|---|
| `push` | a signed webhook carrying the events | the vendor |
| `push_thin` | a signed webhook carrying an event id and the object's id; we fetch what happened | the vendor |
| `poll` | we fetch one page per aggregate | a scheduled fact per aggregate, until a final state |
| `file` | a report file of everything since the last one | a scheduled fact per account |
| `none` | nothing: the vendor offers no events | only events recorded by hand |

```
push, push_thin ─▶ webhook handler: verify signature ┐
poll            ─▶ effect consumer: fetch a page     ├─▶ <port>_inbox row (raw bytes)
file            ─▶ effect consumer: fetch the file   ┘    + fact <port>_inbox_stored
                                                                │
                     local consumer, per account: decode the raw bytes with
                     msgspec into the port's event records; one fact per
                     event, each for its own aggregate (thin: a fact that
                     starts a fetch)
                                                                │
                     local consumer, per aggregate: insert the event (unique
                     per aggregate + vendor event id); derive the status from
                     all events; first time a status is reached: a milestone
                     row (unique) and its fact
```

- **Store first, decode later.** Every mode stores the raw bytes, commits
  and stops; a webhook then answers 200. A decoder bug never loses an
  event: the raw row stays, and a fixed decoder re-runs it.
- **The duplicate guard is the vendor's id, never the bytes**. Every
  event has the vendor's event id, and the unique key
  on (aggregate, vendor event id) records it once, whatever mode brought
  it. Every notice carries an id too (a thin one the id of the event
  behind it), a report file has its name, which is also our cursor, and a
  poll page is a read and needs none. The inbox's unique hash of the body
  only means "these exact bytes are stored already" (a resend of one
  webhook, a page fetched twice); a vendor whose distinct notices could
  be the same bytes would lose the second, so no vendor module relies on
  it.
- **Two hops**, because one step locks one aggregate: the inbox row
  belongs to the account (one webhook may carry events of several
  aggregates), each event to its aggregate.
- **Status is a pure function of the set of events**
  (`derive_status(frozenset[event])`), not of their order: duplicates and
  out-of-order arrival cannot corrupt it, and a late earlier event emits
  no milestone backwards. The milestone's unique key is a second guard
  behind the row lock.
- **Any event can be recorded by hand, whatever the mode**: a
  person who sees on the vendor's site that something
  happened records it as themselves. It enters at the same fact as a
  decoded vendor event (source `manual`), so it goes through the same
  apply step, key, status and milestones.
- **A poll asks by our reference**, not the vendor's id: a thin webhook
  can arrive before we recorded the vendor's answer.
- **A file look reads at most one report**, then makes the next look due
  at once; a look that finds nothing makes the next one due after the
  interval. Reading a backlog in one delivery ran past the 50-step bound.
- **Webhooks take no cookies**, so the signature (HMAC-SHA256, stdlib
  `hmac`, constant-time compare) meets the cross-site rule: a bad one is
  401 and stores nothing. A body already stored is 200 and nothing new.
- A real port keeps a `credentials_ref` naming a key in `.env`; only the
  fakes' made-up secrets sit on the account row.
- Deferred to the shipment slice, where it has a business meaning: a
  check that starts when no event arrived for N days and opens a case for
  a person. Time only starts it.

## 8. The probe: the deploy health check

A real aggregate that walks through two local consumers, kept as the
check that the worker runs and reaches the database:

```
start_probe (CLI)  → probe 'started'  + fact probe_started
probe_echo         ← probe_started:  lock probe, started → echoed, fact probe_echoed
probe_finish       ← probe_echoed:   lock probe, echoed → finished
```

`python -m mor.probe` starts one and exits 0 once it is `finished` (1
after its wait). Kept in the real migrations rather than as a test-only
toy, so `mor_test` never has tables the migrations lack.

## 9. The echo flow (transitional)

A dev/test-only aggregate, `echo_job`, that proves the effect and inbound
machinery against a fake "echo" vendor that stores a message and returns
an id. It goes with the shipment slice, which exercises the same
machinery; until then it is excluded from the ast-grep rules on tuple rows
and on picking a provider by name.

```
echo_job: requested → echoed | refused | needs_person, and deduplicating
echo_send (effect) ← echo_job_requested: send under the provider's guard;
                     while deduplicating, one cancel per `Again` round
inbound            echo_inbox_decode, echo_event_apply, echo_poll,
                   echo_report_fetch, echo_tracking_start (all modes of §7)
```

One provider per duplicate guard:

| Provider | What the fake promises | After a `sent` with no result |
|---|---|---|
| `fake_keyed` (`vendor_constraint`) | a known `Idempotency-Key` returns the first answer, for good | send again with the same key |
| `fake_cancellable` (`compensable`) | nothing on resend, but an echo can be cancelled | send again, then `deduplicating`: find every echo by our reference and cancel each extra one |
| `fake_plain` (`none`) | nothing, no cancel | never resend; find by our reference: found → `echoed`, not found → `needs_person` |

- Only a request sent more than once on a `compensable` vendor can have
  left duplicates; such a send, answered or refused, moves the job to
  `deduplicating`. A resend that is refused keeps an echo an earlier send
  made.
- The fake's `keyed` flavour replays a known key even when a `refuse`
  fault is scripted: refusing a replay would break its own promise.
- The fake (`mor/fakes/echo.py`, routes under `/_fake/echo/{flavour}/…`)
  keeps its state in the `fake_vendor` schema, releases events only when
  told (`…/events/release`), and offers faults: a 500 before or after the
  work, a timeout after the work, a refusal, a webhook sent twice or with
  a bad signature.
- Removing it means dropping its tables and leaving its values in the
  shared enum types: PostgreSQL cannot drop an enum value without
  rebuilding the type.

## 10. Testing

- **Layer 1**: `decode(encode(fact))` round-trips for every fact; any
  bytes decode to a fact or `UndecodableFact`; every inbound decoder turns
  any bytes into events or a typed error; `derive_status` is the same for
  every order and duplication of a set of events.
- **Crashes are faults in the network, not in our code.** The tests'
  transport (`RefusingTransport`, `tests/helpers.py`) refuses a
  connection on demand, and can crash the worker before a request is sent
  or after its answer came back (`crash_around_a_call`); the real
  `run_one` runs, and the connection then closes as a dead worker's
  would. A crash helper waits until the server process is gone, because
  PostgreSQL frees the advisory lock a moment after the client closes;
  production needs no wait (a worker meeting the lock gets `busy`).
- **Layer 2**: a state machine per flow (probe, echo, duty, shipping,
  payment, settlement), drawing candidates from `find_candidates`, so
  deliveries run in random order. Checks after every action: nothing
  raised outside a planted crash, every aggregate in a state the model
  allows, no advisory lock held, no milestone fact twice. After draining
  with faults off: everything in an allowed end state and nothing
  unhandled. The fuzzers' depth and reach milestones are in
  `plan-architecture.md` §10.
- **Layer 3** (`tests/flow/test_concurrent_workers.py`): worker tasks on
  their own connections drain hundreds of probes and echo jobs at once,
  number invoices without a gap, and apply a webhook while a capture call
  is out.
- **Each guard was checked by planting its bug**: no `handled_at` check,
  a consumer that is not idempotent, no advisory lock around an effect,
  guard `none` resending, duplicates never cancelled, no `sent` mark,
  status from the latest event, a polling schedule that never stops.
  Each fails a test. The delivery row's `FOR UPDATE` fails no test while
  the advisory lock and the inbox hold; it stays as a second guard for a
  path that runs a delivery another way (a staff "retry now").

## 11. Decisions kept, with the reason

- **The inbox is kept** although `outbox_delivery` could dedupe alone:
  the duplicate check goes through it exactly as
  it would with a broker delivering instead of our table, so moving to a
  broker changes only the transport. Cost: one small insert per handled
  delivery.
- **Push, with producers not knowing consumers.** `append_fact` looks
  consumers up in `routing.py`, a subscription list. A pull design (a
  cursor per consumer on the log) loses per-message retry and backoff,
  and one failing message stops the cursor.
- **A delivery table, not a broker**, while we run on one database:
  moving to a broker replaces `outbox_delivery` and the claim query only.
- Dependencies: msgspec (BSD-3-Clause), starlette (BSD-3-Clause) and
  httpx2 (BSD-3-Clause), the Pydantic organisation's maintained fork of
  httpx 0.28.1 under the import name `httpx2`; httpx itself has had no
  release since 06-Dec-2024 and lists no Python 3.14 support.

## 12. Not built yet, until needed

- **The message's own uuid.** `outbox.id` identifies a message, but
  `outbox.seq` already does. Dropping `id` touches migrations, the inbox
  and deliveries for little gain.
- **Version draining**, when the first V2 fact lands: a CLI or staff page
  listing `(message_type, schema_version, open deliveries)`, and a test
  that each old version's `decode` case says what must be true before it
  goes.
- **Replay**: facts are kept forever, but a new consumer gets no old
  facts. A backfill command (deliveries for past facts of a type) when
  first needed.

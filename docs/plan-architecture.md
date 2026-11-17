# Plan: overall architecture, with logistics in detail

Status: design confirmed 15-Nov-2026 after review; the decisions are in §15.
No code is written yet, and building waits for a go-ahead. A new session
starts at §16.

This is the one design for the whole flow: checkout, tax and duty, payment,
shipment, courier invoice, returns and payout. It sits above the earlier
plans and does not repeat them:

- `plan-skeleton.md`: money, db, ledger, outbox (the foundation).
- `plan-psp-integration.md`: payment providers, merchant API, payouts.
- `plan-merchant-surfaces.md`: API resources, hosted checkout, dashboard.
- `plan-logistics.md`: countries, the tax path rule, carriers, returns
  policy, registrations.

Where this plan changes something in those, §12 lists the change and why.
Logistics gets the detail here (§7 to §9) because it had the least.

## 1. Three rules that shape everything

**1. A lock or a constraint guards; time only triggers.** Whether an
action is safe, whether work is already being done, and whether something
has already happened are answered by a database lock or a unique
constraint, never by comparing clocks. There are no leases, no "claimed
more than 5 minutes ago, so take it over", no "probably finished by now",
no keys that stop counting after a window. Time may do two things only:
start work (a retry, a poll, a deadline coming due), and be a business
fact we promised someone (a quote valid until 14:35, a 14-day return
window). Even then the decision is taken under the lock (§4).

**2. Every outside party sits behind a port with a fake.** Payment
providers, couriers, the duty source, the payout rail, email. One module
per provider, one registry per port that picks by name, and a fake that
behaves like a real vendor, including its faults (§6).

**3. The flow is fuzzed, not only unit-tested.** Decisions are pure
functions; every step that touches the outside is split so a test can stop
it between any two commits. Hypothesis drives random orders of buyer
actions, vendor events, duplicates, crashes and clock jumps against the
real database and the fakes, and checks money and state invariants after
every step (§10).

## 2. The whole flow

Each row is one aggregate: a record with a closed set of states, changed
only under its own lock. Aggregates talk to each other only through facts
in the outbox.

| # | Stage | Aggregate | Outside party | Fact that ends the stage |
|---|---|---|---|---|
| 1 | Merchant creates a session | `checkout_session` | none | `checkout_session_created` |
| 2 | Buyer gives the address; we price it | `checkout_session` + immutable `quote` | duty source (only for an unknown product, §8) | `quote_made` or `quote_refused` |
| 3 | Buyer pays | `payment_attempt` | PSP | `payment_captured` |
| 4 | Order exists, invoice issued | `order_`, `invoice` | none | `order_paid`, `invoice_issued` |
| 5 | Label booked | `shipment` | courier | `shipment_label_booked` |
| 6 | Parcel travels | `shipment` (+ append-only events) | courier | `shipment_delivered` (or `_lost`, `_returned_to_sender`) |
| 7 | Courier bills us | `courier_invoice` | courier | `courier_invoice_line_matched` |
| 8 | Return or refund, if any | `return_request`, `refund` | courier, PSP | `return_received`, `refund_succeeded` |
| 9 | Merchant's money released and paid | `payout` | bank rail | `payout_paid` |
| 10 | Tax filed, where we collected it | `tax_filing` | authority or filing service | `tax_filing_paid` |

```
buyer ──▶ web handlers ──▶ one transaction: lock row · change state ·
merchant ─▶ (HTML, JSON)    ledger entry · facts into the outbox
                                    │
                                    ▼
                    workers: claim a delivery per aggregate (§5)
                    ├─ local consumer: one transaction, same shape
                    └─ effect consumer: prepare ▸ call ▸ complete
                                    │
              ┌──────────┬──────────┼──────────┬──────────┐
              ▼          ▼          ▼          ▼          ▼
            psp/      couriers/   duties/    rails/     notify/
          registry    registry    registry   registry   registry
              ▲          ▲
   webhooks, polled pages, report files ─▶ vendor inbox (raw, unique key)
                                           ─▶ decoded into our facts
```

The pure centre: pricing (`build_quote`), the tax path rule, every state
transition table, the shipment status derived from its events, and every
ledger posting are plain functions of their arguments. They read no clock,
no database and no environment; `now` is an argument.

## 3. Layout

`mor` stays the placeholder package name. Folders, not a list of every
file:

```
src/mor/
  money/ db/ ledger/ outbox/   as in plan-skeleton.md
  messaging/    fact records with versions, the consumer table, the worker
  merchants/    merchant, users, API keys, KYB state
  checkout/     session, sold lines, quote (pure builder + store)
  markets/      country rows, the tax path rule (plan-logistics.md §4)
  tax/          one module per jurisdiction, effective-dated rules
  duties/       port, registry, client per duty source
  payments/     attempt, refund, dispute (state machines)
  psp/          port, registry, client per PSP
  orders/       order, invoice
  shipments/    shipment, events, derived status, returns
  couriers/     port, registry, client per courier, rate cards
  payouts/      payout runs
  rails/        port, registry, client per payout rail
  web/          handlers and typed HTML pages; route table in main.py
  fakes/        the fake vendor servers (§6), never imported by the rest
tests/          mirrors src/mor/, plus tests/flow/ for the fuzzed flow
```

Each provider folder has the same four parts: `port.py` (our records and
result unions, no provider words), `registry.py` (the one place that maps
a name to a module), one client module per provider, and the client for
the fake.

## 4. Locks: the concurrency design

PostgreSQL at READ COMMITTED with explicit locks. Four tools, each with
one job:

| Tool | Guards | Example |
|---|---|---|
| Row lock (`SELECT … FOR UPDATE` on the aggregate's row) | every read-then-write of a state | pay, cancel and expire on one session cannot interleave |
| Unique constraint | "this happened once" | one order per session; one ledger entry per idempotency key; one event per carrier event id |
| Session advisory lock on the aggregate | who is talking to a vendor about this aggregate right now | two workers never book the same label at once |
| Database triggers | invariants no code path may break | ledger lines sum to zero; ledger and events are append-only |

A session advisory lock is a named lock PostgreSQL holds for a database
connection, across commits, until the connection releases it or dies. That
last part is the point: a crashed worker's lock disappears with its
connection, at once, with no timeout to tune. Workers therefore use
dedicated connections, not a transaction pooler.

Rules:

- **One step locks one aggregate.** Anything that must happen to another
  aggregate goes there as a fact. So lock order cannot cycle, and a
  deadlock PostgreSQL reports is a bug to fix, not something to retry.
  The one planned exception is a decision on a merchant's balance (payout
  run, covering a refund): it locks the `merchant` row first, then its
  own row, always in that order.
- **Handlers never wait on a vendor.** A handler takes the row lock,
  changes state, writes facts, commits. The advisory lock is taken only
  by workers, so a buyer cancelling is never blocked by a courier call in
  flight; the cancel is recorded and handled next, in order.
- **The ledger needs no lock to append** (the idempotency key is unique),
  but a decision that depends on a balance takes the merchant lock, so two
  payouts cannot both spend the same money.
- **Deadlines are decided under the lock.** "Pay" locks the session and
  compares the quote's `valid_until` with the `now` it was given; "expire"
  locks the same row. Whichever gets the lock first wins, and the other
  sees the new state. A payment captured for a stored quote is always
  honoured, even if the capture arrives after the quote's time: the buyer
  paid a price we showed.
- **No expiring keys of our own.** Merchant idempotency keys, vendor
  event keys and request keys are kept for good. A replay next year is
  still recognised.

What time is still used for, and only for:

| Use | Kind | How |
|---|---|---|
| Retry a failed call later | trigger | `not_before` on the delivery (§5) |
| Poll DHL tracking; fetch reports | trigger | a scheduled fact (`tracking_poll_due`) |
| Quote validity, return window, payout delay | business fact | stored on the record, compared under the row lock; a scheduled fact (`payout_delay_passed`) starts the step |
| HTTP client timeout | gives up waiting | the result is `OutcomeUnknown`, a value; it decides nothing |

## 5. Messaging and workers

Facts only: past tense, immutable, versioned, one record type per fact and
version in `messaging/`. There are no command messages. "Book the label"
is the courier consumer reacting to `shipment_created` (changed from the
PSP plan, §12).

Tables (extending `plan-skeleton.md` §7):

- `outbox`: as in the skeleton, with `message_type` as an enum type.
- `outbox_delivery (message_id, consumer, aggregate_key, not_before,
  handled_at)`: one row per consumer of a fact, written in the same
  transaction as the fact. Which consumers get which fact is one table in
  code. This replaces a relay plus broker while we run on one database;
  moving to a broker later replaces this table and the claim query only.
- `inbox (consumer, message_id)`: unchanged. With a broker it becomes the
  only dedupe; today `handled_at` and it are written together.

The worker loop:

1. Pick deliveries that are unhandled and whose `not_before` has passed,
   oldest first.
2. Try the advisory lock for the delivery's aggregate
   (`pg_try_advisory_lock`, which returns at once). Not acquired: another
   worker has that aggregate; take the next one. No waiting, no lease.
3. Under the lock, re-read the oldest unhandled delivery for that
   aggregate and consumer (this gives ordering per aggregate) and run it.
4. Release the lock.

Two kinds of consumer, told apart by a closed type and matched
exhaustively:

**Local consumer**, one transaction: lock the aggregate row, decide with a
pure function, write state, ledger entries and new facts, mark the
delivery handled, commit.

**Effect consumer**, for anything that calls out. Three plain functions,
which the worker calls in a row:

```
prepare(conn, delivery, now) -> Nothing | Call(request)
    One transaction. Lock the row. Decide from the state whether a call is
    still wanted (a cancelled shipment needs no label). Find or create the
    vendor request row: unique on (aggregate, operation, number), holding
    the idempotency key. Mark it `sent`. Commit.
    If the row was already `sent` with no result, an earlier run died
    mid-call: what to do next follows the operation's duplicate guard
    (below).
call(account, request) -> result union
    No transaction open. Goes through the registry to one provider module.
complete(conn, delivery, result, now)
    One transaction. Lock the row. Store the result on the request row,
    apply the transition for the state as it is now, write ledger entries
    and facts, mark the delivery handled (or set `not_before` for a
    retry). Commit.
```

Why three functions and not one: a commit must separate "we are about to
ask the vendor" from "the vendor answered", or a crash cannot be told from
a call never made. And a test can stop between any two of them, which is
exactly a crash (§10).

**After an unknown outcome: the duplicate guard** (revised 15-Nov-2026
after review). The first draft said "look the request up at the vendor
first, and send again only if the vendor does not know it". That is
check-then-act across two systems: the first request can still be on its
way when the lookup answers "not found", and nothing we hold locks the
vendor. A lookup can prove a request arrived; it can never prove it did
not. Rule 1 applies to the vendor's side too: the only real guard is a
constraint in the vendor's own system. So each port operation, per
provider, declares which guard it has, as capability data:

| Guard | What the vendor promises | After a `sent` with no result |
|---|---|---|
| `vendor_constraint` | it refuses or replays a second request with our key or our reference, for good | send the same request again with the same key; the vendor's constraint decides. No lookup is needed to be safe |
| `compensable` | nothing, but a duplicate costs nothing and can be undone (a label that is never handed over) | send again; a duplicate shows up as two results for one reference, and the extra one is cancelled |
| `none` | nothing, and a duplicate moves money or cannot be undone | never send again automatically. Lookups only: found means complete it; still not found goes to an exceptions list for a person |

- A key the vendor honours only for a window (1 hour at Mollie, 14 days
  at Adyen) counts as `none`, not as `vendor_constraint`: trusting it
  would put a clock comparison back in the guard. The cost is small: it
  only bites when a call timed out after being sent and the lookup keeps
  saying "not found".
- `Unavailable` (the request provably never left, such as a refused
  connection) is always safe to send again, whatever the guard.
- Money-moving operations may never be classed `compensable`.
- The guard a provider offers for charging, paying out and booking is a
  selection criterion, checked in its current docs before we sign, never
  assumed.
- Lookup keeps one job: learning the result when the vendor refuses a
  duplicate instead of replaying the answer.
- The guard is one value per provider operation in that provider's
  capabilities. Changing it (a vendor adds a key, or we learn an unused
  label is billed after all) is a one-line change with its test.

Why our own row lock cannot do this alone (asked in review,
15-Nov-2026): it already does everything that happens on our side. The
advisory lock means only one worker ever talks to the vendor about a
shipment, and the committed `sent` mark means we always know a call may
have been made. What no lock of ours can do is make the vendor's work
and our commit one atomic step, because they are two systems. So the
provider's help is needed in exactly one case: the call was sent and no
answer came back. Every other retry is decided by our own locks.

**Inbound: three modes, one path.** Event-driven where the vendor offers
it, polling or files where it does not (decided 15-Nov-2026). The mode is
capability data per provider and channel, never a branch in feature code:

| Mode | How it arrives | Started by |
|---|---|---|
| `push` | a signed webhook | the vendor |
| `poll` | we fetch a page of changes | a scheduled fact per aggregate, which schedules the next one until a final state |
| `file` | a report or invoice file we fetch or are sent | a scheduled fact per account, or an upload |

All three store the raw body in that port's inbox table with a unique key
on the vendor's event id (or a hash of the body), commit, and stop there;
a webhook handler then answers the vendor. A consumer decodes the raw row
with msgspec into the same typed records whatever the mode, and applies
them under the aggregate's row lock. A thin webhook that carries only an
id is one more variant: the consumer fetches the state it points at.

## 6. Ports, provider choice and fakes

**Port.** Plain async functions over frozen records. Every result is a
union: ok, a typed refusal, `Unavailable`, or `OutcomeUnknown`. A provider
module never touches our database and never sees a transaction.

**Registry.** One function per port operation, each an exhaustive `match`
on the provider enum that calls the module's function directly:

```python
async def book(account: CourierAccount, request: BookRequest) -> BookResult:
    match account.provider:
        case CourierName.FAKE:
            return await couriers.fake.book(account, request)
        case CourierName.DHL_EXPRESS:
            return await couriers.dhl_express.book(account, request)
        case _:
            assert_never(account.provider)
```

No protocol class, no injection: jump-to-definition lands on the code that
runs, and pyright checks every module against the port's records. Adding a
provider is an enum member, a module and one `case` per operation.

**Which account is used** comes from data: account rows per port
(`psp_account`, `courier_account`, …) and effective-dated routing rows.
For couriers the routing key is lane (origin, destination), service level
and the order's tax paths (`plan-logistics.md` §4: a border-paid order may
only use a duty-paid service). The routing version is stored on the quote
and the shipment. Feature code never names a provider.

**Fakes are fake vendors, not stubs.** `mor.fakes` is a small Starlette
app per port that speaks a made-up but realistic vendor API over HTTP:
idempotency keys, signed webhooks, tracking pages, invoice files. The
`fake` client module is an ordinary httpx client pointed at
`account.base_url`, the same shape as a real provider's module. The fake
keeps its own state in its own schema (`fake_vendor`) on its own
connection, as a real third party would.

- In pytest and in the fuzzer the fake is called in-process through
  httpx's ASGI transport: no sockets, offline, fast.
- Outside `prod` the fakes' routes are part of the web app itself, under
  `/_fake/` (decided 15-Nov-2026, so local development stays one web
  server and one worker, with no third process to start). The worker
  reaches them at the dev server's own address, and the fake posts its
  webhooks back to the same server. In `prod` the routes are not in the
  route table.
- Why HTTP and not plain in-process functions:
  1. Web and worker are two processes. A booking the worker makes must
     be visible when the web process shows tracking, so the fake's state
     cannot live in one process's memory.
  2. Push mode needs something that sends us a signed webhook. Writing
     the event straight into our inbox would skip signature checking and
     decoding, the code most likely to break on a real vendor.
  3. Timeouts, 500s and half-finished calls happen on the wire. A
     function call cannot time out after doing the work.
- It is scripted, not timed. Scenarios are chosen by values in the
  request (a ship-to postcode `LOST`, an amount ending in a known digit),
  and events are released by an explicit "next event" call from a dev-only
  page, a CLI or the fuzzer. An auto mode plays a whole script at once for
  a hands-free order.
- It injects faults on request: timeout after doing the work, 500, a
  duplicate webhook, events out of order, an invoice line that differs
  from the quote.
- It counts what it was asked to do per reference. That count is the
  fuzzer's proof that nothing was booked or charged twice.

**Environments.** `ENVIRONMENT` is `dev`, `test` or `prod`. Outside `prod`
the registries raise on any provider but the fake, so a wrong account row
cannot reach a real vendor. Merchants test in a separate sandbox
deployment (`ENVIRONMENT=test`, its own database, the fakes), never with
test keys on prod (decided 17-Nov-2026, replacing "test keys on prod
route to fake accounts, `livemode = false`"). Reasons: prod holds only
live data, so no record needs a live/test flag and no test payment can
reach a live report or payout; and the fakes never ship to prod. The
cost: the sandbox is something merchants rely on, so it must stay up,
and the long fuzz runs use a database of their own. Contract tests
against a vendor's sandbox are run by hand with an explicit flag, never
by `pytest`.

**Every integration mode has a fake** (decided 15-Nov-2026: the fakes and
the test setup must exercise every way a provider can be wired, not one
happy shape). A mode here is any capability value that changes which code
runs. The fake for each port offers all of them, each as its own account
or service row:

| Port | Modes the fake offers |
|---|---|
| Courier | tracking by `push` and by `poll`; invoice by `file` and by API page; each duplicate guard; shipping price from our tables and from a live rate call (§8); duty-paid and not |
| PSP | fat and thin webhooks; sale, authorise-then-capture, and buyer action (redirect, QR); synchronous and pending refunds; each duplicate guard; settlement by `file` and by API |
| Duty source | our own tables and a vendor call (§8) |
| Payout rail | instruction file and API with status events |

The fuzzer draws the mode per run (§10), so every one is exercised. The
set of modes is derived from the capability types, and a test fails when
a capability value exists that no fake account offers.

**The test setup** (it does not exist yet; build order §14). A deployed
copy with `ENVIRONMENT=test`: web (with the fakes' routes) and worker,
its own database, synthetic data only. Its seed data
has one merchant per mode combination worth walking through by hand (a
push-tracked order, a polled one, a live-rated one, and so on), and it is
where the long fuzz runs happen.

## 7. Logistics: records and tables

```
CourierAccount   { id, provider, base_url, credentials_ref, billing_currency }
CourierService   { account_id, code, level: express | standard | economy,
                   duties_paid: bool, carries_scheme_number: bool,
                   tracking: push | poll, invoice: file | api,
                   book_guard: vendor_constraint | compensable | none }
                 # capabilities as data (§5)
RateCard         effective-dated: zones, weight breaks, fuel, surcharges
                 (the shape docs/markets/ already has, loaded into tables)

Parcel           { weight_g, dims_mm }                 # billed weight is derived
CustomsSnapshot  { lines[{ description, hs_code, origin, quantity, value }],
                   incoterm: ddp, scheme_numbers[], shipper, exporter }
                 # frozen at booking: what we told customs, for good

Shipment         { id, order_id, courier_account_id, service_code, parcels[],
                   customs: CustomsSnapshot, routing_version, state,
                   tracking_number?, export_declaration_no? }
ShipmentEvent    { shipment_id, carrier_event_id, kind, occurred_at,
                   received_at, raw_ref }              # append-only
ReturnRequest    { id, order_id, reason, state, return_shipment_id? }
CourierInvoiceLine { account_id, invoice_no, line_no, tracking_number,
                   charge: freight | fuel | duty | import_tax | duty_paid_fee
                           | surcharge, amount: Money }
```

Tables and the constraint that does each guarding job:

| Table | Unique on | What it prevents |
|---|---|---|
| `shipment` | `(order_id, sequence)` | a second shipment for the same order step |
| `courier_request` | `(shipment_id, operation, number)`, and the idempotency key | a second booking after a crash |
| `courier_inbox` | `(account_id, vendor_event_key)` | a webhook or polled page stored twice |
| `shipment_event` | `(shipment_id, carrier_event_id)` | an event applied twice |
| `shipment_milestone` | `(shipment_id, milestone)` | `shipment_delivered` emitted twice |
| `courier_invoice_line` | `(account_id, invoice_no, line_no)` | a bill line booked twice |
| `return_request` | `(order_id, sequence)` | a double return on a double click |

One table per thing, typed columns, enum types for every closed set. The
customs snapshot is real columns plus a `shipment_customs_line` table, not
a serialized blob.

**Port operations** (`couriers/port.py`): `book` (label, commercial
invoice, duties billed to us), `find_by_reference`, `cancel`,
`book_pickup`, `book_return`, `quote_rate` (a live price, §8),
`fetch_tracking` (one page), `verify_webhook`, `decode_events` (a webhook
body or a polled page into the same `CarrierEvent` records),
`fetch_invoice_page`, `decode_invoice` (a file or a page into invoice
lines). We always send our shipment id as the carrier's shipper
reference, so `find_by_reference` works wherever the carrier offers a
search by it. A provider that lacks an operation says so in its
capabilities, and routing never picks it for a job that needs it.

## 8. Logistics: pricing at checkout

The quote is one pure function:

```
build_quote(lines, ship_to, country_row, registrations, services,
            shipping_prices, duty_rates, fx_rate, now)
    -> Quote | QuoteRefusal
```

`QuoteRefusal` is a closed set: country closed (with the reason shown),
line restricted for the country, no service for the lane, a required
buyer field missing (KR customs code), parcel over a service's limits.

`build_quote` only ever sees prices and rates as plain inputs. Where each
input comes from is routing data, switchable per lane without a deploy
(decided 15-Nov-2026: the first draft fixed shipping to our own tables
and duty to one vendor; both must be swappable like any provider).

**Shipping price: a source per service and lane.**

| Source | What it is | When it fits |
|---|---|---|
| `own_table` | our rate tables: zone × billed weight + fuel + listed surcharges, loaded from the carrier's rate card (the shape `docs/markets/` already has) | contract rates that are a published table; nothing to call, nothing to pay per view |
| `carrier_api` | the carrier's own rate call (`quote_rate`) on our account | carriers with no usable table, or prices that move daily |
| later: another vendor | a rate aggregator behind the same result record | if one ever sells VN rates |

All three return the same `ShippingPrice` record (amount, the parts it is
made of, source, version, and a `valid_until` if the vendor gives one).
A live price is fetched by a worker, never by the handler, and stored as
a row the quote points at. What the carrier later bills is compared with
the quoted price at invoice import (§9) whatever the source; a steady
difference shows a wrong table or a wrong source.

**Duty and import tax: stored rates, filled from a source.**

- Rates are stored per (HS code, origin, destination), effective-dated
  and append-only: an update is a new row that supersedes the old one
  from a date, and every row names its source and version. Quotes made
  earlier keep pointing at the row they used.
- A row is filled by the source routing names for that destination:
  `own_table` (a tariff file we load, or a rate a person enters on an
  admin page with its legal reference) or a duty vendor through
  `duties/registry.py`. Changing the source for a country is a routing
  row.
- Three ways a rate gets updated, all producing a new row: loading a new
  file; a scheduled refresh that asks the source again
  (`duty_rate_refresh_due`, per source, at a period set in routing data);
  a person correcting it. A refresh that returns a different rate also
  emits `duty_rate_changed`, so someone sees it.
- A vendor is asked once per new combination, when a product or line is
  first seen (`duty_rate_needed`), not on every quote. Vendors bill per
  call, so this keeps the cost per order near zero.

**Waiting.** When every input is already stored, the handler builds the
quote in its own transaction. When one is missing (a live shipping price,
a duty rate for a new product in a new country), the handler records
`quote_requested`, the workers fetch what is missing, and the buyer's
page refreshes itself (the same pattern as the QR page) until `quote_made`
or `quote_refused` exists. So no handler calls a vendor, as the shared
Messaging rule requires.

Decided 15-Nov-2026: a buyer waiting a little longer for a fresh landed
price is acceptable. The same wait happens whenever a stored input can no
longer be used: a rate row was superseded, a live price's `valid_until`
passed, or routing changed the source. Later, popular products can be
kept warm in the background so their buyers rarely wait: that is the
scheduled refresh above, run per product and destination instead of per
source. It adds a schedule, not a new mechanism.

**How the buyer's price is built** (decided 15-Nov-2026: the merchant
names the price it wants for the product and receives exactly that; we
add everything else on top, openly):

```
  merchant's price for the goods      → merchant_payable, in full
+ estimated PSP fee                   → psp_fee_collected
+ our margin                          → revenue_mor_margin
+ shipping for this country           → shipping_payable, at the quoted cost
+ tax and duty for this country       → tax_payable / duties_payable
= the one total the buyer pays
```

- This replaces "merchant's price minus our fee" in the PSP plan's
  capture entry. The merchant's payout no longer depends on our fee or
  on the destination.
- Our fee is part of what the buyer pays us for the goods, so by default
  it is inside the taxable amount and the customs value. Whether any
  country lets it sit outside is an adviser question, not assumed.
- **The fee is a rule in data** (decided 15-Nov-2026: a percentage at
  the start, with a fixed amount or both possible per merchant without a
  code change):

  ```
  FeeRule { merchant_id?, percent: Fraction, fixed: Money?, effective_from }
  ```

  A row without a merchant is the default; a row with one overrides it
  for that merchant from its date. Rows are append-only and the version
  used is stored on the quote. The percentage is exact (numerator and
  denominator), its rounding mode is named and the remainder booked. A
  fixed amount in another currency than the quote's is converted with
  the quote's stored FX rate. Changing a merchant's fee is one new row,
  entered on an admin page.
- **The fee is several named components, not one "MoR fee"** (decided
  15-Nov-2026: costs we pass through are shown as what they are, so the
  merchant can see how a price came to be). A closed set, each with its
  own rule row and its own ledger account:

  ```
  FeeComponent = psp_fee | fx | mor_margin      # more are added as members
  ```

  The merchant's build-up lists each one per country. The buyer still
  sees them inside the item price.
- **A pass-through cost is an estimate at the quote** (decided
  15-Nov-2026: on top for the buyer, so the merchant still receives
  exactly its price). The buyer's total is fixed when the quote is made, but
  the PSP's real fee is only known at settlement and depends on the
  payment method, which the buyer picks after the quote. So the quote
  charges an estimated PSP fee from a rule per buyer country and
  currency, and settlement import compares it with the real fee: the
  collected amount sits in `psp_fee_collected`, the real fee clears it,
  and the difference is ours, like any cost we could have priced in.
  Pricing per method instead would change the total after the quote,
  and charging a buyer extra for a payment method is restricted in
  several countries (to be checked per country, not assumed).

- **Prices from merchants are always tax-exclusive in v1** (decided
  15-Nov-2026: simplest for the merchant, which names one price and gets
  it). The `inclusive` option in the surfaces plan is dropped for now.
- **What the buyer sees** (decided 15-Nov-2026): four lines only, items,
  shipping, duties and taxes, and the total, with our fee inside the item
  price. The fee is stored as its own amount on the quote whatever is
  shown, so showing it as a line later is a change to one page. Reasons
  for folding it in: a buyer gains nothing from the split, and in law we
  are the seller, so our margin is part of the goods' price like any
  shop's, not a separate service. The merchant sees the full build-up
  per country in the dashboard and through the `estimates` endpoint, and
  uses that all-in figure if it wants its own site to match checkout.

**Indirect tax we collect ourselves** comes from the jurisdiction module
(`tax/gb.py` and so on), chosen by the tax path rule.

The quote stores every input's source and version: country row,
registrations, routing, shipping price row, duty rate rows, tax rule, FX
rate. It is immutable; a changed address makes a new one.

## 9. Logistics: the shipment, end to end

**Booking.** `order_paid` → a local consumer creates the `shipment`
(state `created`, customs snapshot frozen, service taken from the quote)
and emits `shipment_created`. The courier effect consumer handles that:

- `prepare`: order still wants shipping? Create `courier_request #1`,
  mark `sent`.
- `call`: `book`. After a recorded send with no result, the service's
  `book_guard` decides (§5): send again, send again and cancel an extra
  label later, or look up only and hand a persistent "not found" to a
  person.
- `complete`: `Booked` → state `labelled`, label and commercial invoice
  stored as files, tracking number and export declaration number saved,
  `shipment_label_booked` emitted. A typed refusal (address not
  serviceable, customs data rejected) → `label_failed` with the reason,
  shown to the merchant, who fixes and re-requests (request #2).
  `Unavailable` or `OutcomeUnknown` → the delivery stays unhandled with a
  later `not_before`; state stays `label_requested`.

**Cancel and refund before pickup** are facts on the same shipment, so
they queue behind a booking in flight. `complete` applies the result to
the state as it is then: a label that comes back for a cancelled shipment
leads straight to the `cancel` call. Pickup is its own request and state.

**Tracking.** Shipment status is a pure function of the whole set of
events, not of the order they arrived in:

```
derive_status(events: frozenset[CarrierEvent]) -> ShipmentStatus
```

So duplicates and out-of-order delivery cannot corrupt it, and a webhook
carrier and a polled carrier behave the same. When the derived status
first reaches a milestone (picked up, held at customs, delivered, lost,
returning, returned), a row in `shipment_milestone` is inserted and the
fact emitted, once, by the unique constraint. Polling (DHL) is a scheduled
fact per shipment that fetches one page and schedules the next; it stops
at a final status.

A carrier correcting itself (a "delivered" scan withdrawn) is a new event
and a new milestone, `delivery_withdrawn`. If the merchant's money was not
released yet, it stays held; if it was, the amount becomes a deduction
like any other.

**Courier invoice.** A file is stored raw, decoded into lines, and each
line is matched to a shipment by tracking number under that shipment's
lock. One ledger entry per line, keyed by the line's identity:

```
at capture (from the quote):
  Dr psp_clearing            gross
  Cr tax_payable:{country}   tax we collected ourselves
  Cr duties_payable:{courier}    duty + import tax + duty-paid fee, estimated
  Cr shipping_payable:{courier}  shipping charged
  Cr merchant_payable        the merchant's price for the goods, in full
  Cr psp_fee_collected       estimated PSP fee, charged on top (§8)
  Cr revenue_mor_margin      our margin, charged on top (§8)
at settlement import (real PSP fee known):
  Dr psp_fee_collected       the estimate
  Dr/Cr psp_fee_variance     the gap, ours
  Cr psp_clearing            the real fee the PSP kept
per courier invoice line:
  Dr duties_payable or shipping_payable   what we held for it
  Cr courier_payable:{courier}            what the carrier bills
  Dr/Cr courier_variance:{courier}        any difference, ours until attributed
when a person attributes a difference to the merchant:
  Dr merchant_payable
  Cr courier_variance:{courier}
```

The carrier bills in its own currency (DHL VN in VND). The line's FX rate
is stored as an exact fraction with its source; rounding is named and the
remainder booked. A line that matches no shipment, or a second bill for a
shipment already cleared (carriers re-bill weeks later), is not an error:
it becomes a `courier_invoice_exception` row for a person, or a further
entry on the same shipment.

**Who bears a difference** (decided 15-Nov-2026; it replaces "duty
estimate differences are the merchant's, both ways" of 04-Nov-2026). The
carrier account is ours, so we always pay the bill. Each difference
between quote and bill then opens a `variance_case`:

```
VarianceCase { id, shipment_id, invoice_line, difference: Money, state }
  state: open | ours { cause } | merchants { cause }
  cause: merchant_weight | merchant_dimensions | merchant_hs_code
       | merchant_description | merchant_address
       | our_rate_table | our_duty_rate | unlisted_surcharge | fx | other
```

- It starts as our cost. We eat what we could have priced in (a wrong
  table, a surcharge we did not list, FX). What comes from the
  merchant's data (wrong weight or box size, a wrong HS code, an address
  the carrier had to correct) falls on the merchant.
- Deciding is manual for now: a person picks the cause on an admin page.
  A merchant cause writes the second entry above, and the merchant's
  statement shows it with the shipment and the invoice line. Rules that
  attribute obvious causes by themselves can come later, per cause.
- A difference in our favour (we collected more than the carrier billed)
  stays in the same account and is reviewed the same way.
- The merchant contract must allow these deductions and say how a
  merchant disputes one: named for counsel, not assumed.

**Returns.** `return_request`: `requested → approved → label_issued →
in_transit → received → refunded`, with `rejected` and
`refunded_without_return` (`plan-logistics.md` §5). The return label is a
second shipment row through the same consumer and port (`book_return`).
The refund amount is computed from the reason by a pure function.

**Payout.** `shipment_delivered` schedules `payout_delay_passed`. That
consumer locks the merchant, checks under the lock that no return or
dispute is open on the order, and moves the amount from pending to
available with a ledger entry. The payout run locks the merchant, reads
the balance from the ledger, and writes the payout and its entry in one
transaction.

## 10. Testing and fuzzing

Three layers. The first two are Hypothesis tests, so they run in plain
`pytest`, with a small budget in the normal check and a large one in the
long runs.

**Layer 1: properties of pure functions** (no database).

- `money`: `allocate` sums to the total; `divide` result plus remainder is
  exact.
- `build_quote`: the total equals the sum of its parts in one currency;
  never a negative line; the merchant's part equals its price exactly; a
  border-paid order never gets a service that is not duty-paid; every
  input yields a `Quote` or a `QuoteRefusal`, never an exception.
- Transition tables: every (state, event) pair has an answer: a new state,
  `Stale` or `Parked`.
- `derive_status`: the same for every order and duplication of the events.
- Decoders: any bytes give a typed record or a typed error. Provider
  amount conversion round-trips or reports `not_representable`.
- Posting builders: every entry balances per currency for any input.

**Layer 2: the whole flow as a state machine** (`tests/flow/`), using
Hypothesis's stateful testing against the real test database and the
in-process fakes. Hypothesis picks the next action from:

- merchant: create session, request refund, approve return;
- buyer: enter address, pay, cancel, request return, double-submit;
- fake vendors: release the next event, send it twice, send an older one,
  send an invoice with a different amount, fail the next call, time out
  after doing the work;
- worker: run one delivery fully, or run `prepare` and stop, or `prepare`
  and `call` and stop (a crash at each commit boundary);
- clock: move `now` forward past a deadline.

A small in-memory model (a few dicts: what should be owed to whom, which
state each order should be in) runs beside it. After every action:

- no step raised (a raise is a bug by our own rule);
- every journal entry balances; the ledger balances equal the model's;
- every aggregate is in a state the model allows;
- no advisory lock is left held.

After draining all deliveries with faults switched off:

- the fake PSP charged each paid order once, for exactly the quote total;
  refunds never exceed captures;
- the fake courier holds exactly one live booking per shipped order;
- each paid order has one invoice; each delivered milestone exists once;
- nothing is left unhandled, and `psp_clearing`, `duties_payable` and
  `shipping_payable` are zero for orders whose bills have all arrived.

Why serial orders of steps are enough: every step is one transaction under
its aggregate's lock, so any real concurrent run equals some serial order
of those steps. The fuzzer explores the orders; shrinking gives the
shortest failing one.

**Layer 3: real concurrency**, a smaller suite. Several worker tasks and
handlers on separate connections against the same rows, checking the same
end invariants and the lock claims themselves (two workers never hold one
aggregate; a killed connection frees its lock at once). Not shrunk; it
tests that the locks do what layer 2 assumes.

**Running it.**

- `uv run pytest` (the normal check): each property with a small,
  derandomized budget, plus every saved failure from the Hypothesis
  example database. Fast and repeatable.
- `HYPOTHESIS_PROFILE=soak uv run pytest tests/`: the same tests with a
  large random budget, run for hours on the test setup (nightly) or on
  the laptop on demand. Failures go into the Hypothesis example database,
  so the next plain `pytest` replays them. A found failure is then pinned
  as an `@example` in the test and committed.
- Plain Hypothesis is the fuzzer (decided 15-Nov-2026: HypoFuzz, checked
  the same day, allows non-commercial use only; "any use within a
  commercial organization, including internal tooling or testing" needs a
  paid licence, and a generic tool is enough). Hypothesis is random
  rather than coverage-guided, so it finds less per hour. The tests need
  no change if a coverage-guided runner is added later.
- Hypothesis is a new dev dependency; its version, licence (MPL-2.0 from
  memory) and Python 3.14 support are checked when it is added.

**Modes.** Each layer 2 run first draws its wiring: for every port, which
of the fake's modes this run uses (§6: push or poll tracking, file or API
invoices, each duplicate guard, table or live shipping price, table or
vendor duty). The invariants are the same for every wiring, which is the
point: the money and the states must not depend on how a provider
happens to deliver. With guard `none`, the allowed end state also
includes "waiting on the exceptions list", and the invariant becomes:
never charged or booked twice, and never silently stuck.

Two notes on the rules:

- Hypothesis's stateful API is a class that inherits from
  `RuleBasedStateMachine`. That breaks "no inheritance", in test code
  only, because the library offers no other form.
- Hypothesis test functions are synchronous. Each test owns one event
  loop and runs our async steps on it.

## 11. Compliance touchpoints (named, not answered)

- Whose name is shipper, exporter and seller on the waybill, and which
  entity holds the carrier account (`plan-logistics.md` §6): the customs
  snapshot has a field for each and assumes no answer.
- Buyer identifiers some countries want on the waybill (KR customs code)
  are personal data under local law: stored on the shipment only, never
  logged, retention to be decided.
- The fakes and every fixture use synthetic people and addresses.

## 12. What this changes in the earlier plans

| Earlier plan | Change | Why |
|---|---|---|
| PSP §5.3: commands in the outbox | facts only; a consumer reacts | the shared Messaging rule: messages are facts |
| PSP §5.3: retry blindly inside the provider's idempotency window | a duplicate guard per operation: resend only where the vendor's own constraint (or free undo) makes it safe, else lookups and a person (§5) | rule 1: no clock decides, and a lookup cannot prove absence |
| PSP §4: `outcome_unknown { since }` plus a reconciler sweep | the delivery stays unhandled and follows the duplicate guard | no sweep by age |
| PSP §6.1, surfaces §4: idempotency keys replayed for 7 days | kept for good (decided 15-Nov-2026) | rule 1 |
| PSP §5.7: mock in-process | a fake vendor over HTTP (ASGI in tests) | the same code path as a real provider; faults; shared by web and worker |
| Logistics §7: `CourierModule` protocol | registry with an exhaustive match | no protocol classes; jump-to-definition |
| Logistics §1, §7: `quote` port call at checkout | a shipping price source per lane, our tables or the carrier's rate call, fetched by a worker (§8) | no vendor call from a handler; switchable |
| Logistics §2.3: duty source per order | stored rates per (HS, origin, destination), filled from our tables or a vendor, refreshed on a schedule (§8) | cost per call; switchable |
| Surfaces §3, logistics §1 step 11: a duty difference goes to the merchant's balance, both ways | every difference is ours until a person attributes it by cause (§9) | decided 15-Nov-2026: we bear what we could have foreseen, the merchant what its data caused |
| PSP §5.5: `merchant_payable` is the price net of our fee | the merchant gets its price in full; our fee is added on top (§8) | decided 15-Nov-2026 |
| Skeleton §3: ruff rule sets and check order | the shared baseline and order in the conventions file | that file is newer (11-Nov-2026) |
| Skeleton: sync `psycopg.Connection` | async psycopg throughout | Starlette is async; I/O functions stay `async` |
| Skeleton §7: `claim_batch` with `SKIP LOCKED` on outbox rows | `outbox_delivery` plus the advisory lock per aggregate | the lock must survive the commits inside an effect step |

## 13. Rules this design applies

Data-oriented records and plain functions (§2, §6); closed sets with
`assert_never` (§5, §6, §8); errors as values from every port (§6);
explicit over clever: registry by `match`, no injection, no reflection
(§6); transactional outbox, idempotent consumers, facts in the past tense,
ordering per aggregate, state machines that commit before the next
message (§5); a table per thing with enum types and unique keys (§7);
money as minor units with currency, exact FX, named rounding, booked
remainders, append-only double-entry ledger (§9); tax rules per
jurisdiction, effective-dated, version stored on the quote (§8); one
module per provider, one place that picks, fake by default (§6); no real
personal data in fixtures (§11); verify on realistic synthetic data (§10).

## 14. Build order

Each slice ends with the checks clean and its part of the flow fuzzed.

1. Skeleton as planned, with the changes in §12.
2. Messaging: `outbox_delivery`, the worker, both consumer kinds, the
   duplicate guards, the three inbound modes, the fake-vendor frame, and
   the layer 2 harness on a two-step toy flow. This is where the lock
   design is proven before anything depends on it.
3. The test setup: `deploy/deploy.sh` for an `ENVIRONMENT=test` server
   running web (with the fakes) and worker, with the nightly soak run. Early,
   so every later slice lands on it.
4. Markets, tax path, rate tables and duty rates with both kinds of
   source, `build_quote`; checkout session.
5. Payments against the fake PSP in every mode; order and invoice.
6. Shipments against the fake courier in every mode: booking, tracking,
   milestones.
7. Courier invoice import and its ledger entries.
8. Returns and refunds.
9. Payout release and payout runs against the fake rail.
10. The first real courier module, once the quotes decide which.

## 15. Decisions from the review, and what is still open

Decided 15-Nov-2026:

- **Locks guard, time only triggers** (§1, §4). Event-driven is
  preferred; polling and timers are used only where a provider offers
  nothing else, as a capability mode (§5).
- **Lookup-then-resend is not a guard** (§5): it only narrows the race.
  Replaced by the duplicate guard per operation.
- **Our idempotency keys never expire** (§4).
- **Shipping price and duty rates are switchable sources**, our own
  tables or a provider, per lane (§8). Duty rates are stored and can be
  updated by file, scheduled refresh or hand.
- **Fuzzing with plain Hypothesis**, no HypoFuzz licence (§10).
- **The test setup** is a deployed `ENVIRONMENT=test` copy with the
  fakes. It does not exist yet and is slice 3. It and the fuzzer exercise
  every integration mode (§6).
- **Guard `none` is the default** where a provider promises nothing: a
  stuck request waits for a person. The guard is a value we can change
  per provider operation (§5).
- **A buyer may wait for a fresh landed price**; background warming for
  popular products comes later on the same refresh mechanism (§8).
- **We pay every carrier bill; a person then attributes each difference**
  to us or to the merchant by cause (§9).

- **Fakes live inside the dev web server** under `/_fake/` (§6).
- **One shipment per order in v1**; several boxes on one waybill can come
  later, true splits are out (reasons: a split needs the boxes known
  before payment, can change the tax owed, pays the duty-paid minimum
  twice, and needs "partly delivered" rules for status, payout and
  returns).
- **The buyer's price is the merchant's price plus our fee, shipping,
  tax and duty on top** (§8).

- **Mistakes are covered per case**, by deducting a merchant-caused
  difference from that merchant's balance (§9). No buffer percentage on
  every shipment: it would make careful merchants pay for careless ones,
  remove the reason to enter correct data, and save no work. The rolling
  reserve covers a merchant whose balance cannot absorb a deduction.
- **The fee is a percentage at first, as a per-merchant rule in data**
  that also allows a fixed amount (§8).
- **Merchant prices are tax-exclusive in v1** (§8).

- **Our fee is inside the item price the buyer sees** (§8). A merchant
  whose own site shows its bare price will show a lower figure than our
  checkout; that is accepted, since we aim to be the merchant's main
  integration and payment links show our price from the start.

- **Fees are named components** (PSP fee, FX, our margin), shown to the
  merchant one by one (§8).

- **Pass-through fees are charged on top as an estimate**; the gap to
  the real fee is ours (§8).

Nothing is open in this plan. Open items that belong to other plans
(entity, carrier quotes, advisers) stay there.

## 16. Picking this up in a new session

Where things stand (16-Nov-2026): slice 1 is built: `pyproject.toml`,
`src/mor/` with `config`, `audit`, `money/`, `db/` (async connection and
the migration runner) and `ledger/`, `migrations/0001_ledger.sql`, and
tests including Hypothesis properties. The outbox moved to slice 2. The
step after it is slice 2, planned in `plan-messaging.md`: step 2a (facts,
deliveries, inbox, worker, the probe flow, layer 2 and 3 tests) and 2b
(effect consumers, duplicate guards, the fake echo vendor) and 2c
(inbound in every mode, the first web handler) are built. Slice 3, the
test setup, is planned in `plan-test-setup.md` (17-Nov-2026: on the
developer's laptop until a VPS exists; the deploy waits for it). Hypothesis was checked when added
(6.170.15, MPL-2.0, supports Python 3.14).

What follows is the hand-over as written before slice 1, kept for its
reading order.

Read, in this order:

1. `AGENTS.md` and the shared conventions it points to.
2. This plan, whole. It wins over the four earlier plans (§12).
3. `plan-skeleton.md` for slice 1, then the earlier plan that covers the
   slice in hand: `plan-psp-integration.md` (payments, payouts),
   `plan-merchant-surfaces.md` (API, checkout pages, dashboard),
   `plan-logistics.md` (countries, tax path rule, carriers, returns).

When told to start, the next step is slice 1 of §14: the skeleton as in
`plan-skeleton.md`, with the changes in §12 (the shared ruff baseline and
check order, async psycopg, `outbox_delivery` in place of `claim_batch`).
Before writing it:

- Ask the two open questions in `plan-skeleton.md` §9: creating the `mor`
  database role and the `mor_dev` and `mor_test` databases, and uuid7
  ids.
- Each slice is still designed first where it adds tables or message
  shapes: a short design for that slice, a reply, then the code.

To check in current docs when the slice that needs it starts, never from
memory:

- Hypothesis: version, licence and Python 3.14 support (slice 2).
- The duplicate guard each real provider offers for booking, charging
  and paying out (§5), when its module is built.
- Whether a carrier's API takes several boxes on one waybill, if a
  merchant needs it (§15).
- Whether charging a buyer extra per payment method is allowed, per
  country, before any per-method pricing (§8).

Names that are placeholders: the package `mor`; the fee percentages and
shipping prices used in examples anywhere.

An explainer page with worked examples of this design was published for
the user as a private artifact, "MoR Flow Architecture". It is a reading
aid; this file is the source.

# Slice 5: payments, the order and the invoice

Status: design proposal (19-Nov-2026), awaiting a reply. No code yet.

Applies plan-architecture §2 (stages 3 and 4), §4, §5, §6 and §9 (the
capture entry), and plan-psp-integration §4 and §5 and
plan-merchant-surfaces §5 (the method and pay steps, the order page).
Where they differ, plan-architecture wins, as its §12 lists. Slice 4 left
the buyer on a quote with a disabled "Pay" (`plan-quote.md` §5, 4d-2).

Out of scope (later slices): refunds and chargebacks (slice 8),
settlement-driven payouts (slice 9), merchant webhooks and emails to
buyers (§9, question 7), any real PSP (none is chosen until the entity is
decided, plan-psp-integration §11).

## 1. Steps

- **5a: pay.** The fake PSP in every mode but settlement, the `psp`
  port, registry and fake client, accounts and routing rows, the
  `payment_attempt` aggregate, the method and pay steps on the buyer
  pages (redirect and QR), waiting without reloads, the order row and the
  capture's ledger entry. Ends: a buyer pays on the sandbox, in each mode,
  and the session is `completed` with its order and entry.
- **5b: the invoice and the order.** Invoice rows with a gapless number,
  rendered as HTML and as PDF, the buyer's order page, the orders API,
  the KYB gate.
- **5c: settlement.** Settlement by file and by API from the fake, the
  real PSP fee clearing the estimate, reconciliation exceptions for a
  person. The echo flow is retired (the fake PSP now exercises all its
  machinery). The fake's card-fields component (the first JavaScript from
  a PSP) and the Playwright smoke suite under the prod CSP
  (plan-architecture §10).

Each step is built, checked and reviewed before the next.

## 2. The fake PSP (5a)

A made-up acquirer under `/_fake/psp/{account}` (`A`), in the same shape
as the fake courier and duty vendor: its own state in schema
`fake_vendor`, its own connections, scripted and never timed.

```
POST A/payments               {"reference", "amount_minor", "currency",
                               "method": card|qr, "return_url"}
     Idempotency-Key (keyed accounts: a key seen before replays the
     first answer, for good)
     201 {"id", "status": "requires_action",
          "action": {"redirect_url"} | {"qr_payload", "expires_at"}}
     402 {"error": "declined", "reason"}       (an amount ending in 51)
GET  A/payments?reference=…   every payment for our reference (lookup)
GET  A/payments/{id}          one payment's state (poll, thin webhook)
POST A/payments/{id}/capture  {"amount_minor"}, Idempotency-Key
POST A/payments/{id}/cancel   only before capture
POST A/webhooks               {"url", "secret", "payload": full|thin}
GET  /_fake/psp/pay/{id}      the hosted page: approve, decline, fail
                              3-D Secure, or leave; then back to return_url
POST A/payments/{id}/release  {"outcome": paid|declined|expired,
                               "push": once|twice|bad_signature}
                              what the hosted page and the QR "phone" do;
                              the fuzzer and the staff page call it too
POST /_fake/psp/faults        script the next call of an operation to fail
```

A payment goes `requires_action → authorised → captured`, or ends
`declined`, `expired` or `cancelled`. On a `sale` account the buyer's
approval captures at once; on an `authorise` account it stops at
`authorised` until we capture.

**Modes**, each an account row of the fake and of ours, so the fuzzer and
the seed can draw them (plan-architecture §6):

| Capability | Values |
|---|---|
| status reaches us by | `push_fat`, `push_thin`, `poll` |
| capture | `sale`, `authorise_capture` |
| guard on create and on capture | `vendor_constraint` (keyed), `none` (plain). Never `compensable`: these move money (§5) |
| methods | `card` (redirect to the hosted page), `qr` |

Faults, on request: 500 before or after doing the work, a timeout after
doing the work, a duplicate webhook, events out of order, a bad
signature, an undecodable body. It counts what it did per reference: the
fuzzer's proof that nothing was charged twice.

## 3. Our side: port, accounts, routing (5a)

`src/mor/psp/`: `port.py` (records and result unions, no provider words),
`registry.py` (one `match` on `PspProvider`, refusing the fake in prod),
`fake_client.py` (httpx2 against `account.base_url`, the amount through
`to_provider_amount`/`from_provider_amount`, plan-psp-integration §4).

```
create_payment(account, PaymentRequest, key) -> PaymentCreated
    | Declined | Unavailable | OutcomeUnknown | UndecodableBody
find_payments(account, reference)            -> list[ProviderPayment] | …
get_payment(account, provider_ref)           -> ProviderPayment | …
capture(account, provider_ref, Money, key)   -> Captured | Refused | …
cancel(account, provider_ref, key)           -> Cancelled | AlreadyFinal | …
verify_webhook, decode_events, decode_thin_webhook   (pure)
```

Tables (`0016_payment_values.sql` for the new enum values, then
`0017_payments.sql`, as 4d-2 split them):

- `psp_account (id, provider, base_url, credentials_ref, status_mode,
  capture_mode, create_guard, capture_guard, methods)`: capability values
  are enum columns, never a branch on the provider in feature code.
- `psp_route (id, merchant_id?, buyer_country?, currency?, psp_account_id,
  effective_from)`: append-only; the most specific row in force wins, as
  fee rules do. Its id is stored on the attempt.

## 4. The payment attempt (5a)

One aggregate per try against one account. The session's `paying` and
`completed` states arrive now.

```
session:  quoted ──pay──▶ paying ──captured──▶ completed
                            └──declined / expired / cancelled──▶ quoted
attempt:  requested ─created─▶ action_required ─approved─▶ authorised
              │                     │                          │ capture
              │                     │                          ▼
              └── declined ◀────────┴──── expired | cancelled  captured
          any state with a lost answer on a `none` guard ─▶ needs_person
```

**Pay** (`POST /c/{session}/pay`, a plain form with the option key and the
method). The handler locks the session and checks, under the lock:

- the session is `quoted`, and the quote is its newest;
- `now < valid_until` (time is the promise here; the lock decides);
- the option and method exist, and routing gives an account for them.

Then it inserts the attempt (`requested`, the quote, option, amount and
currency, account, route), sets the session `paying`, appends
`payment_attempt_created`, commits and answers 303 to `/c/{session}`.
A double submit finds the session already `paying` and changes nothing.

**One open attempt per session**, guarded by a partial unique index on
`payment_attempt (session_id) WHERE state` is not final (question 1). "Pay
another way" on the waiting page appends `payment_cancel_wanted`; the
session returns to `quoted` only once the PSP has confirmed the cancel or
reported a final state. So a buyer can never be charged twice for one
cart by switching methods. A second unique index allows one `captured`
attempt per session: a capture the PSP reports anyway (it broke its own
cancel) becomes `needs_person`, for a refund in slice 8.

**Consumers** (effect consumers follow prepare / call / complete, with a
`psp_request` row per (attempt, operation, number) holding the key):

| Fact | Consumer | Kind | Does |
|---|---|---|---|
| `payment_attempt_created` | `psp_payment_create` | effect | create at the PSP; `action_required` with the redirect or QR, or `declined` |
| `payment_authorised` | `psp_payment_capture` | effect | capture the attempt's amount (authorise accounts only) |
| `payment_cancel_wanted` | `psp_payment_cancel` | effect | cancel; `AlreadyFinal` applies the PSP's real state |
| `psp_inbox_stored` | `psp_inbox_decode` | local | the stored webhook, poll page or thin pointer to our events |
| `psp_event_received` | `payment_event_apply` | local | lock the attempt, apply the event by the transition table |
| `payment_poll_due` | `psp_payment_poll` | effect | fetch the state (poll accounts, and once at the action's expiry on every account) |
| `payment_captured` | `order_create` | local | lock the session: `completed`, the order, the capture entry, `order_paid` |
| `payment_declined`, `_expired`, `_cancelled` | `session_payment_ended` | local | lock the session: back to `quoted` (or `expired` if its time passed meanwhile) |

- The transition table is pure: every (state, event) gives a new state,
  `Stale` or `Parked` (plan-architecture §10), and a property checks it is
  total.
- Time only triggers: the QR's `expires_at` schedules one
  `payment_poll_due`; what the PSP then says decides. No attempt is
  expired by our clock.
- A session in `paying` does not expire: `checkout_session_expire` leaves
  it alone, and `session_payment_ended` expires it if its time passed.
  A captured payment is always honoured (plan-architecture §4).
- The buyer's return from the hosted page proves nothing: the page shows
  "waiting" until the webhook or poll has decided.

**Waiting without reloads** (agreed in 4d-2's open list): the waiting
pages (quote and payment) keep `<meta http-equiv="refresh">` inside
`<noscript>`; `ts/waiting.ts` polls `GET /c/{session}/status` (a small
JSON of the state) every 2 s and reloads the page once it changes. The QR
is drawn on the server as inline SVG (a QR library, checked when added).

## 5. The order and the capture entry (5a)

`order_` (`id`, `session_id` unique, `attempt_id` unique, `merchant_id`,
`quote_id`, `option_no`, `address_id`, `state` = `paid`, `paid_at`,
`buyer_token`): the unique keys are "one order per session" and "one per
capture". `buyer_token` is 32 random bytes for the order page's link
(5b); the session link alone must not show an invoice and address
forever.

The capture entry, keyed `capture:{attempt_id}` (plan-architecture §9),
from the stored quote option's parts:

```
quote currency (say SGD):
  Dr psp_clearing:{psp_account}       captured amount = the option's total
  Cr tax_payable:{country}            seller-collected tax parts
  Cr duties_payable:{courier}         border duty and tax parts, duty-paid fee
  Cr shipping_payable:{courier}       freight, surcharge, fuel parts
  Cr psp_fee_collected                psp_fee part
  Cr revenue_mor_margin               mor_margin and fx parts
  Cr fx_position:{quote currency}     goods parts, per merchant currency
merchant's currency (say USD), per currency its lines use:
  Dr fx_position:{USD}                the merchant's price, exact
  Cr merchant_payable:{merchant}      the merchant's price, exact
```

- `fx_position` is a new account kind: the same account in both
  currencies carries the conversion, so each currency balances and the
  rate (stored on the quote's market load) explains it. When the goods
  are priced in the quote's own currency the two `fx_position` lines are
  skipped and `merchant_payable` is credited directly.
- Sub-unit remainders (question 6): every quote part already stores its
  exact value, its rounded amount and its named mode. The ledger holds
  whole minor units only, so the entry books the rounded parts, which sum
  to the total by the quote's own property; the remainders stay on the
  parts, and the merchant's FX-rounded excess sits in `fx_position`.
- A captured amount other than the attempt's is not booked: the attempt
  goes `needs_person`.
- A property: for any stored quote option, the entry balances per
  currency, and `merchant_payable` equals the merchant's prices exactly.

## 6. The invoice and the order page (5b)

- `invoice (id, order_id unique, series, number, issued_at, currency,
  seller lines, buyer name and address, merchant named)` and
  `invoice_line` (goods with our fee inside, shipping, duties and taxes,
  each tax with its rule and rate): a snapshot, append-only by trigger,
  never re-derived. Issued by `invoice_issue` on `order_paid`.
- Numbers are gapless per series (question 4): `invoice_series (series,
  next_number)` is locked `FOR UPDATE` in the same transaction that
  inserts the invoice, so a rollback gives the number back. This locks a
  second row besides the order, always order then series: the second
  planned exception to "one step locks one aggregate", next to the
  merchant lock (plan-architecture §4).
- Rendered by one htpy function, as a page and through WeasyPrint as a
  PDF, on request, from the rows. What a tax invoice must show per
  country (SG's tax invoice, TH's full and abbreviated ones) is a
  compliance touchpoint: the fields exist, the wording waits for an
  adviser.
- `/o/{buyer_token}`: what was bought, the total, the invoice (page and
  PDF), "what happens next", the merchant's support contact and ours by
  topic (plan-merchant-surfaces §5), and a link back to `return_url`.
- API: `GET /v1/checkout_sessions/{id}` gains `order_id`;
  `GET /v1/orders/{id}` returns lines, totals, the option, the full
  address (for fulfilment, as 4d-2 promised) and the invoice number.

## 7. Settlement (5c)

`import_settlement` per account, by `file` (a CSV the fake cuts on
request, fetched on a schedule or uploaded) and by API page. Each line is
stored raw under a unique key on the vendor's line id, then matched to
an attempt by our reference under that attempt's lock:

```
Dr psp_fee_collected       the estimate (from the capture entry)
Dr/Cr psp_fee_variance     the gap, ours
Cr psp_clearing            the fee the PSP kept
Dr bank:{entity}           settled   (new account kind)
Cr psp_clearing            settled
```

A line that matches nothing, or an amount that differs from the capture,
is a `settlement_exception` row on a staff page. `psp_clearing` per
account reaching zero after each batch is the health figure.

## 8. Testing and fuzzing

- Pure: the transition table is total; the capture entry balances and
  pays the merchant exactly; provider amounts round-trip or report
  `not_representable`; decoders turn any bytes into a record or a typed
  error.
- Pages at their edge: pay with an expired quote shows the current
  price; a double pay makes one attempt; a cross-site POST is refused;
  each state renders; the return from the hosted page shows waiting.
- A Hypothesis state machine for the payment flow against the test
  database and the fake PSP, drawing the account's modes per run.
  Actions: pay (twice), pay another way, the buyer's approve / decline /
  leave, the PSP's webhook once, twice, late, out of order or badly
  signed, a call failing or timing out after the work, a crash between
  prepare, call and complete, the clock passing `valid_until`, the QR's
  expiry and the session's `expires_at`. After draining with faults off:
  - the fake charged each completed session once, for its option's total,
    and nothing else;
  - every captured attempt has exactly one order and one capture entry;
  - no session is `paying`, and none is stuck;
  - with guard `none`, "waiting on a person" is allowed, never a double
    charge.
- Planted bugs to confirm the fuzzer catches them: no open-attempt index,
  expiring a `paying` session, capture applied twice, a decline leaving
  the session `paying`.
- The new fuzzers join soak targets with timed budgets.

## 9. Compliance touchpoints (named, not answered)

- PCI DSS: card data only ever enters the PSP's page or its fields, so we
  stay at the lightest self-assessment. Which questionnaire applies is
  for the PSP and an assessor, not assumed.
- Payment licences (MAS PSA and others) for collecting on a merchant's
  behalf, and which entity holds the PSP contract.
- KYB before a live payment (question 5).
- The statement descriptor `OURS*MERCHANT` (plan-merchant-surfaces §5)
  is a PSP selection criterion; the fake takes one per payment.
- Invoice content and language per country; retention of the order,
  invoice and address.

## 10. Rules this applies

Locks and unique keys, not clocks: the session lock for pay, expiry and
the end of a payment; partial unique indexes for one open and one
captured attempt; time only triggers the expiry poll. Facts only, effect
consumers with a duplicate guard per operation, never `compensable` for
money. One module per provider, one registry, the fake in every mode.
Append-only double-entry ledger, FX as its own account with the rate
stored, named rounding. Typed records at every edge; no handler calls a
vendor. HTML first; the one script only improves a page that works
without it.

## 11. Questions (my recommendation first)

1. **One open attempt per session.** Recommended: a new attempt only
   after the PSP confirmed the old one is final, so switching methods can
   never charge twice. The other choice lets attempts overlap and refunds
   the extra capture, which is simpler for the buyer but needs refunds
   (slice 8) and costs a fee per mistake.
2. **Where the buyer lands after paying.** Recommended: our order page,
   with the invoice and a "Back to <merchant>" link to `return_url`; the
   merchant learns of the order from the API (webhooks later). The other
   choice redirects straight to `return_url`, as Stripe does, and the
   buyer finds the invoice only through a later email.
3. **When to capture.** Recommended: at once after authorisation, on
   every account. We are the seller, the total is final, and the buyer's
   bank sees one charge. Capturing at label booking would protect against
   a merchant that never ships, but holds expire in 7 days at several
   PSPs (plan-psp-integration §2), and QR methods cannot hold at all.
4. **Invoice numbers.** Recommended: one gapless series per selling
   entity, `INV-2026-000001` (year in the prefix, the counter runs on);
   one placeholder entity until it is decided. Per-country series come if
   an adviser says a country needs its own.
5. **KYB, before a live payment.** Recommended: a minimal `kyb_state` on
   the merchant now (`pending`, `approved`, `suspended`, set by the CLI);
   prod refuses the pay step unless `approved`; the sandbox seed approves
   its merchants. The KYB process itself stays a later slice.
6. **Rounding remainders** (amends plan-quote 4c point 7, "booked when
   the order is paid"). Recommended: the ledger books whole minor units;
   the sub-unit remainder of each part stays on its quote part, where it
   is already stored exactly. A `rounding` line would need sub-unit
   amounts the ledger cannot hold.
7. **Buyer emails.** Recommended: not in slice 5. Emails (the order
   link, the invoice) and merchant webhooks come together as one
   `notify` slice right after, with a fake mailbox under `/_fake/`. Until
   then the order link is shown only after paying, which is enough for
   the sandbox.

**Answered 19-Nov-2026:** all seven as recommended, except question 2:
where the buyer lands after paying is the merchant's choice. A merchant
setting, `after_payment`, is `our_page` (the default: `/o/{buyer_token}`
with the invoice and a link back to `return_url`) or `merchant_page` (a
303 to the session's `return_url` with `session_id` added; the merchant
reads the order through the API). Either way the buyer lands only once the
payment is decided, never on the browser's word. Built in 5b with the
order page; until then 5a shows the completed state on `/c/{session}`.

**Vendor sandboxes on the test setup** (decided 19-Nov-2026, recorded in
plan-architecture §6): the test setup may also route chosen merchants to
a real PSP's sandbox once its module exists. 5a prepares for it only by
giving `psp_account` a `kind` (`fake`, `sandbox`, `live`) that the
registry checks against the environment.

**5a built 19-Nov-2026.**

What was built:
- Migrations `0016_payment_values.sql` (enum values, in a file of their
  own) and `0017_payments.sql`; the fake's `migrations_fake/0007_psp.sql`.
- `src/mor/fakes/psp.py`, the fake PSP; `src/mor/psp/` (port, registry,
  fake client, accounts and routing); `src/mor/provider_accounts.py`, the
  `fake` / `sandbox` / `live` check every registry call makes.
- `src/mor/payments/`: the transition table, the attempt store with
  `move` (the one place an attempt changes state and writes its fact),
  the pay and pay-another-way steps, the effect consumers, and inbound.
- `src/mor/orders/`: the order and the capture entry with `fx_position`.
- The buyer page's pay form, waiting, QR (`src/mor/web/qr.py`, inline SVG
  from segno's matrix) and paid states; `ts/waiting.ts` and the
  `/c/{session}/status` route; the PSP webhook; attempts on the staff
  session page; the seed's twelve PSP accounts and default route.
- The API shows the new session states `paying` and `completed` (a
  v1-additive change; `order_id` comes with 5b).

Changed while building:
- **A decline at once is a scripted fault**, not "an amount ending in
  51": a real SG quote total was 61.51 and was declined by chance.
- **A thin webhook's fetch has its own consumer** (`psp_payment_fetch`).
  Deliveries run in order per consumer and aggregate, so a poll scheduled
  for a QR's expiry held back a fetch that was due now.
- **The return URL is stored on the attempt**: the worker makes the PSP
  call and has no web settings.
- **The fakes get a clock**, shared with the tests, so a QR's expiry
  moves with the test's time.
- **The fuzzer checks "one payment per attempt at the PSP" directly.** A
  planted resend on a `none` guard was first missed: a duplicate payment
  only became a double charge if the buyer then paid both.

How it was checked:
- Tests through the pages and the fake for every wiring, lost answers on
  both guards, declines, pay another way (and a capture that beats the
  cancel), webhooks badly signed, twice and out of order, a session paid
  past its life, and goods priced in USD.
- Properties: the transition table, the capture entry (balances, the
  merchant paid exactly), the fake's amounts (IDR's sub-unit refused).
- The fuzzer at 300 examples; planted bugs caught: a resend on a `none`
  guard, and expiring a session while it is paid.
- On the sandbox in a headless browser at phone width: a QR payment
  completed while the page waited and reloaded by itself; a card payment
  through the fake's hosted page; both capture entries balanced, the USD
  one paying the merchant exactly 18.00 USD. No console errors but the
  ones explained.

Open:
- The capture entry is dated when the order is made, not when the PSP
  captured. The PSP's time is on its event; use it when settlement (5c)
  needs it.
- Two workers applying captures of two attempts of one session at the same
  moment: one hits the one-capture index, raises, and its retry hands the
  second to a person. Correct, but noisy; seen by reasoning, not by test.
- The QR was not scanned with a phone here (no decoder installed).
- Another wiring on the sandbox needs a `psp_route` row written by hand;
  a staff form can come with staff sign-in.
- KYB gate, invoice, order page, `after_payment`: 5b. Echo retirement,
  card-fields component, Playwright smoke suite: 5c.

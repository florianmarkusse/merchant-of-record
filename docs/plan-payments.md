# Payments, the order, the invoice and settlement (slice 5)

Status: built, 5a to 5c-3 (19-Nov to 25-Nov-2026) (§1).

Applies `plan-architecture.md` §2 (stages 3 and 4), §4, §5, §6 and §9
(the capture entry) to payments against the fake PSP. Slice 4 left the
buyer on a quote (`plan-quote.md` §5); this slice takes the payment,
makes the order and its invoice, and books what the PSP settles.

Out of scope (later slices): refunds and chargebacks (slice 8),
payouts to merchants (slice 9), merchant webhooks and emails to buyers
(§11), any real PSP (none is chosen until the entity is decided,
`plan-psp-integration.md` §10).

## 1. Steps

- **5a: pay** (built 19-Nov-2026). The fake PSP in every mode but
  settlement, the `psp` port, registry and fake client, accounts and
  routing rows, the `payment_attempt` aggregate, the method and pay steps
  on the buyer pages (redirect and QR), waiting without reloads, the
  order row and the capture's ledger entry.
- **5b: the invoice and the order** (built 20-Nov-2026). Invoice rows
  with a gapless number, rendered as HTML and as PDF, the buyer's order
  page, the orders API, the KYB gate.
- **5c-1: settlement** (built 20-Nov-2026, reworked 23-Nov-2026 to what
  real PSPs send): settlement by file and by API from the fake, the real
  PSP fee clearing the estimate, each payout reconciled with its lines
  (§7).
- **5c-2: the bank statement** (built 25-Nov-2026): a fake bank and
  the import of its transactions, every entry booked, each PSP payout
  matched to its credit, which moves it from `payout_in_transit` to
  `bank`; a payout whose credit arrived but whose lines do not add up is
  flagged on the staff settlement page (§7, the bank statement).
- **5c-3: the browser side** (built 25-Nov-2026): the fake's card
  fields (the first PSP JavaScript, under the CSP the app sends) and the
  Playwright smoke suite (§4, card fields; `plan-architecture.md` §10).

The echo flow stays until the shipment slice: it is the only flow that
exercises the `compensable` guard (money is never `compensable`) and a
status derived from a set of events, both of which come back with carrier
booking and tracking.

## 2. The fake PSP

A made-up acquirer under `/_fake/psp/{account}` (`A`), in the same shape
as the other fakes: its own state in schema `fake_vendor`, its own
connections, scripted and never timed.

```
POST A/payments               {"reference", "amount_minor", "currency",
                               "method": card|qr, "return_url"}
     Idempotency-Key (keyed accounts: a key seen before replays the
     first answer, for good)
     201 {"id", "status": "requires_action",
          "action": {"redirect_url"} | {"qr_payload", "expires_at"}}
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
`authorised` until we capture. A decline at once is a scripted fault,
never a property of the amount (a real quote total ending in 51 was once
declined by chance).

**Modes**, each an account row of the fake and of ours, so the fuzzer and
the seed can draw them (`plan-architecture.md` §6):

| Capability | Values |
|---|---|
| status reaches us by | `push_fat`, `push_thin`, `poll` |
| capture | `sale`, `authorise_capture` |
| guard on create and on capture | `vendor_constraint` (keyed), `none` (plain). Never `compensable`: these move money |
| methods | `card` (redirect to the hosted page), `qr` |
| settlement reaches us by | `api` (Stripe's shape), `file` (Adyen's shape), §7 |

Faults, on request: 500 before or after doing the work, a timeout after
doing the work, a duplicate webhook, events out of order, a bad
signature, an undecodable body. It counts what it did per reference: the
fuzzer's proof that nothing was charged twice. It runs on the clock the
tests give it, so a QR's expiry moves with the test's time.

## 3. Our side: port, accounts, routing

`src/mor/psp/`: `port.py` (records and result unions, no provider words),
`registry.py` (one `match` on `PspProvider`, refusing an account kind the
environment does not allow), `fake_client.py` (httpx2 against
`account.base_url`, converting amounts to and from the provider's
exponent with `to_provider_amount`/`from_provider_amount`; a provider
amount that is not whole in ours is `not_representable` or rounded at
import with a named mode, as `AGENTS.md` Money says).

```
create_payment(account, PaymentRequest, key) -> PaymentCreated
    | Declined | Unavailable | OutcomeUnknown | UndecodableBody
find_payments(account, reference)            -> list[ProviderPayment] | …
get_payment(account, provider_ref)           -> ProviderPayment | …
capture(account, provider_ref, Money, key)   -> Captured | Refused | …
cancel(account, provider_ref, key)           -> Cancelled | AlreadyFinal | …
verify_webhook, decode_events, decode_thin_webhook   (pure)
```

- `psp_account (id, provider, kind, base_url, credentials_ref,
  status_mode, capture_mode, create_guard, capture_guard, methods,
  settlement_mode, settlement_interval_s)`: capability values are enum
  columns, never a branch on the provider in feature code. `kind` is
  `fake`, `sandbox` or `live` (`plan-architecture.md` §6).
- `psp_route (id, merchant_id?, buyer_country?, currency?, psp_account_id,
  effective_from)`: append-only; the most specific row in force wins, as
  fee rules do. Its id is stored on the attempt. Another wiring on the
  sandbox needs a route row written by hand until staff sign-in brings a
  form.

## 4. The payment attempt

One aggregate per try against one account.

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
- the merchant's KYB state is `approved` (§6);
- the option and method exist, and routing gives an account for them.

Then it inserts the attempt (`requested`, the quote, option, amount and
currency, account, route, return URL: the worker makes the PSP call and
has no web settings), sets the session `paying`, appends
`payment_attempt_created`, commits and answers 303 to `/c/{session}`. A
double submit finds the session already `paying` and changes nothing.

**One open attempt per session** (decided 19-Nov-2026), guarded by a
partial unique index on `payment_attempt (session_id)` over the states
that are not final. "Pay another way" on the waiting page appends
`payment_cancel_wanted`; the session returns to `quoted` only once the
PSP has confirmed the cancel or reported a final state. So a buyer can
never be charged twice for one cart by switching methods; letting
attempts overlap would need refunds and cost a fee per mistake. A second
unique index allows one `captured` attempt per session: a capture the
PSP reports anyway (it broke its own cancel) becomes `needs_person`, for
a refund in slice 8.

**Capture at once after authorisation**, on every account (decided
19-Nov-2026): we are the seller, the total is final, and the buyer's bank
sees one charge. Capturing at label booking would guard against a
merchant that never ships, but holds expire in 7 days at several PSPs,
and QR methods cannot hold at all.

**Consumers** (effect consumers through `run_steps`,
`plan-messaging.md` §6):

| Fact | Consumer | Kind | Does |
|---|---|---|---|
| `payment_attempt_created` | `psp_payment_create` | effect | create at the PSP; `action_required` with the redirect or QR, or `declined` |
| `payment_authorised` | `psp_payment_capture` | effect | capture the attempt's amount (authorise accounts only) |
| `payment_cancel_wanted` | `psp_payment_cancel` | effect | cancel; `AlreadyFinal` applies the PSP's real state |
| `psp_inbox_stored` | `psp_inbox_decode` | local | the stored webhook, poll page or thin pointer to our events |
| `payment_events_wanted` | `psp_payment_fetch` | effect | a thin webhook's fetch of the payment's state |
| `psp_event_received` | `payment_event_apply` | local | lock the attempt, apply the event by the transition table |
| `payment_poll_due` | `psp_payment_poll` | effect | fetch the state (poll accounts, and once at the action's expiry on every account) |
| `payment_captured` | `order_create` | local | lock the session: `completed`, the order, the capture entry, `order_paid` |
| `payment_declined`, `_expired`, `_cancelled` | `session_payment_ended` | local | lock the session: back to `quoted` (or `expired` if its time passed meanwhile), only for the session's newest attempt |

- The transition table is pure: every (state, event) gives a new state or
  `StaleEvent`, and a property checks it is total.
- Time only triggers: the QR's `expires_at` schedules one
  `payment_poll_due`; what the PSP then says decides. No attempt is
  expired by our clock.
- A session in `paying` does not expire: `checkout_session_expire` leaves
  it alone, and `session_payment_ended` expires it if its time passed.
  A captured payment is always honoured (`plan-architecture.md` §4).
- `session_payment_ended` acts only on the newest attempt: an earlier
  attempt's end, met again, must not set a session back to `quoted`
  under its open second attempt.
- The buyer's return from the hosted page proves nothing: the page shows
  "waiting" until the webhook or poll has decided.

**Card fields** (5c-3, decided 25-Nov-2026). Real PSPs put the card
form on the merchant's page inside their own iframe: Stripe's Payment
Element mounts from `js.stripe.com` with a `client_secret` made by the
server, and `confirmPayment` sends the browser to `return_url`, whose
query parameters are not to be trusted; Adyen's card component and
Xendit's card session work alike. Stripe's CSP needs `js.stripe.com` in
`script-src` and `js.stripe.com hooks.stripe.com` in `frame-src`
(3-D Secure fails silently without the latter). Sources:
[Stripe security guide](https://docs.stripe.com/security/guide),
[Stripe confirmPayment](https://docs.stripe.com/js/payment_intents/confirm_payment),
[Adyen script security](https://docs.adyen.com/development-resources/pci-dss-compliance-guide/script-security),
[Xendit card sessions](https://docs.xendit.co/docs/cards-save-a-card)
(read 25-Nov-2026).

- A card payment's create answer carries a `fields_secret` beside the
  hosted page's URL, stored as `payment_attempt.action_fields_secret`.
- With JavaScript the buyer sees only the card form on our page; the link
  to the PSP's hosted page stays for a browser without it, or where the
  PSP's script does not load (our glue hides the link only once the
  fields are mounted). A real PSP without a hosted page for the same
  payment leaves such a buyer the QR method.
- The PSP's script and frame come from the PSP's origin; the registry
  names them per provider (`card_fields`), and they join the CSP the app
  sends (`src/mor/web/csp.py`). Our glue per provider (`ts/`) mounts the
  fields; nothing else on our page names a provider.
- The card never touches our page: it is typed and posted inside the
  PSP's frame. The outcome still comes only from the PSP (webhook or
  poll); the return to `return_url` shows "waiting".
- The fake, in Stripe's shape: `GET /_fake/psp/js/fields.js` (its
  script, `window.FakePsp.mount(element, secret)`) and
  `/_fake/psp/fields/{secret}` (the frame), served from a different
  origin than our pages (`FAKE_PSP_BROWSER_ORIGIN`, default
  `http://localhost:8741` beside `127.0.0.1:8741`), so `'self'` hides no
  CSP mistake. Test cards by number: `4242…` approves, `4000…0002`
  declines, `4000…3220` asks for a 3-D Secure challenge (pass or fail).
  The frame's form posts to the PSP's top-level page, which ends at
  `return_url`.
- Browser journeys run in a Playwright smoke suite (`tests_browser/`, not
  in `uv run pytest`): its own database `mor_smoke`, server and worker on
  port 8742, every journey under the app's CSP and failing on any
  "Refused to" console message or page error: card fields approved, a
  declined card then paying again, 3-D Secure, the hosted page without
  JavaScript, QR. It runs nightly beside the soak.

**Waiting without reloads**: the waiting pages (quote and payment) keep
`<meta http-equiv="refresh">` inside `<noscript>`; `ts/waiting.ts` polls
`GET /c/{session}/status` (a small JSON of the state) every 2 s and
reloads the page once it changes. The QR is drawn on the server as inline
SVG.

## 5. The order and the capture entry

`order_` (`id`, `session_id` unique, `attempt_id` unique, `merchant_id`,
`quote_id`, `option_no`, `address_id`, `state` = `paid`, `paid_at`,
`buyer_token`): the unique keys are "one order per session" and "one per
capture". `buyer_token` is 32 random bytes for the order page's link; the
session link alone must not show an invoice and address forever.

The capture entry, keyed `capture:{attempt_id}`
(`plan-architecture.md` §9), from the stored quote option's parts:

```
quote currency (say SGD):
  Dr psp_clearing:{psp_account}       captured amount = the option's total
  Cr tax_payable:{country}            seller-collected tax parts
  Cr duties_payable:{carrier}         border duty and tax parts, duty-paid fee
  Cr shipping_payable:{carrier}       freight, surcharge, fuel parts
  Cr psp_fee_collected                psp_fee part
  Cr revenue_mor_margin               mor_margin and fx parts
  Cr fx_position:{quote currency}     goods parts, per merchant currency
merchant's currency (say USD), per currency its lines use:
  Dr fx_position:{USD}                the merchant's price, exact
  Cr merchant_payable:{merchant}      the merchant's price, exact
```

- `fx_position`: the same account in both currencies carries the
  conversion, so each currency balances and the rate (stored on the
  quote's market load) explains it. When the goods are priced in the
  quote's own currency the two `fx_position` lines are skipped and
  `merchant_payable` is credited directly.
- **Sub-unit remainders stay on the quote parts** (decided 19-Nov-2026):
  every part already stores its exact value, its rounded amount and its
  named mode. The ledger holds whole minor units only, so the entry books
  the rounded parts, which sum to the total by the quote's own property;
  the merchant's FX-rounded excess sits in `fx_position`. A `rounding`
  line would need sub-unit amounts the ledger cannot hold.
- A captured amount other than the attempt's is not booked: the attempt
  goes `needs_person`.
- The entry is dated when the order is made, not when the PSP captured;
  the PSP's time is on its event, for when something needs it.
- A property: for any stored quote option, the entry balances per
  currency, and `merchant_payable` equals the merchant's prices exactly.

## 6. The invoice, the order page and the merchant

**KYB** (decided 19-Nov-2026: minimal now, the KYB process itself a later
slice):

- `merchant_kyb_state` enum `pending`, `approved`, `suspended`;
  `merchant.kyb_state` defaults to `pending`. Every change is also a row
  in `merchant_kyb_change (merchant_id, state, changed_at, actor)`,
  append-only, written in the same transaction: the column is what the
  pay step reads, the table is who changed it and when.
- KYB is ours, never the merchant's: set by the CLI
  (`uv run python -m mor.merchants kyb mer_… approved`) until staff
  sign-in exists, then by a staff page. The seed approves its merchants.
- The gate applies in every environment, so the code that guards prod is
  the code under test (decided 20-Nov-2026). The pay step reads
  `kyb_state` with `FOR SHARE` under the session lock and refuses a new
  attempt unless `approved`; the CLI's change takes the row `FOR UPDATE`,
  so a suspension and a pay cannot cross. The buyer sees "This shop
  can't take payments right now", and no PSP call is made. An attempt
  already open runs to its end.

**The merchant's own settings** go through the API with its key, not the
CLI (decided 20-Nov-2026): `POST /v1/merchant/settings` sets
`support_email` (or null), with `settings_changed_at` and its actor on
`merchant`. Plain settings, not financial state, so no history table. The
dashboard later calls the same code. Settings that hold for every
checkout live on the merchant; what can differ per checkout lives on the
session.

**Where the buyer lands after paying is per session** (decided
19-Nov-2026, per session 20-Nov-2026, as Stripe sets its success URL per
session): `after_payment` is a field of the create-session body next to
`return_url`, stored on `checkout_session`, never a query parameter on
the buyer's link, which the buyer can edit. `/c/{session}` in state
`completed`:

- `our_page` (the default): a 303 to `/o/{token}`, until the session's own
  `expires_at`; after that `/c/{session}` shows "This order is paid" and
  no link, so a forwarded checkout link does not show the invoice and
  address for ever.
- `merchant_page`: a 303 to `return_url` with `session_id=cs_…` added to
  its query (kept if the URL already has one); the merchant reads the
  order through the API.

Both happen only in `completed`, which only `order_create` sets after the
PSP's capture: the browser's return never decides it. The waiting
script's reload follows the redirect.

**The selling entity.** One place, `src/mor/entity.py`: our legal name,
address and tax registration numbers per country, as plain data, with
placeholders until the entity is decided. The invoice copies what it
uses.

**The invoice** (`src/mor/invoices/`):

- **Numbering is data**, so it can grow without touching issued
  invoices. A series (code, entity, prefix, with or without the year,
  width) is plain data in `src/mor/invoices/numbering.py`;
  `invoice_series (code, next_number)` holds only its counter, a row made
  on the series' first use (insert, a conflict means it exists), so a new
  series needs no migration and survives the test setup emptying every
  table. Today one series, `INV`, for the placeholder entity (decided
  19-Nov-2026: one gapless series per selling entity; per-country series
  if an adviser says a country needs its own). `series_for(entity,
  buyer_country)` picks it; a per-country series is an entry in `SERIES`
  and in `SERIES_BY_COUNTRY`, and credit notes (slice 8) are a series of
  their own. A code is never reused. Each invoice stores its series code,
  its number and the finished `number_text`, so a later format change
  never rewrites an issued number. The number exists because tax law asks
  for it (IRAS: an identifying number; TH Revenue Code §86/4: a serial
  number) and a gap shows a missing invoice, which a uuid cannot.
- `invoice (id, order_id UNIQUE, series, number, number_text, issued_at,
  paid_at, currency, total_minor, seller_name, seller_address,
  seller_tax_label, seller_tax_id, merchant_legal_name, buyer_name,
  buyer address fields)`, `UNIQUE (series, number)`. `number_text` is
  `INV-2026-000001`: prefix, the issue year, then the series' running
  counter, which does not restart each year. Name and address are
  copied, not referenced, because an invoice is kept for the legal
  retention period and the session's address may not be. Email and phone
  are not copied.
- `invoice_line (invoice_id, line_no, section, description, quantity,
  amount_minor, rule_text, source)`. `section` is an enum: `goods` (one
  line per session line, the merchant's price plus its share of our fees
  in whole minor units by `money.rounding.allocate`, weighted by the
  rounded goods parts, so the lines sum to the buyer's "Items"),
  `shipping` (one line: the option's service name, the shipping group),
  `tax` (each seller-collected tax part: its label as `rule_text`, its
  source) and `border` (each border duty and tax, "paid on your behalf").
  The rate stays inside the label text: parts store how they were
  reached, not a rate field.
- Both tables append-only by trigger (`refuse_change`).
- **Issued by a local consumer** `invoice_issue` on `order_paid`: lock
  the order, return `Stale` if its invoice exists (backed by
  `UNIQUE (order_id)`), lock the series row, take the number, insert
  invoice and lines, emit `invoice_issued`. One transaction, so a
  rollback gives the number back. Lock order always order, then series.
  Kept apart from `order_create` on purpose: a bug in the invoice code
  must not hold back the order and the capture's ledger entry. No ledger
  entry: the capture already booked the tax as owed.
- The printed dates are the UTC date (`20-Nov-2026`) of `issued_at` and
  of `paid_at`. Whether a country's tax point must be its local date is
  for the adviser (§9).

**Rendering.** One htpy function draws the invoice body from the rows;
the page wraps it in the site layout, the PDF wraps it in a print
stylesheet (`static/css/invoice.css`) and goes through WeasyPrint in
`asyncio.to_thread` (rendering blocks). WeasyPrint gets a URL fetcher
that refuses everything, so nothing in an invoice can make the server
fetch a URL. WeasyPrint (BSD-3-Clause) needs Pango from the system:
installed on this laptop, and a line in the deploy script for the server.

**The order page** `/o/{buyer_token}`:

- What was bought (the invoice's goods lines), shipping by which service
  and transit days, the total, the delivery address, the invoice as a
  page (`/o/{token}/invoice`) and a PDF (`/o/{token}/invoice.pdf`), "what
  happens next" (the merchant ships it; nothing more is due on
  delivery), the merchant's support email if it has one, our support,
  and "Back to <merchant>".
- Until the invoice exists the page says "Your invoice is being
  prepared" and waits like the other waiting pages (`/o/{token}/status`).
- The token is in the URL, so the page sends `Referrer-Policy:
  no-referrer` (the back link must not hand the token to the merchant's
  site) and `X-Robots-Tag: noindex`. An unknown token is a plain 404.
- The link does not expire for now: it is the buyer's receipt, needed for
  returns and warranty. Revisited with the notify slice, which can offer
  "send me my link again" (Stripe's hosted invoice links expire after 30
  to 120 days and the API hands out a fresh one).

**API** (v1-additive):

- `GET /v1/checkout_sessions/{id}` has `order_id` (`ord_…`, null until
  paid).
- `GET /v1/orders/{id}`, for the merchant that owns it (another's is a
  404): state, `paid_at`, currency and total, the lines as the merchant
  sent them, the option (service, name, transit), the full address with
  email and phone (for fulfilment), `invoice_number` (null until issued),
  and `order_url`: the order page's address, so a merchant on
  `merchant_page` can show the buyer "View your invoice" before emails
  exist.

**Staff.** The session page shows the order and its invoice number.

## 7. Settlement

**What real PSPs send** (read 23-Nov-2026):

- **Adyen: one file per payout batch** (`settlement_detail_report_batch_<N>.csv`,
  "generated every time we make a payout to your bank account"), announced
  by a `REPORT_AVAILABLE` webhook (a thin push). Rows by journal type:
  `Settled`, `Refunded`, `Chargeback`, `Fee`, one `MerchantPayout` (the
  amount sent to the bank), and `Balancetransfer`: Adyen "can adjust the
  amount paid out to your bank account by transferring funds between
  settlement batches". No total or count row; corrections show up as
  transfers into later batches.
- **Stripe: the payout and its lines are separate objects.** The payout
  has `amount`, `currency`, `arrival_date`, `status` and
  `reconciliation_status` (`in_progress`, `completed`, `not_applicable`
  for manual payouts). Its lines are balance transactions listed with
  `payout=po_…`, cursor-paged (`starting_after`, at most 100, newest
  first, `has_more`), once the payout is `completed`.
- **Xendit: no batch.** A transaction's `settlement_status` goes
  `PENDING` to `SETTLED` (it reached the Xendit balance); money reaches
  the bank by a withdrawal whose amount comes from the balance, reported
  by email or report, with no withdrawal API.

Sources: [Stripe payout reconciliation](https://docs.stripe.com/payouts/reconciliation),
[Stripe balance transactions](https://docs.stripe.com/api/balance_transactions/list),
[Stripe payout object](https://docs.stripe.com/api/payouts/object),
[Adyen settlement details report](https://docs.adyen.com/reporting/settlement-reconciliation/transaction-level/settlement-details-report),
[Adyen batch-level reconciliation](https://docs.adyen.com/reporting/settlement-reconciliation/batch-level),
[Xendit transaction status](https://docs.xendit.co/docs/transaction-status.md),
[Xendit auto-withdrawal](https://docs.xendit.co/docs/auto-withdrawal.md).

**The atomic things**, each with the vendor's own id, so each is stored
once whatever order it arrives in and however often:

1. a line settled (an Adyen row, a Stripe balance transaction, a Xendit
   transaction turning `SETTLED`): `settlement_line`;
2. a payout reported, the PSP saying it sent this net to the bank (an
   Adyen `MerchantPayout` row, a Stripe payout): `settlement_payout`;
3. a line belonging to a payout: Adyen by file, Stripe by the payout
   filter, Xendit not at all.

Pages are not one of them: a page is how far our reader got through a
list. So nothing depends on the order of pages, lines and payouts.

**Modes** (decided 23-Nov-2026: Stripe's and Adyen's shapes now;
Xendit's balance shape with the first Xendit module):

- `api`, Stripe's shape: `GET A/payouts?after=` lists payout ids, oldest
  first; `GET A/payouts/{id}` is the payout object, whose
  `reconciliation_status` stays `in_progress` for 10 minutes after the
  cut (its lines answer 409 until then); `GET A/payouts/{id}/lines?starting_after=`
  pages its lines newest first, two a page, with `has_more`.
- `file`, Adyen's shape: one CSV per batch, its payout a
  `merchant_payout` row, listed by our poll (`REPORT_AVAILABLE` as a thin
  push is a later capability).

Both read only, so neither needs a guard: a repeat after a crash is
absorbed by the inbox and the vendor's ids.

**The fake** cuts a batch per currency of every captured payment not
settled yet (`POST A/settlements {"faults": [...]}`, called by the staff
page, the seed and the fuzzer). Fees are its own made-up rule per method
(card a percentage plus a fixed part, QR a percentage), in its own minor
units: IDR has 2, so an IDR fee has sub-rupiah parts, as Stripe's would.
Faults per cut: a line for a reference nobody made, a gross that differs
from the capture, a payment settled again under a new line id, a line of
a type we do not know, a net that is not what the lines add up to, a
`balance_transfer` that holds 3.00 back for the account's next batch in
that currency (which pays it with a transfer line), and the list or fetch
answering 500 or timing out.

**Reading** (the one inbound path, `plan-messaging.md` §7):

```
psp_settlement_due (scheduled, per account)
  └▶ psp_settlement_fetch (effect, reads only): one look per delivery, at
     most one document, as every report reader does
       └▶ psp_inbox (raw, with request_ref: the payout whose lines a page
          lists, since a Stripe line page does not name its payout)
            └▶ psp_inbox_decode (per account): stores each line and payout
               once by the vendor's id, one fact per new row
                 ├▶ settlement_line_received ─▶ settlement_line_book
                 │                             settlement_payout_reconcile
                 └▶ settlement_payout_reported ─▶ settlement_payout_book
                                                  settlement_payout_reconcile
```

The read cursor (`psp_settlement_cursor`) in `api` mode is a phase:
`payout` (fetch the payout object; once `completed`, go on) or `lines`
with the last line id read (`starting_after`); no page number. A payout
still `in_progress` is looked at again after the account's interval.

**Booking a line** (`settlement_line_book`, under the attempt's lock):

- The line's attempt is found when the line is stored (our reference and
  the PSP's payment id must agree with one attempt of this account) and
  never changes, so the table can read it to choose which row to lock.
  Every booking step locks the attempt and settles all its pending lines
  in the order the PSP sent them: a payment settled twice keeps its first
  line, whichever delivery runs first.
- The attempt must be `captured`, the line's gross and currency equal to
  what was captured, and no other line booked for it (a partial unique
  index on `settlement_line (attempt_id) WHERE type = 'capture' AND
  state = 'booked'`).
- The entry, keyed `settlement:{account}:{vendor line id}`, in the line's
  currency, dated by the line's `settled_at`:

  ```
  Dr psp_fee_collected               the estimate, read from the capture
                                     entry's own line (what was booked,
                                     not recomputed from the quote)
  Cr psp_clearing:{account}          the fee, rounded to our exponent
  Dr/Cr psp_fee_variance:{account}   the gap, ours (skipped when zero)
  ```

- The net stays in `psp_clearing` until the payout. A fee in sub-units
  of our exponent (IDR 1,234.56) is rounded half-even at import, the one
  place that rounds it; the exact value and the mode are stored on the
  line.
- Anything that does not fit is a `settlement_exception` and books
  nothing: `unknown_reference`, `not_captured` (the attempt ended
  otherwise, or is a second capture already with a person),
  `amount_differs`, `currency_differs`, `settled_twice`, `unknown_type`.
- A line for an attempt still open (settlement got ahead of our webhook)
  asks for the payment's state (`payment_events_wanted`, as a thin
  webhook does) and waits. Time only triggers the retry; the attempt's
  state under the lock decides. The PSP's settling it is not taken as
  the capture itself.
- A `balance_transfer` line is `counted`: it books nothing (it moves
  money inside the PSP's balance, `psp_clearing` for us) and counts
  towards its payout's sum.

**Booking the payout** (`settlement_payout_book`), keyed
`payout:{account}:{vendor payout id}`, as reported, without looking at
the lines:

```
Dr payout_in_transit:{account}   the net the PSP says it paid
Cr psp_clearing:{account}        the same
```

The PSP says the money left it; it is not in our bank until the bank's
statement shows it. The bank match moves it from `payout_in_transit` to
`bank:{bank account}` (below), and only `bank` is money we pay merchants from (decided
20-Nov-2026: a payout to a merchant must never spend money that is not in
our bank yet).

**Reconciling a payout** (`settlement_payout_reconcile`, local, on each
of its lines and on the payout), under the payout's row lock, from
whatever has arrived:

- no payout row yet: nothing to do; the payout's own fact runs it again;
- balanced: the reported net is the exact sum of its lines' `gross − fee`
  rounded either way to our exponent. Never a tolerance;
- otherwise open, with the difference stored.

Each run sets the payout's booked rounding to its target (the remainder
when balanced, zero when open) with a new entry for the change, keyed
`payout-rounding:{account}:{payout}:{n}`. It converges because every
stored line sends a fact after its commit, so the last run sees every
line; two runs serialize on the payout's row lock. A payout that balanced
by coincidence and then reopens is reversed by a new entry, never edited.
A line in another currency than its payout is left out of the sum, so
the payout stays open and shows it.

**An open difference is shown, not raised** (decided 23-Nov-2026). Open
is normal for a while (lines are listed after the payout, pages arrive
one by one, a balance transfer waits for the next batch), so the staff
settlement page lists open payouts with the difference and how long it
has been open. Once its bank credit is matched, a payout still open is
flagged there and takes a note (the bank statement, below).

**The health figure.** `psp_clearing` per account and currency, beside
the captures the PSP has not settled yet: zero once everything captured
is settled and paid out. Beside it, `payout_in_transit` per account: what
the PSP says it sent and the bank has not shown yet.

**Tables**: `settlement_payout (id, account_id, vendor_payout_id,
currency, net, paid_out_on, reconciliation (open, balanced),
difference, rounding_minor, rounding_entries, reconciled_at, booked_at,
…)`, unique on `(account_id, vendor_payout_id)`; `settlement_line (id,
account_id, vendor_line_id, vendor_payout_id, type, provider_payment_id,
our_reference, gross, fee_exact, fee_minor, fee_rounding, currency,
attempt_id, settled_at, state)`, unique on `(account_id, vendor_line_id)`
and joined to its payout by `(account_id, vendor_payout_id)`, so a line
needs no payout row to exist; types `capture`, `balance_transfer` and
`unknown` (refunds and chargebacks join in slice 8); states `received`,
`waiting`, `booked`, `counted`, `exception`. `settlement_exception (id,
line_id, kind, detail, created_at)` and `settlement_exception_resolved
(exception_id unique, note, actor, resolved_at)`, append-only.

**Resolving an exception** records that a person looked, with a note, on
the staff page, and books nothing (decided 20-Nov-2026: typed
corrections, a write-off or an attribution to the merchant, would guess
at cases no real PSP has shown us yet; staff work happens in the
browser). Like the other staff POSTs it is mounted outside prod only
until staff sign-in exists, and guarded against cross-site posts. The
sandbox page has "cut a settlement" per account, with a fault to pick.

**Settlement currency** (decided 20-Nov-2026): a batch settles in the
captured currency only, as most SEA acquirers pay local currency into a
local account. A PSP that converts at settlement is a new capability
value, with the PSP's rate as an FX ledger event, added with the first
real PSP that needs it, as a separate step after the line is booked. The
conversion into the merchant's currency is ours: the capture already owes
the merchant in its currency through `fx_position`, and the real trade
clears it between settlement and the merchant's payout (slice 9).

### The bank statement (5c-2)

A PSP's payout is only a claim until our bank shows the credit. 5c-2
reads our bank accounts, books every entry they show, and matches each
payout to its credit, which moves it from `payout_in_transit` to `bank`
(decided 25-Nov-2026).

**What real banks offer** (read 25-Nov-2026): an end-of-day statement
(ISO 20022 camt.053, or the older MT940) as a file, and more and more a
JSON API. ABN AMRO's Business Account Insight API (Dutch business
accounts only, OAuth client credentials over mutual TLS, a fee agreed at
onboarding) lists `/transactions` per IBAN with a `transactionId`, the
remittance text in `descriptionLines`, the counterparty, and a
`nextPageKey`; `/balances` gives the booked balance; its push product is
a pilot. DBS RAPID (SG) is a JSON API too. Wise keeps a statement per
currency balance (`balance-statements/{balanceId}`), Airwallex lists
transactions per global account, one per currency. camt.053's own entry
reference (`AcctSvcrRef`) is not unique by the standard, so each bank
module names the id its bank promises. On the PSP side Adyen puts its
payout reference `TX…XT` in the transfer's description, which the bank
may cut short; Stripe's best key is the bank's trace id, known up to 10
days after the payout, and its descriptor defaults to "STRIPE".

Sources: [ABN AMRO Business Account Insight](https://developer.abnamro.com/api-products/business-account-insight/overview),
[ABN AMRO statements](https://www.abnamro.nl/en/commercialbanking/products/payments/sepa/reporting/index.html),
[DBS RAPID](https://www.dbs.com.sg/corporate/solutions/digital/rapid),
[Wise balance statement](https://docs.wise.com/api-reference/balance-statement),
[Airwallex global accounts](https://www.airwallex.com/docs/api/core_resources/global_accounts),
[SAP on AcctSvcrRef](https://userapps.support.sap.com/sap/support/knowledge/en/3745593),
[Adyen settlement details report](https://docs.adyen.com/reporting/settlement-reconciliation/transaction-level/settlement-details-report),
[Stripe trace ID](https://docs.stripe.com/payouts/trace-id).

**Which bank is open** (`README.md`, decisions waiting on people): it
follows the entity, and it must hold the SEA currencies we settle in
(settlement currency, above), so it is a multi-currency account (Wise,
Airwallex), local accounts per currency, or both beside a Dutch account.
The design holds for each: a **bank account is one currency balance at
one provider** (an IBAN at ABN AMRO, a Wise balance id, an Airwallex
global account), and a bank provider is a module behind one port, as a
PSP is.

**Our side**:

- `bank_account (id, provider, kind, base_url, credentials_ref,
  external_ref, currency, statement_interval_s)`, `kind` `fake`,
  `sandbox` or `live`, refused by the registry where the environment
  does not allow it (`plan-architecture.md` §6). `external_ref` is what
  the bank calls the account (IBAN, balance id).
- `psp_payout_destination (psp_account_id, currency, bank_account_id)`,
  one row per PSP account and currency, as a PSP account is configured
  with where it pays out. A payout is matched only within its
  destination; a payout with none stays in transit and is shown so.
- `src/mor/bank/`: `port.py`, `registry.py` (one `match` on the
  provider), `fake_client.py`. The port reads only:
  `list_transactions(account, page_key) -> TransactionsPage | …` and
  `decode_transactions` (pure). Reads need no guard.

**Reading**, the one inbound path (`plan-messaging.md` §7):

```
bank_statement_due (scheduled, per bank account)
  └▶ bank_statement_fetch (effect, reads only): one page per look, after
     the cursor, the bank's own page key; more promised looks again now,
     otherwise after the account's interval
       └▶ bank_inbox (raw) ─▶ bank_inbox_decode: each entry stored once by
          (bank_account_id, vendor_entry_id) and booked in the same
          transaction ─▶ bank_entry_received
             └▶ bank_entry_match
settlement_payout_reported ─▶ bank_entry_match
```

**Booking an entry**, on import, keyed `bank-entry:{bank account}:{entry
id}`, dated by its booking date, whatever it is:

```
Dr bank:{bank account}            the amount (a debit: the other way)
Cr bank_suspense:{bank account}   the same
```

So the ledger's `bank` follows the bank's own balance from the first
read, and `bank_suspense` holds whatever is not explained yet.

**The cursor is a position, never a transaction id** (decided
25-Nov-2026, found by the fuzzer): a bank that lists a transaction twice
makes "after this id" ambiguous, and the fake once skipped a credit that
came between the two listings. Business Account Insight pages by an
opaque `nextPageKey`, and so does the fake.

**Matching** (`bank_entry_match`, under the bank account's row lock, so
an entry and a payout arriving at once serialize), each run over every
unmatched credit and unmatched payout of the account:

- a credit and a payout match when the payout's id is a whole token of
  the credit's remittance text, the payout's destination is this account,
  and each is the other's only candidate. Two candidates on either side
  match nothing: a person picks. Never by amount alone (decided
  25-Nov-2026);
- the amount need not agree. What arrived is matched; a shortfall (a
  correspondent bank's charge) or an excess stays in
  `payout_in_transit`, shown on the payout, with no write-off (decided
  25-Nov-2026);
- the match, keyed `bank-match:{bank account}:{entry id}`, moves what
  arrived:

  ```
  Dr bank_suspense:{bank account}      the credit's amount
  Cr payout_in_transit:{psp account}   the same
  ```

- either side may come first: a credit read before the payout report
  waits in suspense, and the payout's fact runs the match again.
  `bank_match` is unique on the entry and on the payout, and append-only.

**A person links** a credit no rule matched to a payout of the same
account and currency on the staff page, booking the same entry with the
person as actor (decided 25-Nov-2026: the correction has one meaning, so
it books). Any other entry (a debit we did not make, a credit nobody
announced) gets a note, booking nothing; it stays in `bank_suspense`.

**The staff settlement page** shows each payout's bank state: not
arrived, or arrived on a date, with the amount when it differs. A payout
whose credit arrived and whose lines are still open, or whose credit
differs, is flagged, and takes a note as a line exception does. Beside
it: per bank account, its unmatched entries with their candidates, and
`bank` beside the bank's own booked balance.

**The fake bank** under `/_fake/bank/{account}`, in `fake_vendor`,
scripted and never timed, in Business Account Insight's shape:
`GET …/transactions?pageKey=` (oldest first, a page of a few, with
`nextPageKey` and `has_more`), each with `transactionId`, a signed decimal `amount` in
major units, `currency`, `descriptionLines`, `counterPartyName` and
`bookDate`. A cut of the fake PSP leaves a pending credit at its
destination, with the payout id in the text; `POST …/book` books what is
pending (the staff sandbox page, the seed and the fuzzer call it).
Faults, picked with the cut: no reference in the text, the reference
cut short, a credit short by a charge, a credit that never arrives, a
transaction listed twice, a credit nobody announced, a debit we did not
make, and the list answering 500 or timing out.

**Tables**: `bank_account`, `psp_payout_destination`, `bank_cursor
(bank_account_id, page_key)`, `bank_inbox`, `bank_balance_shown
(bank_account_id, inbox_id, balance_minor)` (the bank's balance after
the newest page read, which `bank` must equal), `bank_entry (id,
bank_account_id, vendor_entry_id, amount_minor, currency, remittance,
counterparty, booked_on, balance_after_minor, inbox_id, recorded_at)`
unique on
`(bank_account_id, vendor_entry_id)`, `bank_match (entry_id unique,
payout_id unique, actor, matched_at)`, `bank_entry_note (entry_id
unique, note, actor, noted_at)` and `settlement_payout_note (payout_id
unique, note, actor, noted_at)`, all append-only. Ledger kinds `bank`
and `bank_suspense`, scoped by bank account.

## 8. Testing and fuzzing

- Pure: the transition table is total; the capture entry balances and
  pays the merchant exactly; the invoice's lines sum to the option's
  total and its goods lines to the quote's "Items"; the line, payout and
  rounding entries balance and clear exactly what they should; a fee in
  sub-units rounds, and exact = rounded + remainder; provider amounts
  round-trip or report `not_representable`; decoders turn any bytes into
  a record or a typed error.
- Through the edges: pay with an expired quote shows the current price;
  a double pay makes one attempt; a cross-site POST is refused; each state
  renders; the return from the hosted page shows waiting; each wiring,
  lost answers on both guards, declines, pay another way (and a capture
  that beats the cancel); the order page, the invoice page and the PDF;
  each `after_payment` redirect; the KYB gate; the orders API. Settlement
  per mode: a cut settles every payment and the payout balances; each
  fault gives exactly its exception; a net that is off is booked as paid
  and shown open by its gap; a balance transfer leaves both batches
  balanced; a payout read before its lines are listable stays open, then
  balances; a payout and its pages decoded in any order end balanced;
  facts met again book nothing.
  The bank: each fault gives exactly its state (unmatched, short, in
  suspense); a credit before its payout report and after it both end
  matched; a transaction listed twice books once; a clean cut leaves
  `payout_in_transit` at zero and `bank` equal to the bank's balance; a
  person's link books once.
- Concurrency: six workers issue invoices for 24 orders at once, numbers
  still 1…n; a webhook applied while the capture call is out.
- Fuzzers (`plan-architecture.md` §10): the payment machine (up to the
  capture, every wiring drawn), the order machine (from approved payments
  to the invoice) and the settlement machine (a deep start that fetches a
  first cut and decodes its documents in a drawn order). After every
  action, invoice numbers per series run 1…n and the PSP made one payment
  per attempt; after draining, every captured attempt has one order, one
  capture entry and one invoice, no session is stuck in `paying`, every
  settled payment is booked once and every payout balanced unless its
  fault says otherwise. The settlement machine also books the fake
  bank's pending credits and reads the bank in the drawn order; after
  draining, every payout with a clean credit is matched, `bank` per
  account equals the fake bank's balance, and nothing is matched twice. With guard `none`, "waiting on a person" is
  allowed, never a double charge.
- Planted bugs each caught: a resend on a `none` guard; expiring a session
  while it is paid; the invoice counter read without `FOR UPDATE`; the
  number committed apart from the invoice; a tolerance in the net check;
  lines not triggering the reconciliation; waiting lines never booked; a
  payout booked twice; a match by amount alone; a payout matched to two
  credits; an entry booked twice.

## 9. Compliance touchpoints (named, not answered)

- PCI DSS: card data only ever enters the PSP's page or its fields, so we
  stay at the lightest self-assessment. Which questionnaire applies is
  for the PSP and an assessor, not assumed. With card fields the PSP's
  script runs on our page: PCI DSS 4.0's script inventory and change
  detection (6.4.3, 11.6.1) apply as the PSP's terms will say.
- Payment licences (MAS PSA and others) for collecting on a merchant's
  behalf, and which entity holds the PSP contract; safeguarding rules
  for funds the PSP holds between capture and payout, and for funds in
  our bank before the merchant's payout.
- Which bank, in which country, holds merchants' money between the
  PSP's payout and ours, and whether that account must be a segregated
  or safeguarding account; whether the bank API's data (counterparty
  names) is personal data we must treat as such.
- KYB before a live payment, beyond the minimal state of §6.
- The statement descriptor `OURS*MERCHANT` (`plan-merchant-surfaces.md`
  §5) is a PSP selection criterion; the fake takes one per payment.
- Invoice wording, tax registration numbers, a per-country series or a
  local-date tax point; retention of the order, invoice and address.
- Fees with the PSP's VAT or GST on them: whether we can reclaim it as
  input tax, per country. The fee is booked as the PSP charged it, tax
  included.

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

## 11. Open

- **Buyer emails and merchant webhooks** come together as one `notify`
  slice, with a fake mailbox under `/_fake/` (decided 19-Nov-2026). Until
  then the order link is shown only after paying, and `order_url` in the
  API.
- The deploy script must install Pango for WeasyPrint on the server.
- The sandbox market has no tax for the lane tried, so the invoice's tax
  sections were seen only in tests; the fake never cuts IDR, so sub-unit
  fees and the payout's rounding are checked by properties only.
- Two workers applying captures of two attempts of one session at the
  same moment: one hits the one-capture index, raises, and its retry hands
  the second to a person. Correct, but noisy; seen by reasoning, not by a
  test.

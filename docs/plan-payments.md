# Slice 5: payments, the order and the invoice

Status: 5a built (19-Nov-2026), 5b built (20-Nov-2026); 5c split in
three (§7.1): 5c-1 (settlement) built (20-Nov-2026, §7.2); 5c-2 (the bank
statement) is next, to be designed.

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

### 6.1 5b in detail (agreed 20-Nov-2026)

**The merchant** (migrations: new enum values such as `invoice_issued`
in `0018`, as 5a did; types and tables in `0019`):

- `merchant_kyb_state` enum `pending`, `approved`, `suspended` (question
  5); `merchant.kyb_state` defaults to `pending`. Every change is also a
  row in `merchant_kyb_change (merchant_id, state, changed_at, actor)`,
  append-only, written in the same transaction: the column is what the
  pay step reads, the table is who changed it and when.
- KYB is ours, never the merchant's: set by the CLI
  (`uv run python -m mor.merchants kyb mer_… approved`) until staff
  sign-in exists, then by a staff page. The seed approves its merchants
  at once.
- The gate applies in every environment, so the code that guards prod is
  the code under test (decided 20-Nov-2026). The pay step reads
  `kyb_state` with `FOR SHARE` under the session lock and refuses a new
  attempt unless `approved`; the CLI's change takes the row `FOR UPDATE`,
  so a suspension and a pay cannot cross. The buyer sees "This shop
  can't take payments right now", and no PSP call is made. An attempt
  already open runs to its end.
- The merchant's own settings go through the API with its key, not the
  CLI (decided 20-Nov-2026): `POST /v1/merchant/settings` sets
  `support_email` (or null), with `settings_changed_at` and its actor on
  `merchant`. Plain settings, not financial state, so no history table.
  The dashboard (merchant sign-in, plan-merchant-surfaces) later calls the
  same code. Settings that hold for every checkout (support email, later
  branding) live on the merchant; what can differ per checkout lives on
  the session.
- **`after_payment` is per session** (decided 20-Nov-2026, amends the
  answer to question 2), as Stripe sets its success URL per session: a
  field of the create-session body next to `return_url`, `our_page`
  (default) or `merchant_page`, stored on `checkout_session`. Never a
  query parameter on the buyer's link, which the buyer can edit.

**The selling entity.** One place, `src/mor/entity.py`: our legal name,
address and tax registration numbers per country, as plain data, with
placeholders until the entity is decided (plan-psp-integration §11). The
buyer page's `OUR_NAME` moves there. The invoice copies what it uses.

**The invoice** (`src/mor/invoices/`):

- Numbering is data, so it can grow without touching issued invoices
  (asked 20-Nov-2026). A series (code, entity, prefix, with or without
  the year, width) is plain data in `src/mor/invoices/numbering.py`;
  `invoice_series (code, next_number)` holds only its counter, a row made
  on the series' first use (insert, a conflict means it exists). Today one
  series, `INV`, for the placeholder entity (question 4). One function,
  `series_for(entity, buyer_country)`, picks it; a per-country series an
  adviser asks for is an entry in `SERIES` and in `SERIES_BY_COUNTRY`,
  and credit notes (slice 8) are a series of their own. A code is never
  reused. Each invoice stores its series code, its number and the
  finished `number_text`, so a later format change never rewrites an
  issued number. The uuid stays the invoice's id; the number exists
  because tax law asks for it (IRAS: an identifying number; TH Revenue
  Code §86/4: a serial number) and a gap shows a missing invoice, which a
  uuid cannot.
- `invoice (id, order_id UNIQUE, series, number, number_text, issued_at,
  paid_at, currency, total_minor, seller_name, seller_address,
  seller_tax_label, seller_tax_id, merchant_legal_name, buyer_name,
  buyer address fields)`, `UNIQUE (series, number)`. `number_text` is
  `INV-2026-000001`: prefix, the issue year, then the series' running
  counter, which does not restart each year.
  Name and address are copied, not referenced, because an invoice is kept
  for the legal retention period and the session's address may not be
  (§9). Email and phone are not copied.
- `invoice_line (invoice_id, line_no, section, description, quantity,
  amount_minor, rule_text, source)`. `section` is an enum: `goods` (one
  line per session line, the merchant's price plus its share of our fees
  in whole minor units by `money.rounding.allocate`, weighted by the
  rounded goods parts, so the lines sum to the buyer's "Items"),
  `shipping` (one line: the option's service name, the shipping group),
  `tax` (each seller-collected tax part: its label as `rule_text`, its
  source) and `border` (each border duty and tax, "paid on your behalf").
  The rate stays inside the label text: parts store how they were reached,
  not a rate field, and re-parsing labels would be clever.
- Both tables append-only by trigger (`refuse_change`).
- **Issued by a local consumer** `invoice_issue` on `order_paid`, as
  planned: lock the order, return `Stale` if its invoice exists, lock
  the series row, take the number, insert invoice and lines, emit
  `invoice_issued` v1 (`invoice_id`, `order_id`, `merchant_id`; a record
  for now, webhooks later). One transaction, so a rollback gives the
  number back. Lock order always order, then series. Kept apart from
  `order_create` on purpose: a bug in the invoice code must not hold back
  the order and the capture's ledger entry. No ledger entry: the capture
  already booked the tax as owed.
- The printed dates are the UTC date (`20-Nov-2026`) of `issued_at` and
  of `paid_at`. Whether a country's tax point must be its local date is
  for the adviser (§9).

**Rendering.** One htpy function draws the invoice body from the rows;
the page wraps it in the site layout, the PDF wraps it in a print
stylesheet (`static/css/invoice.css`, read at render) and goes through
WeasyPrint in `asyncio.to_thread` (rendering blocks). WeasyPrint gets a
URL fetcher that refuses everything, so nothing in an invoice can make
the server fetch a URL. WeasyPrint 70.0 (08-Sep-2026, BSD-3-Clause,
Python 3.14 listed on PyPI, checked 20-Nov-2026) needs Pango from the
system: installed on this laptop, and a line in the deploy script for the
server.

**The order page** `/o/{buyer_token}`:

- What was bought (the invoice's goods lines), shipping by which service
  and transit days, the total, the delivery address, the invoice as a
  page (`/o/{token}/invoice`) and a PDF (`/o/{token}/invoice.pdf`), "what
  happens next" (the merchant ships it; nothing more is due on
  delivery), the merchant's support email if it has one, our support
  (placeholder from `entity.py`), and "Back to <merchant>".
- Until the invoice exists the page says "Your invoice is being
  prepared" and waits like the other waiting pages (`ts/waiting.ts`,
  `/o/{token}/status`, meta refresh without JavaScript).
- The token is in the URL, so the page sends `Referrer-Policy:
  no-referrer` (the back link must not hand the token to the merchant's
  site) and `X-Robots-Tag: noindex`. An unknown token is a plain 404.

**Where the buyer lands.** `/c/{session}` in state `completed`, by the
session's `after_payment`:

- `our_page`: a 303 to `/o/{token}`, until the session's own
  `expires_at`; after that `/c/{session}` shows "This order is paid" and
  no link, so a forwarded checkout link does not show the invoice and
  address for ever (decided 20-Nov-2026; no new time limit).
- `merchant_page`: a 303 to `return_url` with `session_id=cs_…` added to
  its query (kept if the URL already has one).

Both happen only in `completed`, which only `order_create` sets after the
PSP's capture: the browser's return never decides it. The waiting
script's reload follows the redirect.

**How long the order link lives.** `/o/{token}` does not expire for now:
it is the buyer's receipt, needed for returns and warranty, and its
token is 32 random bytes. Revisited with the notify slice, which can
offer "send me my link again" (Stripe's hosted invoice links expire after
30 to 120 days and the API hands out a fresh one).

**API** (v1-additive):

- `GET /v1/checkout_sessions/{id}` gains `order_id` (`ord_…`, null until
  paid).
- `GET /v1/orders/{id}`, for the merchant that owns it (another's is a
  404): state, `paid_at`, currency and total, the lines as the merchant
  sent them, the option (service, name, transit), the full address with
  email and phone (for fulfilment, as 4d-2 promised), `invoice_number`
  (null until issued), and `order_url`: the order page's address, so a
  merchant on `merchant_page` can show the buyer "View your invoice"
  before emails exist (decided 20-Nov-2026).

**Staff.** The session page shows the order and its invoice number.

**Tests and fuzzing:**

- Through the edges: pay, drain, then the order page, the invoice page
  and the PDF (`%PDF-` and `application/pdf`); each `after_payment`
  redirect; the KYB gate (`pending` and `suspended` refused, no attempt,
  no PSP call); the orders API (own order, another merchant's is 404,
  `invoice_number` null then set); the session API's `order_id`. The
  new routes are all GET, so there is no new POST to guard.
- Properties: the invoice's lines sum to the option's total, and its goods
  lines to the quote's "Items", for any stored quote option.
- The payment fuzzer drains `invoice_issue` too. After every action, the
  numbers per series run 1…n with no gap and the counter is one past the
  last, and each invoice is for what its payment captured; after
  draining, every order has its invoice.
- A crash inside `invoice_issue` before its commit gives the number back
  (a test of its own), and the concurrency suite has six workers issue
  invoices for 24 orders at once: numbers still 1…n.

### 6.2 5b built (20-Nov-2026)

What was built:
- Migrations `0018_invoice_values.sql` (enum values) and
  `0019_invoices.sql` (KYB state and its history, the merchant's
  settings, `after_payment` on the session, the series counter, the
  invoice and its lines, append-only).
- `src/mor/entity.py` (the selling entity, placeholders);
  `src/mor/invoices/` (records, `numbering`, `lines`, `store` with the
  `invoice_issue` step); `src/mor/orders/store.py`; `src/mor/quote/groups.py`
  (`group_of`, moved out of the web layer so the invoice can use it).
- The order page and invoice at `/o/{token}` (`web/order_pages.py`,
  `order_html.py`, `invoice_html.py`, `invoice_pdf.py`,
  `static/css/invoice.css`); the redirect of a paid `/c/{session}`; the
  KYB notice and gate; `GET /v1/orders/{id}`, `GET /v1/merchant`,
  `POST /v1/merchant/settings`, `order_id` and `after_payment` on the
  session; the order and invoice on the staff session page; `kyb` in the
  merchants CLI; the seed approves the Sandbox Shop on every run.
- WeasyPrint 70.0, with a stub of the part we call in `typings/weasyprint/`
  (it ships no types and pyright is strict).

Changed while building:
- **The series is data in code, the table only a counter.** The first
  version kept the format in a row the migration inserted; the test setup
  empties every table, and a new series would have needed a migration.
- **The fuzzer reached a paid order in 9 of 354 runs.** It now has a "pays
  and approves" action (pay, the worker makes the payment, the buyer
  approves; the rest is left to the other actions), and a crash inside a
  local step before its commit. Paid orders now come in about a quarter
  of the runs.

How it was checked:
- Tests through the pages and the API (above); the invoice-lines
  property; the fuzzer at 300 and 1,000 examples.
- Planted bugs: the counter read without `FOR UPDATE` is caught by the
  concurrency test (3 of 3 runs, a duplicate number refused by the unique
  key). The number committed apart from the invoice is caught by the
  crash test, **not by the fuzzer** at 1,000 examples: a crash rarely
  lands in the moment `invoice_issue` waits. The existence check before
  issuing is backed by `UNIQUE (order_id)` and tested by a redelivery.
- On the sandbox in a headless browser at phone width: a card payment
  waited, reloaded and landed on the order page; the invoice as a page
  and as a PDF (rendered and looked at); the orders and settings API; a
  `merchant_page` session paid by QR went to its `return_url` with its
  own query kept and `session_id` added. The only console error was a 400 from
  an address form filled in wrong while testing.

Open:
- The deploy script must install Pango for WeasyPrint on the server
  (`deploy/deploy.sh` does not exist yet: the deploy slice).
- Invoice wording, the tax registration numbers and whether a country
  needs its own series or a local-date tax point: for the adviser (§9).
- The sandbox market has no tax for the lane tried, so the invoice's tax
  sections were seen only in tests.

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

### 7.1 5c in detail (agreed 20-Nov-2026; wins over the sketch above)

**Split.** §1 put four things in 5c. Settlement is the slice's money
work; the other three are not about settlement. Proposed (question 8):

- **5c-1: settlement.** Below.
- **5c-2: the bank statement** (added 20-Nov-2026 with question 9): a
  fake bank and the import of its statements, matching each PSP payout
  to the credit that shows it arrived. Designed after 5c-1 is built.
- **5c-3: the browser side.** The fake's card-fields component (the first
  PSP JavaScript, under the prod CSP) and the Playwright smoke suite.
- **Echo retirement waits** for the shipment slice. The PSP covers push,
  thin push and poll, and 5c-1 covers file; but the echo is still the
  only flow that exercises the `compensable` guard (money is never
  `compensable`) and a status derived from a set of events, both of
  which come back with courier booking and tracking (plan-architecture
  §9). Retiring it now leaves both untested until then.

**What real PSPs send** (plan-psp-integration §2): a report per payout
batch, as a file (Adyen's settlement detail report, 2C2P's SFTP CSV) or
through a report API (Xendit, Checkout.com). One batch is one bank
transfer in one currency; its lines are per payment (gross, fee, net),
plus refunds, chargebacks and the PSP's own charges. Fees are known only
here. Fees may carry the PSP's VAT or GST (§9).

**Capability**, a new column on `psp_account`, so the fuzzer and the
seed draw it:

| Capability | Values |
|---|---|
| settlement reaches us by | `file` (a list of report names after a cursor, then one CSV per batch), `api` (a list of batch ids after a cursor, then pages of one batch) |

Both read only, so neither needs a guard: a repeat after a crash is
absorbed by the inbox and the vendor's ids.

**The fake PSP** (`/_fake/psp/{account}`):

```
POST A/settlements              {"faults": [...]}  cut a batch now, per
                                currency, of every captured payment not
                                settled yet; the staff page, the seed and
                                the fuzzer call it ("cut" in the sandbox)
GET  A/settlement-files?after=  report names, oldest first     (file)
GET  A/settlement-files/{name}  CSV: a header row for the batch (id,
                                currency, net, paid-out date), one row
                                per line (line id, type, payment id,
                                our reference, gross, fee, net)
GET  A/settlements?after=       batch ids, oldest first        (api)
GET  A/settlements/{id}?page=n  JSON: the batch header, a page of lines,
                                `next_page` or null
```

- Fees are the fake's own made-up rule per method (card a percentage
  plus a fixed part, QR a percentage), in its own minor units: IDR has
  2, so an IDR fee has sub-rupiah parts, as Stripe's would.
- Faults per cut: a line for a reference nobody made, a gross that
  differs from the capture, a payment settled again in a later batch
  under a new line id, a line of a type we do not know (`adjustment`), a
  net that is not what the lines add up to, and the list or fetch
  answering 500 or timing out. The fake records what it cut; the fuzzer
  compares.

**Our side.** The same one inbound path as webhooks and polls
(plan-messaging §12): raw bytes into `psp_inbox` first, decoding after.

```
psp_settlement_due (scheduled, per account)
  └▶ psp_settlement_fetch (effect, reads only): one look, at most one
     report or page, as echo_report_fetch does (a backlog reads as looks
     that follow each other at once)
       └▶ store_inbox (mode settlement_file | settlement_page)
            └▶ psp_inbox_decode (per account): inserts the batch and its
               lines, each once by the vendor's id; one
               settlement_line_received per new line; on the batch's last
               page, settlement_batch_received
                 ├▶ settlement_line_book (per attempt, local)
                 └▶ settlement_payout_book (per batch, local)
```

The cursor is `psp_settlement_cursor (account_id, last, pending)`, as
the echo's report cursor: `last` is a report name or a batch id and page.

**Booking a line** (`settlement_line_book`, under the attempt's lock):

- The line names our reference (the attempt id) and the PSP's payment id;
  both must agree with one attempt of this account. The attempt must be
  `captured`, its gross and currency equal to what was captured, and no
  other line booked for it (a partial unique index on
  `settlement_line (attempt_id) WHERE type = 'capture' AND state =
  'booked'`).
- The entry, keyed `settlement:{account}:{vendor line id}`, in the
  line's currency:

  ```
  Dr psp_fee_collected               the estimate, read from the capture
                                     entry's own line (what was booked,
                                     not recomputed from the quote)
  Cr psp_clearing:{account}          the fee, rounded to our exponent
  Dr/Cr psp_fee_variance:{account}   the gap, ours (skipped when zero)
  ```

- The net stays in `psp_clearing` until the payout. A fee in sub-units
  of our exponent (IDR 1,234.56) is rounded half-even at import, the one
  place that rounds it (plan-psp-integration §4); the exact value and the
  mode are stored on the line.
- Anything that does not fit is a `settlement_exception` and books
  nothing: `unknown_reference`, `not_captured` (the attempt ended
  otherwise, or is a second capture already with a person),
  `amount_differs`, `currency_differs`, `settled_twice`, `unknown_type`.
- A line for an attempt still open (settlement got ahead of our webhook)
  asks for the payment's state (`payment_events_wanted`, as a thin
  webhook does) and returns `RetryLater`. Time only triggers the retry;
  the attempt's state under the lock decides. The PSP's settling it
  is not taken as the capture itself.

**Booking the payout** (`settlement_payout_book`, under the batch's
lock), keyed `payout:{account}:{vendor batch id}`:

```
Dr payout_in_transit:{account}   the net the PSP says it paid   (new kind)
Cr psp_clearing:{account}        the same
```

- Booked as reported, whatever the lines say: the PSP says the money
  left it. It is not in our bank until the bank's statement shows it
  (question 9): the statement import (5c-2) moves it from
  `payout_in_transit` to `bank:{entity}`. Only `bank` is money we can
  pay merchants from.
- The check is exact, never a tolerance: from the lines' gross and
  their exact fees, the exact net is known; the reported net must be
  that value rounded either way to our exponent. Then the difference
  between the reported net and (gross − booked fees) is a `rounding`
  line in the same entry. Otherwise a `settlement_exception`
  (`net_differs`) on the batch, and the gap stays in `psp_clearing`,
  where the health figure shows it.
- Lines and the payout can be booked in any order; only the end balance
  is checked. A batch is in one currency; a header with a currency the
  account cannot take is an exception.

**The health figure.** `psp_clearing` per account and currency, beside
the captures the PSP has not settled yet: zero once everything captured
is settled and paid out. Beside it, `payout_in_transit` per account: what
the PSP says it sent and the bank has not shown yet. Shown on a staff page
with the open exceptions.

**Tables** (`0020_settlement_values.sql` for enum values, then
`0021_settlement.sql`):

- `psp_settlement_mode` enum and `psp_account.settlement_mode`,
  `settlement_interval_s`.
- `settlement_batch (id, account_id, vendor_batch_id, currency,
  net_minor, net_exact text, paid_out_on date, inbox_id, recorded_at,
  state)` with `UNIQUE (account_id, vendor_batch_id)`; state `received`,
  `booked`, `exception`.
- `settlement_line (id, batch_id, account_id, vendor_line_id, type,
  provider_payment_id, our_reference, gross_minor, fee_exact text,
  fee_minor, fee_rounding, currency, attempt_id, state)` with `UNIQUE
  (account_id, vendor_line_id)`; type `capture` (refunds and chargebacks
  join in slice 8) or `unknown` with the raw type kept; state
  `received`, `booked`, `exception`.
- `settlement_exception (id, line_id?, batch_id?, kind, detail,
  created_at)` and `settlement_exception_resolved (exception_id unique,
  note, actor, resolved_at)`: append-only. Resolving books nothing in
  5c; it records that a person looked (question 10).
- `AccountKind.PAYOUT_IN_TRANSIT`, scoped by the PSP account; `BANK`,
  scoped by the entity, comes with 5c-2.

**Facts**: `settlement_line_received`, `settlement_batch_received`,
`psp_settlement_due`, each `V1`. No change to an existing fact's shape.

**Staff and sandbox.** A staff page per PSP account: the health figure,
the batches, the open exceptions, and a form per exception to resolve it
with a note (question 10). Like the other staff POSTs it is mounted
outside prod only until staff sign-in exists, then behind it; it is
guarded against cross-site posts as every form is. Until sign-in the
actor is a placeholder staff actor. The sandbox page gets "cut a
settlement" per account, with a fault to pick.

**Testing.**

- Pure: the line entry and the payout entry balance for any estimate,
  fee and net; a fee in sub-units rounds, and exact = rounded +
  remainder; the decoders turn any bytes into a batch, its lines or a
  typed error.
- Through the edge: per mode, a cut settles every captured payment once
  and `psp_clearing` reaches zero; each fault gives exactly its
  exception and books nothing wrong; a report stored twice books once;
  settlement ahead of the webhook waits and then books.
- The post-capture fuzzer (`test_order_flow_fuzz.py`, plan-fuzz-reach)
  gains: cut a settlement (faults drawn), the fetch's faults, a crash at
  each new commit, the settlement mode drawn per run. After draining with
  faults off: every payment the fake settled is booked once; each
  account's `psp_clearing` equals the model's captured-but-unpaid sum
  plus the gaps the exceptions name; one exception per fault cut. New
  milestones: a line booked, a payout booked, each exception kind.
- Planted bugs: no unique line id (booked twice), matching by amount
  instead of reference, the variance line dropped, the payout's rounding
  line taken as a tolerance.
- Soak targets and budgets updated with timed runs.

**Compliance touchpoints** (named, not answered):

- Fees with the PSP's VAT or GST on them: whether we can reclaim it as
  input tax, per country. 5c books the fee as the PSP charged it, tax
  included, and stores the tax part if the report shows one.
- Funds the PSP holds for us between capture and payout, and in our bank
  before the merchant's payout: safeguarding rules under payment
  licences (MAS PSA and others), with §9's licensing question.

**Questions for 5c** (my recommendation first; numbered after §11's):

8. **Split 5c.** Recommended: 5c-1 settlement, 5c-2 card fields and the
   Playwright suite (now 5c-3, after question 9), and the echo retired only with the shipment slice,
   which takes over `compensable` and the set-derived status. The other
   choice retires it now and loses that coverage for a slice or two.
   **Answered 20-Nov-2026: as recommended.**
9. **The payout's debit.** Recommended: `bank:{entity}` on the PSP's
   word; a bank statement import, when it comes with payouts (slice 9),
   matches those entries rather than booking them again. The other
   choice books `payout_in_transit:{account}` now and moves it to
   `bank` on the bank's word, which is more exact but leaves a balance
   growing until bank import exists. **Answered 20-Nov-2026: the other
   choice, "on its way"**, because a payout to a merchant must never
   spend money that is not in our bank yet. The statement import is
   brought forward from the payout slice to 5c-2, so the in-transit
   balance does not grow unchecked; the payout run (slice 9) pays only
   from `bank`.
10. **Resolving an exception.** Recommended: a note only (who, when,
    what was found), by CLI; any correcting entry is made by hand
    through a later staff tool. Writing typed corrections now (write
    off, attribute to the merchant) guesses at cases we have not seen
    from a real PSP. **Answered 20-Nov-2026: a note only, but on a staff
    web page, not the CLI**: staff work happens in the browser, and the
    dashboard will be web too.
11. **Settlement currency.** Recommended: a batch settles in the
    captured currency only (most SEA acquirers pay local currency into
    a local account). A PSP that converts at settlement is a new
    capability value, with the PSP's rate as an FX ledger event, added
    with the first real PSP that needs it. The other choice builds the
    converting mode into the fake now. **Answered 20-Nov-2026: as
    recommended.** If a PSP's conversion is ever used, it is built as a
    separate step after the line is booked, not mixed into the line's
    entry. The conversion into the merchant's currency (USD, VND, EUR)
    is ours: the capture already owes the merchant in its currency
    through `fx_position`, and the real trade, at the rate we get,
    clears it between settlement and the merchant's payout (slice 9).

### 7.2 5c-1 built (20-Nov-2026)

What was built:
- Migrations `0020_settlement_values.sql` (enum values) and
  `0021_settlement.sql` (the account's settlement mode and interval, the
  reading cursor, batches, lines, exceptions and their resolutions); the
  fake's `migrations_fake/0008_psp_settlement.sql`.
- `src/mor/settlement/`: `entries.py` (the line and payout entries and the
  exact net check, pure), `fetch.py` (`psp_settlement_fetch`, one report
  or page per look, and `start_settlement_reading`), `store.py` (decoding
  into rows, `settlement_line_book`, `settlement_payout_book`),
  `review.py` (what the staff page reads, and resolving).
- The port's settlement records and both modes in the fake client and the
  registry; the fake PSP's cut (with faults), report files and batch pages
  (`src/mor/fakes/psp.py`).
- `AccountKind.PAYOUT_IN_TRANSIT`; `psp_inbox` takes settlement bytes on
  the one inbound path.
- The staff pages at `/staff/settlement` (balances, batches, exceptions,
  the resolve form); "cut a settlement" on the sandbox page; the seed
  starts every account's reading (keyed accounts by file, the others by
  API, one-minute interval on the test setup).

Changed while building:
- **An attempt's lines are settled together, earliest first.** The first
  version booked each line on its own delivery; when a PSP settled a paid
  payment twice, whichever delivery the worker ran first was booked, so the
  original line could become the `settled_twice` exception. Found by the
  fuzzer, pinned by
  `test_a_payment_settled_twice_keeps_its_first_line_whichever_runs_first`.
  The line's attempt is now found when the line is stored, and every
  booking step locks the attempt and settles all its pending lines in the
  order the PSP sent them.
- **Settlement has a fuzzer of its own** (`tests/flow/test_settlement_flow_fuzz.py`),
  not actions added to the order fuzzer: added there, they diluted it
  ("crash in order_create" fell to 0.5% of runs) and reached a waiting
  line in 0.2%. Its deep start draws which payments our books have caught
  up with, cuts and reads a first batch, and books it or not as drawn; two
  preconditioned rules aim crashes and redeliveries at the booking steps
  (`docs/plan-fuzz-reach.md` §3). The order fuzzer is unchanged but for
  sharing `meet_again` from `payment_checks.py`.
- A line in another currency than its batch is left out of the batch's
  net check (it is `currency_differs` already).

How it was checked:
- Tests through the edge in both modes (`tests/settlement/`): a cut with
  three payments (two API pages) clears `psp_clearing` to zero, each fault
  gives exactly its exception, a line waits for its order and is then
  booked, a declined payment's line and a wrong-currency line book nothing,
  redelivered facts book nothing more, failing reads are retried, the
  pages and the resolve form (a cross-site POST refused, one note only).
- Properties: the entries balance and clear what they should, the net
  check is exact, the fake's IDR payout rounding, the decoders on any
  bytes.
- The settlement fuzzer at 600 examples: every milestone reached by at
  least 5.9% of runs. Planted bugs caught: a tolerance in the net check,
  waiting lines never booked, a payout booked twice.
- On the sandbox in a headless browser, desktop and phone width: a cut
  with an `unknown_type` fault booked four lines and the payout, kept the
  5.00 SGD adjustment in `psp_clearing` under its exception, and the
  exception was resolved with a note. No console errors but the sandbox
  page's missing favicon.

Open:
- Accounts made before `0021` got the column defaults (file, one hour).
  On `mor_sandbox` the twelve seeded accounts were brought in line by
  hand (mode by guard, one minute); a fresh test setup gets them from the
  seed.
- The fake never cuts IDR (the sandbox market and the fuzzers are SGD):
  sub-unit fees and the payout's rounding line are checked by properties
  only.
- The payment fuzzer failed once in a full `pytest` run (20-Nov-2026) and
  not again in 16 runs since, nor at 1,500 examples; its output was not
  kept. 5c-1 gives it new deliveries (`settlement_line_book` on a
  payment's end, which finds no lines there) and a settlement check that
  holds trivially. Keep the log if it fails again.
- 5c-2: the bank statement import that moves `payout_in_transit` to
  `bank`, with a fake bank; 5c-3: card fields and the Playwright suite.

## 8. Testing and fuzzing

- Pure: the transition table is total; the capture entry balances and
  pays the merchant exactly; provider amounts round-trip or report
  `not_representable`; decoders turn any bytes into a record or a typed
  error.
- Pages at their edge: pay with an expired quote shows the current
  price; a double pay makes one attempt; a cross-site POST is refused;
  each state renders; the return from the hosted page shows waiting.
- (Since 20-Nov-2026 two machines, split at the capture, with their reach
  measured: `docs/plan-fuzz-reach.md`.) A Hypothesis state machine for the
  payment flow against the test
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

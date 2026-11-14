# Plan: payment provider integration, merchant API and Shopify

Status: design proposal (04-Nov-2026), awaiting confirmation. No code yet.
The language is still undecided, so the types below are written in neutral
pseudo-notation (records, closed unions, functions), not in any language.

## 0. Decisions and context so far

- (04-Nov-2026) Legal entity: Singapore entity or Dutch BV, not decided yet.
  This matters a lot: local SEA methods and the SEA-native PSPs fit an SG
  entity much better, and licensing differs (§8).
- (04-Nov-2026) Goods: both physical and digital. Both tax regimes are in
  scope (digital-services rules for foreign sellers, plus import and goods
  rules).
- (04-Nov-2026) Launch: merchants in Vietnam first, selling to buyers in
  other SEA countries. Consequences:
  - **Buyer side:** the buyers' local methods come first (PayNow, PromptPay,
    DuitNow/FPX, QRIS/e-wallets, GCash/Maya) plus cards. VN checkout methods
    (MoMo, VNPay, ZaloPay) are not first. Xendit and 2C2P cover the buyer
    side best (§2).
  - **Merchant side:** KYB of Vietnamese companies, a contract per merchant,
    and payouts into Vietnam. VND is a restricted currency, so the payout is
    probably foreign currency (USD) into the merchant's VN bank as export
    proceeds, converted by that bank. This must be verified (§6.4, §8).
  - **Legal chain:** under MoR the merchant sells to us, and we resell to the
    buyer. The Vietnamese merchant therefore exports goods or services to
    our entity (SG or BV) and needs the matching documents from us (a
    contract, a self-billed invoice or statement per payout) for its own VN
    tax treatment. That is VN export VAT and corporate income tax, and it
    needs advice.
  - **Physical goods are in scope from launch** (decided 04-Nov-2026: VN and
    SEA merchants mostly sell physical goods). They ship from VN to the
    buyer's country. As seller we
    likely also own import VAT and duties there (who is importer of record,
    whether we sell DDP or DAP, and the low-value import thresholds per
    country). This is the largest tax and ops question for physical goods.
    Digital goods avoid it and are simpler to launch with.
  - **Tax modules needed first:** the buyer countries (SG GST, MY SST/DST,
    TH VAT, ID VAT, PH VAT). VN VAT matters only on the merchant-to-us leg.
- (04-Nov-2026) No orchestration vendor or switching layer for now. We only
  keep the ability to add or swap a PSP when needed: one module per PSP
  behind one interface, plus a small routing config (§5.2).
- (04-Nov-2026) JavaScript: PSP components are allowed in checkout. Our own
  pages avoid JS where possible (AGENTS.md updated).
- (04-Nov-2026) IDR uses exponent 0, departing from ISO 4217 on purpose
  (AGENTS.md updated). Provider modules convert (§4).
- (04-Nov-2026) Saved cards and subscriptions: probably in scope. Accept that
  tokens are bound to the PSP that issued them, and plan to export them
  later if we switch (§5.6).
- (04-Nov-2026) Merchant payouts in v1: our own bank, weekly batched USD
  wires generated from the ledger (§6.4).

## 1. Framing: these providers are acquirers, not merchants of record

Adyen, Mollie, PPRO, Worldline, Worldpay, Checkout.com, Nuvei, Stripe, Xendit
and 2C2P do not act as merchant of record for us. They are PSPs and acquirers
(PPRO is a local-payment-method aggregator). **We** are the merchant of record.
Each provider takes the buyer's money into an account that belongs to one of
our legal entities. The tax, the invoice, refunds, chargebacks and merchant
payouts stay with us. The companies that do offer MoR as a product (Paddle,
Lemon Squeezy, FastSpring, Global-e, Stripe's "Managed Payments") compete
with us. They are not suppliers.

Who we compete with for Vietnamese merchants (noted 10-Nov-2026, from a
review of the "MoR / landed-cost / DDP" vendor categories): the
physical-goods MoRs (Global-e, Reach, Digital River, ESW, Passport's Seller
of Record, OpenBorder) and DDP networks that pay the border tax for the merchant
(FlavorCloud, Zonos's guarantee mode) are competitors, not suppliers. None
of them shows support for a Vietnamese seller entity or for goods shipped
from Vietnam (`docs/research/logistics/04-landed-cost-returns.md`). A
Vietnamese merchant's way in is a Singapore or US holding company plus
enterprise volume; Global-e is said to want about US$5M+ a year. Both the
entity limit and the volume figure come from a third-party summary and are
UNVERIFIED. If they hold, small and mid-size Vietnamese sellers cannot take
that route, and they are our market. What we do buy from this space is only
calculation (a duty source in estimate mode) and carrier DDP with duties
billed to us (`docs/plan-logistics.md` §2).

Consequences:

- We use each provider's **plain merchant/checkout API** and do not use its
  marketplace product. Adyen's docs say this directly: Adyen for Platforms is
  for onboarding third-party sellers, and a merchant of record uses standard
  Checkout. The same holds for Stripe Connect: without `on_behalf_of`, the
  platform is the business of record and carries refunds, chargebacks and
  fees. Their platform/split products only become relevant if we later let
  merchants be the seller of record.
- Merchant payouts are **our** ledger plus a payout rail. They are not a
  provider "split".
- Each provider contract is held by one of our entities, and local methods
  often need a local entity. Stripe PayNow, for example, needs a Stripe SG
  account and PromptPay needs a Stripe TH account. Licensing and entity
  structure therefore decide which providers we can use (see §9).

## 2. What the provider docs say (read 04-Nov-2026)

Sources were each provider's public developer docs. "?" means the docs did
not say, or I could not read them. Worldpay's docs render client-side and
could not be read, so Worldpay is still unknown.

| | SEA fit (documented) | Amount format | IDR exponent | Idempotency | Webhook auth | Webhook body |
|---|---|---|---|---|---|---|
| **Adyen** | ? (payment-method pages not read) | minor units | **0** (Adyen table, "differs from standard") | `idempotency-key`, ≤64 chars, kept **7–14 days**; 422/409 while in flight | HMAC-SHA256 over `:`-joined fields, base64 | fat, one item per POST |
| **Checkout.com** | PayNow; Dana and GCash (SG merchant region **beta**); Touch 'n Go and TrueMoney listed | minor units, ≤9 digits | **2** by rule (IDR not named) | `Cko-Idempotency-Key`, **72 h**, 2xx only; 409 concurrent | HMAC-SHA256 of raw body, hex, `Cko-Signature` | fat; at-least-once, unordered; 8 retries over ~42 h |
| **Stripe** | PayNow (SG account), FPX (MY), GrabPay (MY, SG), PromptPay (TH account); QRIS, GCash, MoMo and others as enums | minor units | 2 (inferred from limits) | `Idempotency-Key`, ≤255 chars, ≥24 h; errors on different params | `Stripe-Signature` t=…,v1=HMAC(t.body) | thin (recommended) or snapshot; unordered; 3 days of retries |
| **Xendit** | **ID, PH, TH, MY, VN, SG**: QRIS, OVO, DANA, GoPay, ShopeePay, GCash, Maya, QRPh, PromptPay, TrueMoney, DuitNow, FPX, TnG, ZaloPay, VAs, OTC | integer in examples | 0 in examples (IDR 25000) | **not found** | static `x-callback-token` | fat; unordered, duplicates; 6 retries |
| **2C2P** | **SG, MY, TH, ID, PH, VN**: PayNow, DuitNow, PromptPay, QRIS, QR Ph, GCash, VNPAY, MoMo, ZaloPay, ShopeePay, GrabPay, OVO, DANA, bank redirects, OTC | **decimal**, D(12,5) | ? | `idempotencyID` field, semantics ? | JWT HS256 (PGW); JWE+JWS (PaymentAction) | ?; reconciliation via SFTP CSV |
| **Nuvei** | SEA currencies listed, no SEA methods found | decimal string | decimals (IDR not marked) | `clientRequestId` **rejects** duplicates, no replay | SHA-256 checksum over fields + secret | URL-encoded; IP allowlist |
| **Worldline** | none documented (EU, plus Alipay+/WeChat/UnionPay) | integer minor | ? | `X-GCS-Idempotence-Key`, **≤40 ASCII**, ≥24 h | HMAC (header not found); request signing is `GCS v1HMAC` | fat JSON array; 5 retries over ~35 h |
| **Mollie** | **not usable**: Europe only; SGD/PHP cards+PayPal only, no IDR/VND | **decimal string** "10.00" | not supported | `Idempotency-Key`, **1 h only**; 400 on different params | **none**: body is just `id`, you re-fetch | thin |
| **PPRO** | SEA countries in the site filter, but no SEA methods surfaced | minor units (ISO) | ISO (2) | `Request-Idempotency-Key`, 24 h; 409 on different body | ? | ? |
| **Worldpay** | ? | ? | ? | ? | ? | ? |

Further facts that shape the design:

- **Capture semantics differ by method, not only by provider.** Stripe
  PayNow and PromptPay support neither manual capture nor partial capture.
  QR and wallet methods are effectively a sale. Card holds expire (Checkout.com
  voids an uncaptured payment after 7 days; Worldline holds last 7 days for a
  final authorisation and up to 30 for a pre-authorisation; Nuvei holds last
  7–31 days). Refunds also vary. Stripe PromptPay refunds need the buyer's
  bank account number. 2C2P refunds only settled transactions, and some are
  async (`REFUND_PENDING`). Mollie delays refunds by 2 h.
- **Ordering is never guaranteed** (Stripe, Checkout.com and Xendit say so
  outright), and duplicates are expected. This matches our Messaging rules.
- **Request authentication schemes vary widely.** They include a bearer API
  key (Adyen, PPRO, Checkout.com), OAuth client credentials (Checkout.com,
  Mollie Connect), Basic auth (Xendit), per-request HMAC signing (Worldline),
  JWT/JWE payloads (2C2P) and per-call SHA-256 checksums (Nuvei).
- **Base URLs are per merchant** at Adyen (live prefix) and Checkout.com
  (MSSD prefix, else 403). They are configuration, not constants.
- **Settlement data** comes as CSV reports (Adyen `settlement_detail_report`,
  2C2P SFTP CSVs before 04:00 GMT+7, Worldline exports), report APIs (Xendit,
  Checkout.com) or a balances API (Mollie). Fees are generally known only at
  settlement.

### Provider shortlist (for discussion)

Based only on the docs read, and to be confirmed with each provider's sales
team for entity eligibility and pricing:

- **Global card acquiring plus wallets:** Adyen or Checkout.com. Adyen's
  long idempotency window and IDR=0 fit our rules well. For Checkout.com,
  the SEA local methods are still beta for SG merchants.
- **SEA local methods and payouts:** Xendit (broadest documented coverage in
  ID/PH/TH/MY/VN, with a Payouts API) and/or 2C2P (broadest QR coverage
  including PayNow, VNPAY and MoMo, plus payouts).
- **Drop for now:** Mollie (no SEA), Worldline (no SEA methods), Nuvei (no
  SEA methods found). PPRO is aggregator-only and comes later via a PSP if at
  all. Worldpay is unknown and needs a sales call or rendered docs.

## 3. Architecture overview

```
 merchant ─▶ Merchant API (JSON) ─┐            ┌─▶ hosted checkout (HTML) ◀─ buyer
 Shopify  ─▶ Shopify adapter  ────┤            │
                                  ▼            ▼
                    core: checkout · payment · refund · dispute
                          tax · invoice · ledger · payout
                                  │  (all writes + outbox in one tx)
                                  ▼
                    outbox ─▶ workers (FOR UPDATE SKIP LOCKED)
                                  │
                ┌─────────────────┼──────────────────┐
                ▼                 ▼                  ▼
        provider port      merchant webhooks    payout rail
     (adyen, checkout_com,   (signed, retried)
      xendit, 2c2p, mock)
                ▲
 PSP webhooks ─▶ psp_inbox (verify → store raw → 200) ─▶ normalizer
 PSP reports  ─▶ settlement importer ─▶ ledger entries + reconciliation
```

Rules applied: Transactional outbox, idempotent consumers, per-aggregate
ordering, state machines driven by messages, one module per PSP, and exactly
one place that picks a provider by name.

## 4. Canonical domain model

Our types never contain provider vocabulary. Provider identifiers live only
in `provider_*` columns and in the provider modules.

```
Money            = { amount_minor: int64, currency: CurrencyCode }
ProviderName     = adyen | checkout_com | xendit | two_c2p | stripe | mock     # closed enum
ProviderAccount  = { id, provider: ProviderName, entity_id, environment,
                     base_url, credentials_ref }  # ref to a key in .env, never the secret

MethodFamily     = card | wallet | qr | bank_redirect | virtual_account | otc | direct_debit
PaymentMethod    = { family: MethodFamily, scheme: text }   # e.g. qr/paynow, wallet/gcash

CheckoutSession  (aggregate, our own; the merchant creates it)
  state: open | completed | expired | canceled
  lines, buyer_country_evidence[], tax_quote, totals: Money

PaymentAttempt   (aggregate; one per try against one provider account)
  provider_account_id, provider_reference?, idempotency_keys[]
  state:
    created
    | requires_buyer_action { kind: redirect | qr | app_switch | otp, expires_at }
    | authorized { capturable: Money, hold_expires_at? }
    | captured   { captured: Money }       # covers sale methods too
    | failed     { reason: DeclineReason }
    | voided
    | outcome_unknown { since }            # timeout: never retried elsewhere, see §5.3
    | expired

Refund   (aggregate, child of a captured attempt; same provider account, always)
  state: requested | pending | succeeded | failed
Dispute  (aggregate) state: opened | evidence_required | submitted | won | lost | accepted
```

The state transitions are a table in code (from-state, event → to-state).
An event that does not fit the current state is either a stale duplicate
(no-op, logged) or out of order (parked and retried after the next fetch of
the true state). It is never applied blindly.

Amounts: we keep our own currency-exponent table (AGENTS.md). Each provider
module also holds **its own exponent table** and a pair of functions:

```
to_provider_amount(Money)  -> Ok(ProviderAmount) | Err(not_representable)
from_provider_amount(...)  -> Ok(Money)          | Err(not_representable)
```

Decimal-string providers (Mollie, Nuvei, 2C2P) format by integer division and
padding, never through a float. A conversion that is not exact is an error
and is not rounded.

**IDR** (decided 04-Nov-2026: exponent 0, departing from ISO 4217's 2).
Adyen also uses 0, so no conversion is needed. Checkout.com, Stripe and
PPRO use 2: outbound is an exact ×100. Inbound, a provider amount such as a
fee of IDR 1,234.56 is not representable. The provider module reports it as
`not_representable` with the exact provider value. The settlement importer
is the one place that rounds it, with a named mode, and it books the
remainder to a rounding account. Tax, fee and FX calculations in IDR round
to whole rupiah, as they do for any 0-exponent currency (VND, JPY).

## 5. Provider port

### 5.1 The interface every provider module implements

Plain functions over plain records. Each returns a value-level result, and a
provider module never touches the database.

```
capabilities(account)                       -> CapabilitySet
create_payment(account, PaymentRequest, idem_key)
    -> Ok(ProviderPayment{reference, state, buyer_action?})
     | Err(Declined{reason} | InvalidRequest{detail} | Unavailable{retryable}
           | OutcomeUnknown | NotSupported)
get_payment(account, provider_reference | our_reference) -> Ok(ProviderPayment) | Err(...)
capture(account, ref, Money, final: bool, idem_key)  -> Ok(OperationAccepted|Done) | Err(...)
void(account, ref, idem_key)                         -> ...
refund(account, ref, Money, RefundExtra?, idem_key)  -> ...
verify_webhook(account, raw_request)        -> Ok(VerifiedPayload) | Err(BadSignature)
parse_webhook(VerifiedPayload)              -> [ProviderEvent]   # thin: carries only refs
import_settlement(account, ReportFile)      -> [SettlementLine] | Err(ParseError)
```

`ProviderEvent` and `ProviderPayment.state` are mapped by the provider module
into **our** closed state set. Raw codes (`AUTHORISATION`,
`payment_captured`, statusCode `9`, `ppp_status=OK`) are stored for audit but
never leave the module.

`CapabilitySet` is data. For each (method, country, currency) it lists:
manual capture, partial capture, multi-capture, partial or multiple refund,
async refund, refund extra fields (for example PromptPay's bank account),
hold duration, min/max amount, idempotency window, webhook model
(thin/fat), and whether `get_payment` works by our reference. The
orchestration code branches on capabilities, never on provider names.

### 5.2 Choosing a provider: the routing table

The **only** place that maps a name to a module is a `provider_registry` that
dispatches on `ProviderName` (one exhaustive match). Which account to use for
a new attempt comes from data. This is deliberately small (decided
04-Nov-2026: no orchestration vendor or smart routing). With one or two PSPs
it is a handful of rows, and failover lists can stay empty until we need
them:

```
routing_rule (effective-dated, versioned, append-only)
  match: buyer_country?, currency, method family/scheme?, amount range?, merchant_id?
  → ordered list of provider_account_id   # first = primary, rest = failover
```

The rule version is stored on each attempt. Switching providers means a new
routing rule and requires no deploy. In-flight payments keep their provider.
Captures, voids, refunds and disputes always go to the account that took the
attempt.

Failover to the next account happens **only** on a definite non-charge:
`Unavailable` before the request reached the provider, or a decline class
marked safe to retry elsewhere. It never happens on `OutcomeUnknown`.

### 5.3 Outbound calls: idempotency and unknown outcomes

- Every outbound money-moving call is a command written to the outbox in the
  same transaction as the state change, then executed by a worker. A request
  handler never calls a PSP directly.
- One `psp_request` row per (attempt, operation, sequence) holds the
  idempotency key, with a unique constraint. The key is a UUIDv4 (36 ASCII
  characters, which fits Worldline's 40-character limit and Adyen's 64), is
  generated once and is reused on every retry.
- Retry windows range from 1 h (Mollie) to 14 days (Adyen), and Nuvei
  rejects a duplicate instead of replaying it. So a worker may only retry
  blindly **inside the provider's window from `capabilities`**. Outside it,
  or after a timeout, the worker first calls `get_payment` by our reference
  and moves to the real state. While the state is unknown the attempt stays
  `outcome_unknown`, and a reconciler sweeps those attempts.
- We always pass our own attempt id as the provider's merchant reference
  (`merchantReference`, `reference`, `clientUniqueId`, `invoiceNo`, …), so
  lookups and settlement matching work without the provider's id.

### 5.4 Inbound webhooks

1. `POST /psp/{provider_account_id}/webhook`: verify with that account's
   secret (`verify_webhook`). On a bad signature, return 401 and store
   nothing. Mollie has no signature, so its body is treated as an untrusted
   hint and only the re-fetch counts.
2. Insert the raw body and headers into `psp_inbox` with a unique key on
   `(provider_account_id, provider_event_id or hash)`, commit, and return the
   provider's expected ack. That ack is a 2xx in general, and 2C2P and
   Adyen's legacy setup may want a specific body. The 10-second budget
   (Checkout.com) and 15-second budget (Mollie) mean no other work happens
   here.
3. A worker parses the stored body. For thin events, or where state matters
   (capture, refund, dispute), it calls `get_payment` and uses the fetched
   state as the truth. It applies the transition and emits our past-tense
   facts (`payment_authorized`, `payment_captured`, `refund_succeeded`,
   `dispute_opened` …) through the outbox.

### 5.5 Ledger and reconciliation

Accounts are per currency. Rates and fees always come from the tax modules
and the settlement data, never from constants.

```
at capture (example, SGD buyer in SG):
  Dr psp_clearing:{provider_account}     gross
  Cr tax_payable:SG-GST                  tax    (rule id + version stored)
  Cr merchant_payable:{merchant}         net of tax and our fee
  Cr revenue:mor_fee                     our fee (rounding mode named; remainder booked)
at settlement import (fees known now):
  Dr bank:{entity}:{currency}            settled
  Dr expense:psp_fees:{provider}         fee
  Cr psp_clearing:{provider_account}     gross
refund / chargeback: new reversing entries, never edits
```

`import_settlement` turns each provider's report into `SettlementLine`s
(our reference, gross, fee, net, FX if any, payout batch). A matcher pairs
lines with captures. A line that does not match, or an amount that differs,
becomes a `reconciliation_exception` row for a human. The `psp_clearing`
balance per account must trend to zero after each payout batch, and that
balance is the health metric.

### 5.6 Stored payment methods and switching

Tokens belong to the PSP that issued them. Switching cards between PSPs is
therefore easy for one-off payments and hard for stored cards and
subscriptions. Options, from cheapest to most costly:

1. Accept lock-in for stored cards. New cards follow routing, and old tokens
   stay where they are. (Chosen for v1, 04-Nov-2026.) The exit path is the
   PCI-compliant card data export that large PSPs typically offer on
   request, from one PCI DSS Level 1 provider to another. We never handle
   the data ourselves. Before signing a contract, confirm with each PSP
   that it supports such an export. Our `stored_payment_method` row keeps
   our own id, the provider account and the provider token. After an
   export, a new row points at the new provider's token and the old one is
   closed, so subscriptions only ever reference our id.
2. Network tokens (Adyen, Checkout.com, Worldpay, Xendit and Nuvei document
   them) can be portable, depending on the token requestor arrangement.
3. A third-party PCI vault (Basis Theory, VGS, Spreedly) that forwards to
   any PSP. It keeps PANs out of our systems, so our no-PAN rule holds, but
   it adds a vendor and per-call cost.

Never: storing PANs ourselves.

### 5.7 Mock provider

`mock` implements the full port in-process. It has deterministic scenarios
keyed by amount or by a test token (approve, decline, require action,
timeout→unknown, async refund, chargeback, duplicate and out-of-order
webhooks), and a fake settlement report generator. It is the default
outside `ENVIRONMENT=prod`. Each real provider module also gets contract
tests against that provider's sandbox, run on demand and not in normal CI.

## 6. Merchant side

### 6.1 Merchant API (JSON)

- Auth: per-merchant secret API keys (hashed at rest), separate test and
  live keys.
- `Idempotency-Key` is **required** on every POST, stored with a unique
  constraint on (merchant, key). A replay with different params returns 409.
  The window is long (e.g. 7 days), because we promise more than our PSPs.
- Resources: `checkout_sessions` (create, get, expire), `payments` (get,
  list), `refunds` (create, get), `invoices` (get, as PDF/HTML), `disputes`
  (get, submit evidence), `payouts` and `balance` (derived from the ledger),
  `webhook_endpoints`.
- Money is always `{ "amount_minor": 1999, "currency": "SGD" }`. Each price
  declares whether it includes tax. Totals come back with the tax breakdown
  and the rule version.
- Merchants never see provider names or provider ids. "Which PSP" is our
  concern, and that is what lets us switch.

### 6.2 Hosted checkout (server-rendered HTML)

This is the main channel and the one Shopify could reuse. Flow: address and
country → tax quote (from the jurisdiction module, evidence stored) → method
choice (filtered by routing and capabilities) → pay → receipt and invoice in
our name.

JavaScript (decided 04-Nov-2026): PSP components (Adyen Components,
Checkout.com Flow, Xendit/2C2P SDKs) are allowed for the payment step. Our
own steps (address, tax quote, method choice, receipt) stay server-rendered
forms. The server finalizes from the webhook or a fetch, never from the
browser. A redirect to the provider's hosted page stays available as a
fallback, for example when a component fails to load.

### 6.3 Merchant webhooks

These are produced from our outbox, signed with HMAC-SHA256 over
`timestamp.event_id.body` with a per-endpoint secret, and retried with
backoff for several days. Events are versioned (`payment_captured`, schema
v1) and ordering is documented as per-aggregate only. Merchants dedupe on
`event_id`, and we provide a resend endpoint.

### 6.4 Merchant payouts

The payable comes from the ledger. A payout run (state machine: scheduled →
instructed → paid | failed → reversed) uses a payout rail behind its own
port, with the same pattern as the PSP port: one module per rail, a mock,
and an idempotency key per instruction. Reserves and rolling holds against
chargebacks are ledger accounts, not flags.

Rail choice, provider versus our own bank:

| | Payout provider (Xendit, 2C2P, Wise, Airwallex…) | Our own bank (bulk file or bank API) |
|---|---|---|
| Integration | API plus status webhooks | Uneven across SEA; often batch files |
| Reach | Local transfer systems in many countries | Local within its own country; SWIFT elsewhere (slow, per-wire fees) |
| FX | Provider converts, with a markup | Bank rates; better at volume |
| Funding | Often paid from the balance the PSP collected | Funds must settle to our bank first |
| Checks | Beneficiary validation, status per transfer | More of it on us |
| Cost | Fee per payout plus FX margin | Cheaper at scale |

Decided 04-Nov-2026 (after "VN merchants, physical goods"): **our own bank
for v1**. Weekly batched USD SWIFT wires from our entity to each
merchant's VN bank, with the instruction file generated from the ledger by
a `bank_file` rail module.

- **Why:** a USD wire from the foreign buyer named in the contract is the
  usual way a VN exporter receives export proceeds. The sender on the
  merchant's statement is our entity, which matches the contract and our
  statement or self-billed invoice. Early volume is low, so the manual work
  of uploading files is acceptable, and the build cost is minimal.
- **Costs to manage:** fees are flat per wire, so payouts are batched
  (weekly, with a minimum amount). Middle banks may deduct fees; the choice
  between OUR (we pay all fees) and SHA (fees shared) is to be decided.
  The wire takes 1–3 days, and returned wires need a manual process
  (returned → reversing ledger entry → merchant notified).
- **Risk controls for physical goods:** payout eligibility starts N days
  after delivery confirmation or capture, and a rolling reserve is kept as
  a ledger account.
- **Later:** a provider rail (Wise, Airwallex, Payoneer, or Xendit/2C2P if
  they reach VN) behind the same port, for many small merchants or for
  faster or VND payouts. Before using one, check with the provider and a VN
  bank that the merchant's statement shows a remitter that matches the
  export contract.

Needs VN FX/tax advice before launch (§8): documentation that VN banks
require for export proceeds, and whether VND payouts through a local
licensed partner are allowed for this flow.

### 6.5 Physical goods: customs, duties and couriers

Decided 04-Nov-2026: goods ship from VN merchants to SEA buyers through one
or more **courier partners that offer DDP and customs clearance from VN
into SEA**. Merchants create shipments through us, not on their own
courier accounts, so the customs data and our registration numbers are
always right and we receive tracking events. Candidates to evaluate: Ninja
Van, J&T, DHL eCommerce/Express (duties-paid billing), Janio.

Terms: the **importer of record** is the party legally responsible for an
import (declaration, duty and import VAT/GST, admissibility). For B2C
parcels it is usually the buyer, cleared by the courier. Low-value goods
regimes make the **foreign seller** collect the tax at checkout instead.
That seller is us, and we cross the thresholds by combining all
merchants' sales. Under **DDP** the buyer pays one total at checkout and we
pay duties through the courier. Under **DAP** the buyer pays on delivery.
We choose DDP.

Buyer-country rules as found 04-Nov-2026 (all to be confirmed by an
adviser; every one becomes an effective-dated rule in the per-country
module):

- **SG:** 9% GST on goods ≤ SGD 400 by air or post via OVR. Register above
  SGD 1M global turnover and SGD 100k of these sales to SG. Unclear whether
  the threshold is per item or per consignment, and whether shipping counts.
- **MY:** 10% sales tax on goods ≤ RM 500, on the sale price. Register above
  RM 500k in 12 months.
- **TH:** since 01-Jan-2026 no 1,500 baht exemption; duty plus 7% VAT from the
  first baht.
- **ID:** duty de minimis USD 3; VAT applies regardless (rate to confirm).
- **PH:** de minimis PHP 10,000; VAT treatment below it disputed (CAO
  02-2025).

Update 06-Nov-2026 (research, see `docs/research-logistics.md`; nothing
decided yet):

- Candidates: Ninja Van stopped VN express on 30-Sep-2025 and DHL eCommerce
  left VN in 2021, so both are out as VN-origin carriers. Remaining SEA
  candidates: Janio, Boxme, J&T International (J&T VN does not list ID).
  DHL Express, FedEx or UPS cover express and the rest of the world.
- SG: the S$400 test is per item, on sales value excluding shipping; GST is
  charged on the price including shipping (IRAS guide, 5th ed.).
- ID: VAT is 12% on an 11/12 base (11% effective). Apparel, bags and shoes
  pay 25% duty plus 5% income tax (PMK 4/2025). Permendag 31/2023 (US$100
  minimum price per unit) may block direct cross-border sales: adviser.
- PH: CAO 02-2025 is a fee schedule, not a VAT ruling. The statute exempts
  duties and taxes below PHP 10,000.

Data needed. A courier clears customs but only declares what we send:

- **Product (merchant enters; contract makes the merchant guarantee
  accuracy):** customs description, country of origin, weight and
  dimensions, restricted-goods category (required). HS code (strongly
  recommended: DDP quotes duty at checkout, before the courier sees the
  parcel; a landed-cost API proposes it from the description and the
  merchant confirms).
- **Order:** value and currency per line, quantity, buyer name, address and
  phone, and a buyer ID or tax number where a country requires it.
- **Our config:** SG OVR GST number, MY LVG number, other registrations
  per country.

Design additions:

- A `landed_cost` interface per buyer country (duty, import tax, de
  minimis, effective-dated), first backed by a landed-cost API (Zonos,
  Avalara or similar), with the provider, rule version and inputs stored
  on the checkout.
- A courier interface, one module per courier, same pattern as the PSPs:
  rate quote, create shipment and label, tracking webhooks into an inbox,
  duty and shipping invoices imported for reconciliation, plus a mock.
- A **Shipment** aggregate: created → labelled → in_transit → delivered |
  returned | lost. `shipment_delivered` (plus a delay) makes the merchant's
  balance eligible for payout (§6.4).
- Ledger: duties collected → `duties_payable:{country}`; shipping collected
  → `shipping_payable:{courier}`. Both are cleared by the courier's
  invoice, and the difference between quote and actual goes to
  `duty_variance` (our cost unless the contract passes it on).
- Merchant onboarding: a prohibited and restricted goods list per buyer
  country (product compliance risk is ours as seller).
- VN export side: the merchant or courier files the VN export
  declaration; the merchant likely needs it for 0% export VAT (VN
  adviser).

## 7. Shopify: the biggest risk in this plan

What Shopify's docs say:

- Payments apps are **invitation-only**, and only approved Payments Partners
  may build them. Review takes two stages, a revenue share applies above
  USD 150k, and general availability needs ≥50 stores and >USD 1M processed.
- A payments app may use **only** the Payments Apps API and mandatory
  webhooks. It receives a payment session for a total that Shopify already
  computed (including Shopify's tax). The docs show no way to change the
  total or the tax.
- "Most merchants act as their own merchant of record". The third-party MoR
  path is Managed Markets with Global-e, and that is limited to US, CA and UK
  merchants. The docs read do not say whether a payments app can make a
  third party the seller of record.

So a plain "Shopify payments app" gets us **payment processing** but likely
not **MoR**: the order and the invoice would show the merchant as seller,
and the tax would be Shopify's. Realistic options:

| Option | MoR? | Feasibility |
|---|---|---|
| A. Payments app (offsite extension → our hosted payment page) | Unclear. Shopify's order says the merchant sold, and we would invoice too, which is a legal conflict | Needs Shopify approval; the flow itself is well specified |
| B. Shopify app that syncs catalog/orders, with the sale itself on our hosted checkout (e.g. for cross-border or digital goods) | Yes, we sell | Bypasses Shopify checkout; must be checked against Shopify's terms |
| C. Partnership like Global-e's Managed Markets | Yes | Business deal, not engineering |

Proposal: build the Merchant API and hosted checkout first (they are needed
in every case). Do not build for Shopify yet. Hold one exploratory call
with Shopify once we have the entity decision, a working hosted checkout
and a few interested merchants. Decide A, B or C from that answer, not from
assumptions. Meanwhile, ask the first merchants where they actually sell:
many SEA sellers use Shopee, Lazada, TikTok Shop or WooCommerce rather
than Shopify.

Questions for that call:

1. Can a payments app be the merchant of record? Can we issue the invoice
   in our name and own the tax when the Shopify order shows the merchant
   as seller?
2. Can tax be set or overridden for orders that go through us, or would
   merchants turn off Shopify Tax for the markets we cover?
3. Is an SG entity or a Dutch BV eligible for the Payments Partner
   program? With which `supported_countries` and currencies, and does
   Vietnam work?
4. Is an app allowed that sends some buyers (cross-border, digital goods)
   to our own checkout? Or is a Managed Markets-style partnership the only
   route?
5. Revenue share, review timeline, and what PCI and licensing evidence
   they expect.

## 8. Compliance and licensing touchpoints (named, not answered)

- Which of our entities holds each acquiring contract, and whether local
  methods need a local entity or licence (Stripe PayNow needs an SG account
  and PromptPay a TH account; Checkout.com's SEA methods are beta for SG
  merchants). Collecting funds for merchants and paying them out may need
  payment licences (for example MAS PSA in Singapore) even as MoR.
- KYB of merchants before payouts; this is our process, not the PSP's.
- Data residency of buyer PII and invoices per country.
- Tax regimes for foreign sellers of digital services versus physical goods
  differ per country. What merchants sell decides which tax modules come
  first.

## 9. Project rules this design applies

Money as minor units plus currency with one exponent table (§4); errors as
values (§5.1); closed state sets (§4); transactional outbox and no PSP calls
from handlers (§5.3); idempotent consumers via `psp_inbox` and unique keys
(§5.4); per-aggregate ordering only (§5.4, §6.3); append-only double-entry
ledger and reversing entries (§5.5); one module per PSP and one selection
point (§5.2); mock provider by default (§5.7); no PANs (§5.6); tax rule and
version stored per transaction (§5.5, §6.2); compliance named, not assumed
(§8).

## 10. Proposed first slice

1. Choose the language (it blocks everything else; per AGENTS.md this is its
   own proposal).
2. Migrations: currencies, ledger, outbox/inbox, merchants, checkout
   sessions, payment attempts, psp_request, psp_inbox.
3. Provider port + `mock` + routing table + attempt state machine.
4. Merchant API: checkout sessions + payments + webhooks; hosted checkout
   with the redirect baseline.
5. One real card provider (Adyen or Checkout.com) + one SEA-local provider
   (Xendit or 2C2P), each with settlement import.
6. Refunds, disputes, payouts. Then Shopify, depending on §7.

## 11. Open questions

Answered 04-Nov-2026 (see §0): goods (both); IDR (ISO 2); orchestration
vendor (no); saved cards (yes, PSP-bound, export later); Shopify (nobody
has talked to them; explore later, §7).

Still open:

1. SG entity or Dutch BV? (This blocks PSP contracts and the licensing
   analysis.)
2. Physical goods: confirm the per-country rules in §6.5 with a customs
   and tax adviser (DDP via courier partners is decided).
3. Which couriers do the first merchants use today, what is the typical
   order value (it decides how often de minimis rules apply), and which
   buyer countries come first?
4. Expected buyer mix by method (cards vs QR/wallets), once the first
   merchants are known.

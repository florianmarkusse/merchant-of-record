# Payment providers, merchant payouts and Shopify

Status: input for what is not built: choosing real PSPs, stored payment
methods, refunds and disputes (slice 8), merchant webhooks, payouts
(slice 9) and Shopify. The payment flow against the fake PSP is built
(`plan-payments.md`); the port, the attempt, webhooks, settlement and the
ledger entries are described there.

## 1. Context and decisions

- **Legal entity**: a Singapore entity or a Dutch BV, not decided (§10).
  It matters a lot: local SEA methods and the SEA-native PSPs fit an SG
  entity much better, and licensing differs (§9).
- **Goods**: physical goods from launch (decided 04-Nov-2026: VN and SEA
  merchants mostly sell physical goods); digital goods are in scope too,
  with the digital-services rules for foreign sellers.
- **Launch**: merchants in Vietnam first, selling to buyers in other SEA
  countries.
  - **Buyer side**: the buyers' local methods come first (PayNow,
    PromptPay, DuitNow/FPX, QRIS and e-wallets, GCash/Maya) plus cards. VN
    checkout methods (MoMo, VNPay, ZaloPay) are not first. Xendit and 2C2P
    cover the buyer side best (§3).
  - **Merchant side**: KYB of Vietnamese companies, a contract per
    merchant, and payouts into Vietnam. VND is a restricted currency, so
    the payout is probably foreign currency (USD) into the merchant's VN
    bank as export proceeds, converted by that bank. To be verified (§7,
    §9).
  - **Legal chain**: under MoR the merchant sells to us, and we resell to
    the buyer. The Vietnamese merchant therefore exports goods or services
    to our entity and needs the matching documents from us (a contract, a
    self-billed invoice or statement per payout) for its own VN export VAT
    and corporate income tax. Needs advice.
- **No orchestration vendor or switching layer** (decided 04-Nov-2026): one
  module per PSP behind one port, and routing rows (`plan-payments.md` §3).
- **Saved cards and subscriptions**: probably in scope, with tokens bound
  to the PSP that issued them (§4).

## 2. Framing: these providers are acquirers, not merchants of record

Adyen, Mollie, PPRO, Worldline, Worldpay, Checkout.com, Nuvei, Stripe,
Xendit and 2C2P do not act as merchant of record for us. They are PSPs and
acquirers (PPRO is a local-payment-method aggregator). **We** are the
merchant of record. Each provider takes the buyer's money into an account
that belongs to one of our legal entities. The tax, the invoice, refunds,
chargebacks and merchant payouts stay with us. The companies that offer
MoR as a product (Paddle, Lemon Squeezy, FastSpring, Global-e, Stripe's
"Managed Payments") compete with us. They are not suppliers.

For Vietnamese merchants (noted 10-Nov-2026): the physical-goods MoRs
(Global-e, Reach, Digital River, ESW, Passport's Seller of Record,
OpenBorder) and DDP networks that pay the border tax for the merchant
(FlavorCloud, Zonos's guarantee mode) are competitors, not suppliers.
None of them shows support for a Vietnamese seller entity or for goods
shipped from Vietnam (`docs/research/logistics/04-landed-cost-returns.md`).
A Vietnamese merchant's way in is a Singapore or US holding company plus
enterprise volume; Global-e is said to want about US$5M+ a year. Both the
entity limit and the volume figure come from a third-party summary and are
UNVERIFIED. If they hold, small and mid-size Vietnamese sellers cannot
take that route, and they are our market. What we buy from this space is
only calculation (a duty source) and carrier DDP with duties billed to us
(`plan-logistics.md` §2).

Consequences:

- We use each provider's **plain merchant/checkout API**, not its
  marketplace product. Adyen's docs say this directly: Adyen for Platforms
  is for onboarding third-party sellers, and a merchant of record uses
  standard Checkout. The same holds for Stripe Connect: without
  `on_behalf_of`, the platform is the business of record and carries
  refunds, chargebacks and fees. Their platform products only become
  relevant if we later let merchants be the seller of record.
- Merchant payouts are **our** ledger plus a payout rail, not a provider
  "split".
- Each provider contract is held by one of our entities, and local methods
  often need a local entity: Stripe PayNow needs a Stripe SG account and
  PromptPay a Stripe TH account. Licensing and entity structure decide
  which providers we can use (§9).

## 3. What the provider docs say (read 04-Nov-2026)

Sources were each provider's public developer docs. "?" means the docs did
not say, or could not be read. Worldpay's docs render client-side and
could not be read. Check again in current docs before a provider is
chosen.

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

What shapes the design:

- **A provider's key window decides its guard.** A key honoured for a
  window (Mollie 1 h, Checkout.com 72 h, Adyen 7–14 days) counts as guard
  `none`, and Nuvei rejects a duplicate instead of replaying it
  (`plan-architecture.md` §5).
- **Capture semantics differ by method, not only by provider.** Stripe
  PayNow and PromptPay support neither manual nor partial capture. QR and
  wallet methods are effectively a sale. Card holds expire (Checkout.com
  voids an uncaptured payment after 7 days; Worldline holds last 7 days
  for a final authorisation and up to 30 for a pre-authorisation; Nuvei
  7–31 days).
- **Refunds vary.** Stripe PromptPay refunds need the buyer's bank account
  number. 2C2P refunds only settled transactions, and some are async
  (`REFUND_PENDING`). Mollie delays refunds by 2 h.
- **Ordering is never guaranteed** (Stripe, Checkout.com and Xendit say so
  outright), and duplicates are expected.
- **Request authentication varies widely**: a bearer API key (Adyen,
  PPRO, Checkout.com), OAuth client credentials (Checkout.com, Mollie
  Connect), Basic auth (Xendit), per-request HMAC signing (Worldline),
  JWT/JWE payloads (2C2P) and per-call SHA-256 checksums (Nuvei).
- **Base URLs are per merchant** at Adyen (live prefix) and Checkout.com
  (MSSD prefix, else 403): configuration, not constants.
- **Webhook acks have budgets**: 10 s at Checkout.com, 15 s at Mollie, and
  2C2P and Adyen's legacy setup may want a specific body. Mollie has no
  signature, so its body is an untrusted hint and only the re-fetch counts.
- **Amounts**: decimal-string providers (Mollie, Nuvei, 2C2P) are
  formatted by integer division and padding, never through a float.

**Shortlist** (from the docs only; to confirm with each provider's sales
team for entity eligibility and pricing):

- **Global card acquiring plus wallets**: Adyen or Checkout.com. Adyen's
  long key window and IDR=0 fit our rules well. For Checkout.com, the SEA
  local methods are still beta for SG merchants.
- **SEA local methods and payouts**: Xendit (broadest documented coverage
  in ID/PH/TH/MY/VN, with a Payouts API) and/or 2C2P (broadest QR coverage
  including PayNow, VNPAY and MoMo, plus payouts).
- **Dropped for now**: Mollie (no SEA), Worldline (no SEA methods), Nuvei
  (no SEA methods found). PPRO is aggregator-only and comes later via a
  PSP if at all. Worldpay is unknown and needs a sales call or rendered
  docs.

## 4. Stored payment methods and switching

Tokens belong to the PSP that issued them. Switching cards between PSPs is
easy for one-off payments and hard for stored cards and subscriptions.
Options, from cheapest to most costly:

1. **Accept lock-in for stored cards** (chosen for v1, 04-Nov-2026). New
   cards follow routing, and old tokens stay where they are. The exit path
   is the PCI-compliant card data export that large PSPs offer on request,
   from one PCI DSS Level 1 provider to another; we never handle the data
   ourselves. Before signing, confirm with each PSP that it supports such
   an export. Our `stored_payment_method` row keeps our own id, the
   provider account and the provider token. After an export, a new row
   points at the new provider's token and the old one is closed, so
   subscriptions only ever reference our id.
2. Network tokens (Adyen, Checkout.com, Worldpay, Xendit and Nuvei
   document them) can be portable, depending on the token requestor
   arrangement.
3. A third-party PCI vault (Basis Theory, VGS, Spreedly) that forwards to
   any PSP. It keeps PANs out of our systems, but adds a vendor and a
   per-call cost.

Never: storing PANs ourselves.

## 5. Refunds, disputes and failover

- **Refund**: an aggregate, child of a captured attempt, always on the
  account that took the payment: `requested | pending | succeeded |
  failed`. Async refunds (`REFUND_PENDING`) and refunds that need extra
  buyer data (PromptPay's bank account) are capabilities of the method,
  not branches on the provider.
- **Dispute**: an aggregate, `opened | evidence_required | submitted | won
  | lost | accepted`.
- Both are reversing ledger entries, never edits.
- **Failover** to another account happens only on a definite non-charge:
  `Unavailable` before the request reached the provider, or a decline
  class marked safe to retry elsewhere. Never on `OutcomeUnknown`.
  Captures, voids, refunds and disputes always go to the account that
  took the attempt.
- We always pass our own attempt id as the provider's merchant reference
  (`merchantReference`, `reference`, `clientUniqueId`, `invoiceNo`, …), so
  lookups and settlement matching work without the provider's id.
- **Capability data per (method, country, currency)**: manual, partial and
  multiple capture, partial or multiple refund, async refund, refund extra
  fields, hold duration, minimum and maximum amount, webhook model.
  Feature code branches on these, never on provider names.

## 6. Merchant webhooks

Produced from our outbox, signed with HMAC-SHA256 over
`timestamp.event_id.body` with a per-endpoint secret, and retried with
backoff for several days. Events are versioned (`payment_captured`,
schema v1), arrive in any order and possibly twice: merchants dedupe on
`event_id`, and we provide a resend endpoint. They come with buyer emails
in one `notify` slice (`plan-payments.md` §11).

## 7. Merchant payouts

The payable comes from the ledger; only money in `bank` is paid out
(`plan-payments.md` §7). A payout run (`scheduled → instructed → paid |
failed → reversed`) uses a payout rail behind its own port, with the same
pattern as the PSP port: one module per rail, a fake, and an idempotency
key per instruction. Reserves and rolling holds against chargebacks are
ledger accounts, not flags.

| | Payout provider (Xendit, 2C2P, Wise, Airwallex…) | Our own bank (bulk file or bank API) |
|---|---|---|
| Integration | API plus status webhooks | Uneven across SEA; often batch files |
| Reach | Local transfer systems in many countries | Local within its own country; SWIFT elsewhere (slow, per-wire fees) |
| FX | Provider converts, with a markup | Bank rates; better at volume |
| Funding | Often paid from the balance the PSP collected | Funds must settle to our bank first |
| Checks | Beneficiary validation, status per transfer | More of it on us |
| Cost | Fee per payout plus FX margin | Cheaper at scale |

**Our own bank for v1** (decided 04-Nov-2026): weekly batched USD SWIFT
wires from our entity to each merchant's VN bank, with the instruction
file generated from the ledger by a `bank_file` rail module.

- **Why**: a USD wire from the foreign buyer named in the contract is the
  usual way a VN exporter receives export proceeds. The sender on the
  merchant's statement is our entity, which matches the contract and our
  statement or self-billed invoice. Early volume is low, so uploading
  files by hand is acceptable, and the build cost is minimal.
- **Costs to manage**: fees are flat per wire, so payouts are batched
  (weekly, with a minimum amount). Middle banks may deduct fees; OUR (we
  pay all fees) or SHA (shared) is to be decided. The wire takes 1–3
  days, and returned wires need a manual process (returned → reversing
  ledger entry → merchant notified).
- **Risk controls for physical goods**: payout eligibility starts N days
  after delivery (`plan-architecture.md` §9), and a rolling reserve is
  kept as a ledger account.
- **Later**: a provider rail (Wise, Airwallex, Payoneer, or Xendit/2C2P
  if they reach VN) behind the same port, for many small merchants or for
  faster or VND payouts. Before using one, check with the provider and a
  VN bank that the merchant's statement shows a remitter that matches the
  export contract.

Needs VN FX and tax advice before launch (§9): the documentation VN banks
require for export proceeds, and whether VND payouts through a local
licensed partner are allowed for this flow.

## 8. Shopify: the biggest risk

What Shopify's docs say:

- Payments apps are **invitation-only**, and only approved Payments
  Partners may build them. Review takes two stages, a revenue share
  applies above USD 150k, and general availability needs ≥50 stores and
  >USD 1M processed.
- A payments app may use **only** the Payments Apps API and mandatory
  webhooks. It receives a payment session for a total that Shopify already
  computed (including Shopify's tax). The docs show no way to change the
  total or the tax.
- "Most merchants act as their own merchant of record". The third-party
  MoR path is Managed Markets with Global-e, limited to US, CA and UK
  merchants. The docs read do not say whether a payments app can make a
  third party the seller of record.

So a plain "Shopify payments app" gets us **payment processing** but
likely not **MoR**: the order and the invoice would show the merchant as
seller, and the tax would be Shopify's. Options:

| Option | MoR? | Feasibility |
|---|---|---|
| A. Payments app (offsite extension → our hosted payment page) | Unclear. Shopify's order says the merchant sold, and we would invoice too, which is a legal conflict | Needs Shopify approval; the flow itself is well specified |
| B. Shopify app that syncs catalog/orders, with the sale itself on our hosted checkout (e.g. for cross-border or digital goods) | Yes, we sell | Bypasses Shopify checkout; must be checked against Shopify's terms |
| C. Partnership like Global-e's Managed Markets | Yes | Business deal, not engineering |

Proposal: build nothing for Shopify yet; the merchant API and hosted
checkout are needed in every case. Hold one exploratory call with Shopify
once we have the entity decision, a working hosted checkout and a few
interested merchants, and decide A, B or C from that answer. Meanwhile,
ask the first merchants where they actually sell: many SEA sellers use
Shopee, Lazada, TikTok Shop or WooCommerce rather than Shopify.

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

## 9. Compliance and licensing touchpoints (named, not answered)

- Which of our entities holds each acquiring contract, and whether local
  methods need a local entity or licence (Stripe PayNow needs an SG
  account and PromptPay a TH account; Checkout.com's SEA methods are beta
  for SG merchants). Collecting funds for merchants and paying them out
  may need payment licences (for example MAS PSA in Singapore) even as
  MoR.
- KYB of merchants before payouts: our process, not the PSP's.
- Data residency of buyer PII and invoices per country.
- Tax regimes for foreign sellers of digital services and of physical
  goods differ per country; what merchants sell decides which tax modules
  come first.

## 10. Open questions

1. SG entity or Dutch BV? This blocks PSP contracts and the licensing
   analysis.
2. Which couriers do the first merchants use today, what is the typical
   order value (it decides how often de minimis rules apply), and which
   buyer countries come first?
3. Expected buyer mix by method (cards vs QR and wallets), once the first
   merchants are known.

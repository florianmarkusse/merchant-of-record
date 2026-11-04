# Plan: merchant and buyer surfaces

Status: design proposal (2026-11-04), awaiting confirmation. No code yet.
This builds on `docs/plan-psp-integration.md` (core, PSP port, ledger,
couriers, payouts) and only covers what merchants and buyers touch.

## 1. The surfaces

"Our frontend for merchants" is really three groups. Because we are the
merchant of record, the buyer is **our** customer, so the buyer-facing pages
are ours too, not the merchant's.

| Group | Surface | Used by | v1? |
|---|---|---|---|
| Merchant | **Dashboard** (server-rendered HTML) | merchant staff | yes |
| Merchant | **Merchant API** (JSON) + webhooks | merchant developers | yes |
| Buyer | **Hosted checkout** | buyers | yes |
| Buyer | **Order page**: status, tracking, invoice, return request | buyers | yes |
| Buyer | **Payment link pages** (a product page that leads to checkout) | buyers | yes |
| Channel | **Shopify adapter** | Shopify merchants | later (§6) |
| Channel | Other platform plugins (WooCommerce, Haravan, Sapo …) | merchants on them | later, from data (§6) |

All HTML is server-rendered (AGENTS.md). JavaScript appears only where a PSP
component needs it, on the payment step.

## 2. Ways a merchant can sell through us

Ordered by how much work it is for the merchant. **Every channel is a thin
client of the same public API** (decided 2026-11-04): it creates a checkout
session, sends the buyer to the same hosted checkout and reacts to the same
webhooks. Tax, landed cost, invoicing and the seller identity therefore
always come from the core. A new channel is a small adapter, not a change
to the core, and which one we build next is only a question of demand.
Shopify is the exception, because its own checkout computes tax and owns
the order (§6).

1. **Payment links (no code).** The merchant creates a product in the
   dashboard and shares a link in Facebook, Instagram, TikTok, Zalo or chat.
   Social selling is common among SEA and VN sellers, so this may be the
   fastest way to the first merchants. (A question to check with them, §9.)
2. **Hosted checkout via the API.** The merchant's own site creates a
   checkout session, redirects the buyer to us and receives webhooks. This
   is the same shape as Stripe Checkout or Paddle.
3. **Platform plugins.** Shopify or another platform, which call option 2
   underneath.
4. **Embedded checkout** inside the merchant's site. Not in v1: we have to
   control the price build-up (tax, duties, shipping) and the seller
   display, and a redirect does that with far less risk.

## 3. Domain records the surfaces need

These records are new or sharper than in the PSP plan. As in that plan, they
are plain records with closed state sets, and changes are append-only where
money or tax is involved.

```
Merchant        { id, legal_name, country: VN, kyb_state, payout_account, settings }
  kyb_state: pending | submitted | approved | rejected | suspended

MerchantUser    { id, merchant_id, email, role: owner | admin | fulfilment | developer | viewer,
                  totp_enabled }                       # 2FA required: this is money

ItemData        { name, unit_price: Money, tax_behavior: exclusive | inclusive,  # default exclusive
                  merchant_sku?, image_url?,
                  customs: { description, origin_country, weight_g, dims_mm?, hs_code? } }

Product         { id, merchant_id, ItemData, active }   # optional: ItemData saved under an id

LineInput       = { product_id, quantity } | { ItemData, quantity }   # what the API accepts
SoldLine        { ItemData, quantity, product_id? }   # snapshot stored on session and order

PaymentLink     { id, merchant_id, lines: [LineInput], quantity_rule, active }
                # a saved checkout template; opening it creates a CheckoutSession

CheckoutSession { id, merchant_id, payment_link_id?, lines: [SoldLine], buyer?, ship_to?,
                  quote?, state, return_url, cancel_url, merchant_reference? }
  state: open | quoted | paying | completed | expired | canceled

Quote           { session_id, valid_until, presentment_currency, fx_rate?,
                  lines[{ goods, tax, duty }], shipping, totals, rule_versions[] }
                # immutable; a changed address or quantity makes a new quote

Order           { id, session_id, merchant_id, buyer, ship_to, invoice_id, state }
  state: paid | fulfilling | shipped | delivered | return_requested | returned | refunded | closed
```

- **No required catalog** (decided 2026-11-04: a mandatory catalog is too
  restrictive). The merchant's server sends the lines with their price and
  customs data on each checkout, like Stripe's inline `price_data`. The
  price is trusted because a secret-key call creates the session; prices
  never come from the browser.
  - **Required per line before payment:** customs description, origin
    country and weight. The HS code is optional: if missing, we suggest one
    via the landed-cost API, and the duty quote notes that it is estimated.
  - **Restricted-goods screening is automatic** at session creation and
    again at the quote, once the buyer country is known. A blocked line is
    a typed error (`line_restricted_for_country`), not a manual review.
  - **What was sold is snapshotted** on the session and the order, because
    the invoice and the customs declaration need it. With a
    `merchant_sku`, an HS code we suggested once is reused for later
    sessions with that SKU.
  - **Inline or by reference** (decided 2026-11-04). Each line is either
    full inline data or a `product_id`. A stored `Product` is the same
    data under an id: it saves resending the customs data and gives one
    place to correct it. Both resolve to a `SoldLine` snapshot at session
    creation, so a later product edit never changes what was sold.
  - **Payment links need no product** (decided 2026-11-04). A link is a
    saved checkout template with its own lines, inline or by reference.
    Lines by reference follow product edits (a new price applies without a
    new URL). Inline lines are frozen until the link is edited.
- **Price currency:** whatever currency the merchant sends (decided
  2026-11-04). If the buyer pays in another currency, the quote converts
  with a stored exact rate.
- **Duty estimate differences are the merchant's** (decided 2026-11-04).
  When the courier's duty bill differs from the duty in the quote, the
  difference is booked against `merchant_payable` and settles with the
  next payout. It is symmetric: an overestimate is credited to the
  merchant too. Each case is a ledger entry that references the
  shipment and the courier invoice line, so the statement can show it.
- **Tax behaviour per line:** with `exclusive` (the default, decided
  2026-11-04) we add tax, duties and shipping on top, so the merchant's
  amount is predictable.
  With `inclusive` the buyer total stays fixed and we back tax and duties
  out, so the merchant's net varies by country. The back-out rounding mode
  is named and the remainder booked (AGENTS.md).
- **Quotes are immutable and expire** (e.g. after 30 minutes). The buyer
  pays exactly a stored quote. The tax rule, duty rule, FX rate and their
  versions are stored with it, so the invoice can be explained years later
  (AGENTS.md, tax rule).

## 4. Merchant API

- **Style:** JSON over HTTPS. Resource URLs under `/v1/`. Within v1, changes
  are additive only, and a breaking change means `/v2`, run side by side.
  (Stripe's dated versions are more flexible but cost more to run; we can
  move to them later.)
- **Auth:** secret API keys per merchant (`sk_test_…` / `sk_live_…`), stored
  hashed, shown once. Keys can be restricted to scopes (e.g. "products
  only"), and rotation keeps the old key valid for a short overlap.
- **Test mode:** test keys hit the same API with separate data, the mock
  PSP and a mock courier, so a merchant can run a whole order (pay → label →
  delivered → payout statement) without real money or parcels.
- **Idempotency:** an `Idempotency-Key` header is required on every POST.
  It is stored per merchant with a unique constraint and replayed for 7
  days. The same key with different parameters returns 409.
- **Money:** always `{ "amount_minor": 129000, "currency": "VND" }`, using
  the exponents from our table.
- **Errors:** one shape, `{ "error": { "type", "code", "message", "param" } }`,
  with a closed set of documented codes (`product_not_approved`,
  `quote_expired`, `ship_to_not_supported` …).
- **Lists:** cursor pagination (`starting_after`, `limit`), never offsets.
- **Resources (v1):**
  - `products` (optional, reusable line data): create, update, list,
    deactivate.
  - `payment_links`: create (inline lines or product references), update,
    deactivate.
  - `estimates`: landed price for given lines and a country, no session.
  - `checkout_sessions`: create, get, expire. A merchant may pass a known
    buyer email or ship-to; we still quote.
  - `orders`: get, list. `orders/{id}/shipments`: book a label, get it.
  - `refunds`: create (full or per line), get.
  - `disputes`: get, submit evidence.
  - `balance`, `payouts`, `statements` (the per-payout document a VN
    merchant needs for its own tax treatment).
  - `webhook_endpoints`: create, rotate secret, list deliveries, resend.

Example: create a checkout session.

```
POST /v1/checkout_sessions
Idempotency-Key: 6f1c…
{
  "lines": [{
    "name": "Ceramic coffee dripper",
    "quantity": 2,
    "unit_price": { "amount_minor": 1800, "currency": "USD" },
    "tax_behavior": "exclusive",
    "merchant_sku": "DRIP-01",
    "customs": { "description": "Ceramic coffee filter holder",
                 "origin_country": "VN", "weight_g": 420 }
  }],
  "merchant_reference": "order-1042",
  "return_url": "https://shop.example.vn/thanks",
  "cancel_url": "https://shop.example.vn/cart"
}
→ 201 { "id": "cs_31Pq", "state": "open", "url": "https://checkout.<ours>/c/cs_31Pq",
        "expires_at": "2026-11-04T10:30:00Z" }
```

The buyer's country is not known yet, so there is no total in that
response. The total comes from the quote once the buyer enters an address.
That surprises merchants who expect a final price up front, so an
`estimates` endpoint (`POST /v1/estimates`, same lines plus a country)
returns the landed price for showing on the merchant's own product pages.

- **Webhooks to merchants:** past-tense facts with a schema version:
  `checkout_completed`, `order_paid`, `shipment_label_created`,
  `shipment_delivered`, `refund_succeeded`, `dispute_opened`,
  `payout_paid`. They are signed with HMAC-SHA256 over
  `timestamp.event_id.body`, retried with backoff for 3 days, and
  resendable. Ordering is guaranteed per order only.

## 5. Hosted checkout and buyer pages

### Flow

```
/c/{session}            cart summary (lines, merchant name and logo)
   → address            country first; the address form adapts per country; phone with country code
   → quote              goods + tax + duties + shipping, all-in total in the buyer's currency, expiry shown
   → method             only methods that routing and capabilities allow for this country and amount
   → pay                PSP component (JS) or a redirect to the PSP's hosted page; QR shown as an image
   → /o/{order}         confirmation, invoice in our name, tracking, return request
```

- **Branding: the merchant's, ours nearly invisible** (decided
  2026-11-04: our brand confuses buyers). The merchant's name, logo and
  accent colour carry every buyer page. Ours appears only where law or the
  card networks require it:
  1. one small line at checkout and in the terms, "Sold and invoiced by
     <us> on behalf of <merchant>" (exact wording per country needs legal
     input);
  2. the invoice, in our name with the merchant named on it;
  3. the card statement descriptor, combined as `OURS*MERCHANTNAME`, so the
     buyer recognises the charge. Unrecognised descriptors cause "I don't
     recognise this" chargebacks, which we pay for. Dynamic descriptor
     support is a PSP selection criterion.

  Later: checkout on the merchant's own domain (`checkout.merchant.com`
  via a CNAME).
- **Prices shown all-in.** Consumers in the target countries expect prices
  that include tax, and with DDP the duty is in the total too. The quote step
  shows the breakdown, so nothing is due at the door.
- **Currency:** the buyer pays in their own currency. The merchant prices in
  the currency it sends per call (§3). The FX rate is stored with the quote as an
  exact fraction, and our FX margin, if any, is visible in the ledger.
- **Language:** English plus the buyers' languages (Thai, Bahasa
  Indonesia, Malay, Vietnamese for merchant-facing text; Filipino and Chinese
  later). Text is a typed table of messages per language, never string
  formatting of markup.
- **Mobile first.** Most SEA buyers use phones, often mid-range Android
  devices on mobile data. Server-rendered pages, little JS and small images
  suit that.
- **QR and wallet methods:** the QR is rendered on the server. The page
  refreshes itself with `<meta http-equiv="refresh">` until the webhook has
  settled the state, and it shows the expiry time. App-switch methods
  redirect to the app and come back to `/o/{order}`.
- **Sessions:** a session URL is a bearer link for that cart only. A
  completed session goes to the order page, and an expired one to the
  merchant's `cancel_url`.
- **Order page:** the buyer gets a magic link by email (no account). It
  shows tracking from courier events, the invoice download, and a return or
  refund request inside the policy window.
- **Buyer support is split by topic** (decided 2026-11-04). The
  **merchant** answers product, shipping, delivery and return questions,
  under our policy for response times and refunds. **We** answer charge,
  invoice, tax and payment questions, and own disputes and chargebacks.
  Legally we are the seller, so buyers must be able to reach us. One
  contact route on every buyer page and on the invoice sends each question
  to the right side by topic.
- **Return policy is a merchant setting** (decided 2026-11-04: it depends
  on what the seller wants). It can be set per buyer country:
  ```
  ReturnPolicy { window_days, destination, conditions }
    destination: return_to_origin (VN) | local_address { address } | refund_without_return
  ```
  The window can't be shorter than each buyer country's consumer-law
  minimum; the system enforces that floor from the per-country rules.
  `refund_without_return` suits cheap items whose return shipping costs
  more than the goods.
- **Returns:** the merchant pays return shipping (decided 2026-11-04). It is
  deducted from the next payout. If the balance cannot cover it, the
  reserve does, or the balance goes negative and the merchant is invoiced.
  Where consumer law forbids charging the buyer (e.g. defective goods),
  the buyer is not charged; it is still the merchant's cost.
- **Shipping labels:** in v1, cross-border shipments use labels booked
  through our courier partners (decided 2026-11-04). Only then do we get
  DDP duty billing, our SG/MY tax registration numbers on the declaration
  (otherwise buyers can be taxed twice), and delivery events for payout
  timing. Later, an "own courier" mode for routes where the merchant's
  courier can do DDP with our numbers: the merchant enters courier and
  tracking number, and tracking comes from an aggregator (AfterShip,
  17TRACK or similar) instead of one integration per courier.

## 6. Shopify and other platforms

What `plan-psp-integration.md` §7 found: Shopify payments apps are
invitation-only, may use only the Payments Apps API, and receive a total
that Shopify already computed. On a Shopify order the merchant is normally
the seller.

For physical goods from VN, that leaves:

- **A payments app:** we would process payments, but tax and duties come
  from Shopify, and the order says the merchant sold. That conflicts with
  being merchant of record.
- **A catalog and order sync app with our checkout:** it avoids that
  conflict but may break Shopify's checkout rules. To be asked.
- **A partnership** like Global-e's.

Proposal: **no Shopify work in v1.** Payment links and the API cover the
first merchants. Ask the first 10–20 prospective merchants where they sell
today. Shopee, Lazada and TikTok Shop are closed marketplaces we can't plug
into. If many use Shopify, have the Shopify call (questions in the PSP plan,
§7). If many use an open platform (WooCommerce, or VN platforms such as
Haravan and Sapo, to be verified), a redirect plugin there is cheap and
needs nobody's approval. Whatever we build, a plugin only creates checkout
sessions through our public API and reacts to our webhooks. It gets no
private shortcut, so the API stays the single integration.

## 7. Merchant dashboard

Server-rendered pages for each record above, in Vietnamese and English:

- **Onboarding and KYB:** company details, documents, beneficial owners,
  the payout bank account, and the review state. Nothing can be sold before
  approval. The exact KYB requirements are a compliance input (§8).
- **Products (optional):** saved line data with the customs fields and a
  suggested HS code to confirm.
- **Payment links:** create a link with inline lines or saved products,
  then copy it, print it as a QR code, or switch it on or off. The page
  shows an estimated landed price per buyer country.
- **Orders:** list and detail with the price breakdown, the buyer's
  address (only what fulfilment needs), shipment booking, label printing
  (PDF), pickup scheduling and tracking.
- **Refunds, returns, disputes:** approve return requests, issue refunds,
  submit dispute evidence.
- **Money:** balance (pending, available, reserve), payouts and the
  per-payout statement documents.
- **Developers:** API keys, webhook endpoints with delivery logs and resend,
  and a test/live toggle.
- **Team:** users and roles. 2FA is required, and every change is recorded
  with who made it and when (AGENTS.md audit rule).

## 8. Compliance touchpoints (named, not answered)

- KYB for VN companies: documents, sanctions screening, and who reviews.
- Restricted and prohibited goods per buyer country: the automatic
  screening rules, who maintains them, and how we sample-check what passes
  (an automatic check alone will miss some lines).
- Consumer law per buyer country: cooling-off and return rights, and the
  information a seller must show before purchase.
- Buyer PII in the dashboard: merchants see only what fulfilment needs.
  Retention and residency rules apply.
- Invoice content per country for B2C sales by a foreign seller, e.g.
  under SG's OVR or MY's LVG rules.

## 9. Open questions

Answered 2026-11-04: the merchant sends the price and currency per call;
there is no required catalog; lines are inline or by product reference;
payment links need no product; `exclusive` is the default; duty estimate
differences are the merchant's, both ways (§3). Every channel is a thin API
client (§2). Our brand is nearly invisible; buyer support is split by
topic; return policy is a merchant setting; the merchant pays return
shipping; cross-border labels go through our courier partners in v1 (§5).

None of the remaining items block the design:

1. Which channel after payment links and the API: decided by where the
   first merchants sell, once known.
2. Consumer-law minimums per buyer country (return window, required
   pre-purchase information, seller statement wording): legal input.

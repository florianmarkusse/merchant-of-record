# Merchant and buyer surfaces

Status: input for what is not built: payment links and products, the
rest of the merchant API (refunds, disputes, balance, payouts, webhook
endpoints), returns on the order page, the dashboard, and other channels.
What is built is described where it was built: checkout sessions, the API
conventions, estimates and the buyer's checkout pages in `plan-quote.md`
§5; the pay step, the order page and the orders API in `plan-payments.md`
§4 and §6.

## 1. The surfaces

"Our frontend for merchants" is really three groups. Because we are the
merchant of record, the buyer is **our** customer, so the buyer-facing
pages are ours too, not the merchant's.

| Group | Surface | Used by | State |
|---|---|---|---|
| Merchant | **Dashboard** (server-rendered HTML) | merchant staff | not built (§7) |
| Merchant | **Merchant API** (JSON) + webhooks | merchant developers | sessions, estimates, orders and settings built; the rest §4 |
| Buyer | **Hosted checkout** | buyers | built |
| Buyer | **Order page**: status, invoice, tracking, return request | buyers | status and invoice built; tracking and returns with their slices |
| Buyer | **Payment link pages** (a product page that leads to checkout) | buyers | not built (§3) |
| Channel | **Shopify adapter** | Shopify merchants | later (§6) |
| Channel | Other platform plugins (WooCommerce, Haravan, Sapo …) | merchants on them | later, from demand (§6) |

All HTML is server-rendered; JavaScript appears only where a PSP
component needs it, on the payment step.

## 2. Ways a merchant can sell through us

Ordered by how much work it is for the merchant. **Every channel is a thin
client of the same public API** (decided 04-Nov-2026): it creates a
checkout session, sends the buyer to the same hosted checkout and reacts
to the same webhooks. Tax, landed cost, invoicing and the seller identity
therefore always come from the core. A new channel is a small adapter,
not a change to the core, and which one we build next is only a question
of demand. Shopify is the exception, because its own checkout computes
tax and owns the order (§6).

1. **Payment links (no code).** The merchant creates a link in the
   dashboard and shares it in Facebook, Instagram, TikTok, Zalo or chat.
   Social selling is common among SEA and VN sellers, so this may be the
   fastest way to the first merchants (to check with them, §9).
2. **Hosted checkout via the API** (built). The merchant's own site
   creates a checkout session, redirects the buyer to us and reads the
   order through the API. The same shape as Stripe Checkout or Paddle.
3. **Platform plugins.** Shopify or another platform, which call option 2
   underneath.
4. **Embedded checkout** inside the merchant's site. Not in v1 (decided
   04-Nov-2026: v1 is API sessions plus payment links). Later it would be
   our own address, quote and payment steps in an iframe, so the price
   build-up and the seller line still come from us. It needs JavaScript
   and a strict `frame-ancestors` policy per merchant domain.

Options 1 and 2 differ only in how the buyer reaches the hosted checkout.
With the API, the merchant's server creates one session per buyer from
its cart. With a payment link, the link is created once and shared, and
each buyer who opens it gets a fresh session from the link's template.
From the checkout page onwards both paths are identical.

## 3. Products and payment links

```
Product      { id, merchant_id, ItemData, active }  # optional: line data saved under an id
LineInput    = { product_id, quantity } | { ItemData, quantity }   # what the API accepts
PaymentLink  { id, merchant_id, lines: [LineInput], quantity_rule, active,
               after_payment }   # a saved checkout template; opening it makes a session
```

`ItemData` is what a session line carries today (name, unit price,
`merchant_sku`, customs description, origin country, weight, HS code).

- **No required catalog** (decided 04-Nov-2026: a mandatory catalog is
  too restrictive). The merchant's server sends the lines with their
  price and customs data on each checkout, like Stripe's inline
  `price_data`; this is how sessions work today. The price is trusted
  because a secret-key call creates the session; prices never come from
  the browser.
- **Inline or by reference** (decided 04-Nov-2026). Each line is either
  full inline data or a `product_id`. A stored `Product` is the same data
  under an id: it saves resending the customs data and gives one place to
  correct it. Both resolve to the session's line snapshot at creation, so
  a later product edit never changes what was sold.
- **Payment links need no product** (decided 04-Nov-2026). A link is a
  saved checkout template with its own lines, inline or by reference.
  Lines by reference follow product edits (a new price applies without a
  new URL). Inline lines are frozen until the link is edited.
- **An HS code suggested once is reused**: with a `merchant_sku`, a code
  we suggested and the merchant confirmed applies to later sessions with
  that SKU.
- **Restricted-goods screening is automatic** at session creation and
  again at the quote, once the buyer country is known; a blocked line is
  a typed refusal, not a manual review.

## 4. The rest of the merchant API

The conventions are built (`plan-quote.md` §5): JSON under `/v1/`,
additive changes only within v1; secret keys per merchant and
environment (`sk_{environment}_…`), stored hashed, shown once; a required
`Idempotency-Key` on every POST, kept for good; money as
`{ "amount_minor", "currency" }`; one error shape with a closed set of
codes; cursor pagination. Test mode is the separate sandbox deployment,
never test keys on prod (`plan-architecture.md` §6).

Still to build:

- `products`: create, update, list, deactivate.
- `payment_links`: create (inline lines or product references), update,
  deactivate.
- `orders/{id}/shipments`: book a label, get it (the shipment slice).
- `refunds`: create (full or per line), get (slice 8).
- `disputes`: get, submit evidence (slice 8).
- `balance`, `payouts`, `statements` (the per-payout document a VN
  merchant needs for its own tax treatment; slice 9).
- `webhook_endpoints`: create, rotate secret, list deliveries, resend.
  Webhooks are past-tense facts with a schema version
  (`checkout_completed`, `order_paid`, `shipment_label_created`,
  `shipment_delivered`, `refund_succeeded`, `dispute_opened`,
  `payout_paid`), in any order and possibly twice
  (`plan-psp-integration.md` §6).
- Key scopes (a key for "products only") and rotation with a short
  overlap.

## 5. Buyer pages: what is decided for what comes next

- **Branding: the merchant's, ours nearly invisible** (decided
  04-Nov-2026: our brand confuses buyers). The merchant's name, logo and
  accent colour carry every buyer page. Ours appears only where law or the
  card networks require it:
  1. one small line at checkout and in the terms, "Sold and invoiced by
     <us> on behalf of <merchant>" (exact wording per country needs legal
     input);
  2. the invoice, in our name with the merchant named on it;
  3. the card statement descriptor, combined as `OURS*MERCHANTNAME`, so
     the buyer recognises the charge. Unrecognised descriptors cause "I
     don't recognise this" chargebacks, which we pay for. Dynamic
     descriptor support is a PSP selection criterion.

  Later: checkout on the merchant's own domain (`checkout.merchant.com`
  via a CNAME).
- **Language**: English plus the buyers' languages (Thai, Bahasa
  Indonesia, Malay; Vietnamese for merchant-facing text; Filipino and
  Chinese later). Text is a typed table of messages per language, never
  string formatting of markup.
- **Mobile first.** Most SEA buyers use phones, often mid-range Android
  devices on mobile data. Server-rendered pages, little JS and small
  images suit that.
- **Buyer support is split by topic** (decided 04-Nov-2026). The
  **merchant** answers product, shipping, delivery and return questions,
  under our policy for response times and refunds. **We** answer charge,
  invoice, tax and payment questions, and own disputes and chargebacks.
  Legally we are the seller, so buyers must be able to reach us. One
  contact route on every buyer page and on the invoice sends each
  question to the right side by topic.
- **Return policy is a merchant setting** (decided 04-Nov-2026: it depends
  on what the seller wants), per buyer country:
  ```
  ReturnPolicy { window_days, destination, conditions }
    destination: return_to_origin (VN) | local_address { address } | refund_without_return
  ```
  The window can't be shorter than each buyer country's consumer-law
  minimum; the system enforces that floor from the per-country rules.
  `refund_without_return` suits cheap items whose return shipping costs
  more than the goods.
- **The merchant pays return shipping** (decided 04-Nov-2026), deducted
  from the next payout. If the balance cannot cover it, the reserve does,
  or the balance goes negative and the merchant is invoiced. Where
  consumer law forbids charging the buyer (defective goods), the buyer is
  not charged; it is still the merchant's cost.
- **Cross-border labels go through our courier partners in v1** (decided
  04-Nov-2026). Only then do we get DDP duty billing, our SG/MY tax
  registration numbers on the declaration (otherwise buyers can be taxed
  twice), and delivery events for payout timing. Later, an "own courier"
  mode for routes where the merchant's courier can do DDP with our
  numbers: the merchant enters courier and tracking number, and tracking
  comes from an aggregator (AfterShip, 17TRACK or similar).
- **The order page** gains tracking from courier events and a return or
  refund request inside the policy window, with their slices; the buyer
  gets its link by email with the `notify` slice.

## 6. Shopify and other platforms

What `plan-psp-integration.md` §8 found: Shopify payments apps are
invitation-only, may use only the Payments Apps API, and receive a total
that Shopify already computed. On a Shopify order the merchant is
normally the seller.

For physical goods from VN, that leaves:

- **A payments app:** we would process payments, but tax and duties come
  from Shopify, and the order says the merchant sold. That conflicts with
  being merchant of record.
- **A catalog and order sync app with our checkout:** it avoids that
  conflict but may break Shopify's checkout rules. To be asked.
- **A partnership** like Global-e's.

Proposal: **no Shopify work in v1.** Payment links and the API cover the
first merchants. Ask the first 10–20 prospective merchants where they
sell today. Shopee, Lazada and TikTok Shop are closed marketplaces we
can't plug into. If many use Shopify, have the Shopify call (questions in
`plan-psp-integration.md` §8). If many use an open platform (WooCommerce,
or VN platforms such as Haravan and Sapo, to be verified), a redirect
plugin there is cheap and needs nobody's approval. Whatever we build, a
plugin only creates checkout sessions through our public API and reacts
to our webhooks: no private shortcut, so the API stays the single
integration.

## 7. Merchant dashboard

Server-rendered pages, in Vietnamese and English, behind merchant
sign-in (users with roles `owner | admin | fulfilment | developer |
viewer`, 2FA required because this is money, every change recorded with
who and when). It calls the same code as the API.

- **Onboarding and KYB:** company details, documents, beneficial owners,
  the payout bank account, and the review state (today's `kyb_state` is
  set by us, `plan-payments.md` §6). Nothing can be sold before approval.
  The exact KYB requirements are a compliance input (§8).
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
- **Money:** balance (pending, available, reserve), payouts, the
  per-payout statement documents, and each deduction with its shipment
  and invoice line (`plan-architecture.md` §9).
- **Developers:** API keys, webhook endpoints with delivery logs and
  resend.
- **Settings:** support email (today through the API), branding, return
  policy.

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

None block the design:

1. Which channel after payment links and the API: decided by where the
   first merchants sell, once known.
2. Consumer-law minimums per buyer country (return window, required
   pre-purchase information, seller statement wording): legal input.

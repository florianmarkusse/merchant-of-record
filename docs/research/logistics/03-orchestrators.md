# 03 — Multi-carrier orchestrators, aggregators and tracking platforms (VN origin)

Researched 2026-11-06. Source-type labels: [published] = vendor's own page/docs,
[3rd-party] = review/comparison site (weaker), [calculator] = public rate tool,
[estimate] = my arithmetic from published numbers, UNVERIFIED = could not confirm.
Several vendor pages (support.easyship.com, aftership.com/docs) returned HTTP 403
to automated fetches; claims from them come via search snippets and are marked.

## TL;DR

1. **No Western orchestrator sells pre-negotiated rates from Vietnam.** Easyship
   lists VN as an origin where you "need to link your own courier"; its VN courier
   directory says "Coming soon" and its VN->MY calculator says "We don't offer rates
   from Vietnam, yet" [published]. EasyPost's DHL Express wallet account is US-export
   only [published]; Shippo's discounted DHL is US-only, other origins need your own
   DHL account [published]. ShipStation API (ShipEngine) BYO requires a paid plan
   [published].
2. So for VN origin, an orchestrator is **only a software layer over carrier
   contracts we must sign ourselves anyway** (DHL Express / FedEx / UPS Vietnam
   export accounts). It does not give us rates, coverage or DDP that the carrier
   contract would not.
3. **The SEA regional cross-border carriers (Ninja Van, J&T, Flash, LEX, SingPost,
   Pos, LBC…) are not bookable from VN through EasyPost/Shippo/ShipEngine/Easyship.**
   VN/SEA-native aggregators (Boxme Global, Janio) are logistics operators with
   contract pricing and API-on-request, not self-serve software.
4. **Recommendation:** integrate DHL Express (and maybe FedEx/UPS) directly, plus
   one SEA cross-border operator (Boxme/Janio/Ninja Van, see other slices) directly.
   Do not put an orchestrator in the critical path at 500/month. Use a tracking
   aggregator (AfterShip Tracking or 17TRACK) only if we end up with many carriers.
   Re-evaluate Easyship when/if VN origin goes live there (its Tax & Duty API and
   DDP flow are the strongest of the group).

## Provider-by-provider

### Easyship (HK/SG roots)
- **VN origin:** VN is in the "can create an account, but link your own courier to
  generate labels" group; pre-negotiated origins are AU, BE, CA, FR, DE, HK, NL, NZ,
  SG, US, UK [published via search snippet, https://support.easyship.com/hc/en-us/articles/360000487172 — page 403s].
  VN courier directory: "Coming soon" [published, https://www.easyship.com/en-vn/couriers, fetched 2026-11-06].
  Linkable own accounts: DHL Express, FedEx, UPS [published, https://www.easyship.com/de-de/feature-list];
  whether linking works for a VN-registered account is UNVERIFIED (one forum user
  reports own FedEx account not appearing on some lanes [3rd-party, developers.easyship.com forum]).
- **Notable:** if we had an SG entity, SG origin has pre-negotiated rates — irrelevant
  unless goods are consolidated/forwarded into SG first (a separate design).
- **API:** REST, versioned (e.g. 2023-01, 2024-09), free sandbox without credit card
  [published, https://www.easyship.com/blog/easyships-upgraded-global-shipping-api-for-ecommerce].
  Webhooks signed: `X-EASYSHIP-SIGNATURE` is an HS256 JWT verified with the webhook
  secret (`webh_…`), retries 30x at 1-minute intervals [published, https://developers.easyship.com/reference/webhooks-guide].
  Labels PDF/PNG/ZPL (UNVERIFIED per carrier).
- **DDP/tax:** Tax & Duties API (landed cost, 220+ destinations), DDP or DDU at
  checkout [published, same blog]. Callable stand-alone — could serve as our duty
  calculator even if we don't book labels through Easyship.
- **Pricing (from Jan 2026)** [published, same blog]: Free (pay per call, no BYO);
  Plus from $29/mo, Premier from $69, Scale 1/2/3 $99/$149/$199, Enterprise custom.
  Rates calls: Free $0.01; Plus 2,000 incl. then $0.008; Premier 10,000 incl. then
  $0.007; Scale 20k/30k/40k incl. then $0.006. Tax & Duty $0.11/$0.11/$0.10/$0.09.
  HS code $0.05–0.03. **Link Your Own Courier label fee $0.05 / $0.04 / $0.03**
  (Plus/Premier/Scale). Annual billing −20%.
- **Multi-tenant:** sub-accounts per merchant UNVERIFIED; for an MoR we would ship
  everything under our own account anyway.
- **Returns/insurance:** returns labels and insurance exist for supported origins;
  for VN BYO lanes UNVERIFIED.

### EasyPost (US)
- **VN origin:** only via BYO. DHL Express "Wallet" (EasyPost's own) account is US
  exports only; with your own DHL Express account the Account Country Code is the
  export origin [published, https://support.easypost.com/hc/en-us/articles/4407272347661-DHL-Express-Wallet-Guide,
  https://docs.easypost.com/carriers/dhl-express-guide]. FedEx/UPS similar (own
  account). No SEA regional carriers found for VN origin.
- **API:** best-in-class REST docs, test mode keys, child users for platforms
  (UNVERIFIED for non-US), webhooks HMAC-SHA256 in `X-Hmac-Signature`, newer v2
  scheme with `x-hmac-signature-v2` + `x-timestamp` + `x-path`, 60 s tolerance
  [published, https://docs.easypost.com/guides/webhooks-guide, https://support.easypost.com/hc/en-us/articles/39826034964237-Webhook-HMAC-Validation].
  Labels PDF/PNG/ZPL/EPL2 [published, general knowledge of EasyPost API; per-carrier UNVERIFIED].
- **Pricing (since Feb 2026):** Wallet plan 3,000 labels/mo free then $0.08; BYOCA
  plan **$20/mo + $0.08 per label** [published, https://support.easypost.com/hc/en-us/articles/39984592062605-EasyPost-Wallet-and-Bring-Your-Own-Carrier-Account-Plans].
  Third parties disagree whether BYOCA has a free allowance [3rd-party, https://1teamsoftware.com/2026/02/09/easypost-new-pricing-plans-2026/].
  Enterprise custom.
- **Entity:** US company; signs up SG/NL entities fine (UNVERIFIED, no restriction found).

### Shippo (US)
- **VN origin:** DHL Express/FedEx/UPS listed "Global" but only with your own account;
  DHL own accounts must be whitelisted by your DHL account manager. No Asian-origin
  carriers listed [published, https://docs.goshippo.com/carriers/carrier-capabilities.md].
- **API:** REST, test tokens, Platform API with managed accounts for multi-tenant.
  Webhook HMAC (`Shippo-Auth-Signature`, timestamp + v1) is **not self-serve**:
  requested via account manager, up to 10 business days; otherwise a URL token
  [published, https://docs.goshippo.com/docs/tracking/webhooksecurity; 3rd-party, hookdeck.com].
- **Pricing:** $0.05–$0.07 per label PAYG, Pro ~$19/mo, overage $0.08; live rate
  calls $0.01; external tracking $0.02 [3rd-party, conflicting, https://revaddress.pages.dev/blog/shipping-api-comparison-2026/] UNVERIFIED.
- **Fit:** weak — US-centric, nothing VN/SEA-specific.

### ShipStation API (formerly ShipEngine; Auctane)
- **VN origin:** BYO DHL Express etc. Its DHL Express guide names US/CA/UK/AU origins;
  Asia origins UNVERIFIED [published, https://docs.shipengine.com/docs/carriers/dhl-express-guide/].
  Account country "does not impact your ability to ship to or from any other country"
  [published, https://docs.shipengine.com/docs/account-country/].
- **API:** REST, sandbox keys, Partner API for sub-accounts, webhooks signed with
  **RSA-SHA256**, JWKS at https://api.shipengine.com/jwks, timestamp header for replay
  protection [published, https://docs.shipstation.com/apis/docs/guides/webhooks-validation].
  Cleanest webhook security of the group.
- **Pricing** [published, https://www.shipstation.com/shipping-api/pricing/]: Free
  (no BYO); Advanced $75/mo (1,000 labels, $0.075 overage), $325 (5,000, $0.065),
  $600 (10,000, $0.060); Enterprise custom (25k+).

### AfterShip Shipping (formerly Postmen)
- **VN origin:** 100+ carriers incl. DHL Express (PDF/ZPL) [published via snippet,
  https://www.aftership.com/docs/shipping/carrier-guide-supported-couriers — 403];
  VN-origin label creation not confirmed. Its Easyship-backed rates have no VN
  ship-from [published via snippet, https://shipping-helpcenter.aftership.com/en/article/aftership-shipping-and-easyship-integration-1gl0vvt].
  So again BYO DHL/FedEx/UPS. UNVERIFIED whether any VN/SEA local carrier supports
  label creation (AfterShip *tracks* VNPost, Viettel Post, GHN, J&T VN, Ninja Van).
- **Pricing:** Free (10 labels), Essentials ~$9, Pro ~$69 (API), Enterprise
  [3rd-party/snippet, https://aftership.com/pricing/shipping] UNVERIFIED.
- **Webhooks:** HMAC-SHA256 base64 `aftership-hmac-sha256` (tracking product) [3rd-party, hookdeck.com].
- **Fit:** reasonable if we also buy AfterShip Tracking + Returns; HK/SG-based support.

### EasyParcel (MY; also SG, TH, ID, PH)
- Consumer/SME booking portal and API; international booking supports DDP "pay by
  sender" and a duty estimator [published, https://helpcentre-my.easyparcel.com/support/solutions/articles/9000188598].
- **VN origin:** no VN market found; its VN pages are MY->VN ("Ship to Vietnam from
  RM93.16") [published, https://blog.easyparcel.com/my/?p=117615]. API is mainly
  domestic; a 2025 Shopify reviewer says international doesn't work in the app
  [3rd-party]. **Not usable for VN origin.** Possibly useful later for MY/SG-origin
  merchants or domestic return legs.

### Biteship (ID) and Shipper.id (ID)
- Indonesian domestic aggregators. Biteship API: rates from IDR 5/request, orders
  IDR 5, tracking IDR 10 per request [published, https://biteship.com/en/harga];
  "rates applied for international" but origin is Indonesia. Shipper has a
  "Create Order International V2" endpoint (doc URL now 404) — ID origin.
- **Not usable for VN origin.** Relevant only for ID-side domestic return pickups
  or a future ID-origin merchant.

### Sendcloud (NL) and nShift (NO/SE)
- Sendcloud: origins limited to AT, BE, FR, DE, IT, NL, ES, UK [published via snippet,
  https://support.sendcloud.com/hc/en-us/articles/36317556679569]. nShift: Nordic/EU,
  contract-based; carriers priced "based on your agreement with the carrier" [published,
  https://help.unifaun.com/uo-se/en/integrations/price-via-api.html].
- **Not usable for VN origin.** Only relevant if a Dutch BV later ships from an EU
  warehouse.

### Parcel Monkey / Sendle
- Parcel Monkey: UK/US consumer reseller; Sendle: AU/US/CA domestic. **Not relevant.**

### Boxme Global (VN-based, SEA cross-border fulfillment)
- VN-headquartered cross-border fulfillment/logistics; claims connections to 50+
  carriers and 7–10 day cross-docked international delivery [published but 2018,
  https://boxme.asia/2018/05]. **DDP "may be available upon agreement"**, discounted
  rates by contract/volume [published, https://boxme.asia/?p=104443].
- **API:** developer portal exists (orders: Create B2C order, Get Shipping Option,
  Cancel; webhook examples use `country: VN`) but docs seen are thin; base
  `https://s.boxme.asia`; no documented sandbox or webhook signing [published,
  https://developers.boxme.asia/api-7188116]. Webhook auth = optional static token.
- **Fit:** the most genuinely VN-origin option in this slice, but it is a logistics
  operator (quote, contract, maybe warehousing), not neutral software. Treat as a
  carrier module, not an orchestrator. Needs a sales call: DDP lanes VN->SG/MY/TH/ID/PH,
  duty billing to a third party (us), returns.

### Janio (SG 4PL)
- SG-HQ cross-border e-commerce 4PL over ~300 partners in ID, MY, PH, TH, VN [3rd-party,
  https://bitscale.ai/directory/janio-asia]. REST API at api.janio.asia (orders,
  rates, labels, tracking), docs on request [3rd-party, https://apis.io/providers/janio/].
  DDP listed as a service; **VN-origin outbound UNVERIFIED** (sources mostly show VN
  as destination). Worth a sales question; same "carrier module" treatment as Boxme.

### ShipChung, Ahamove and other VN domestic players
- ShipChung: VN domestic e-commerce gateway (Peacesoft/NextTech), no current
  cross-border product found [3rd-party, bizhub.vietnamnews.vn]. Ahamove: same-day
  domestic. **Not relevant** to cross-border, except maybe first-mile pickup to a
  consolidator hub.

### Tracking-only: AfterShip Tracking, 17TRACK, Parcel Perform
| | AfterShip Tracking | 17TRACK API | Parcel Perform |
|---|---|---|---|
| Carriers | 1,100+ incl. VNPost, Viettel, GHN, J&T VN, Ninja Van, SPX [published, carrier pages] | 2,000+ (claimed) | 1,000+ (claimed) |
| Pricing | Essentials ~$11/100 shipments; API/webhooks on higher tiers ($59–$119 reported) [3rd-party, conflicting] UNVERIFIED | Quota, annual: 10k CNY 299, 50k CNY 1,299, 500k CNY 7,299, 1M CNY 9,999 [3rd-party mirror, https://raw.githubusercontent.com/api-evangelist/17track/refs/heads/main/plans/plans.yml] UNVERIFIED | Enterprise custom, ~2 month implementation [3rd-party, g2.com] |
| Webhook signing | HMAC-SHA256 base64 header | signed push (`sign` = SHA256 of body+key, UNVERIFIED) | UNVERIFIED |
| Fit | Good mid-tier; also Returns product | Cheapest per number (≈CNY 0.03/number ≈ $0.004 at 10k [estimate]) | Overkill before ~50k/mo; SG company |

Tracking normalization is the one thing we will need for every carrier; a tracking
aggregator lets us skip writing per-carrier tracking webhooks for long-tail carriers.
But our main carriers (DHL Express, a SEA cross-border operator) push their own
webhooks, and we need raw events for duty/exception handling anyway.

## Which orchestrators genuinely support VN origin?

| Provider | VN origin labels | Carriers from VN | Own rates? | DDP | Duty calc |
|---|---|---|---|---|---|
| Easyship | BYO only ("link your own"), "coming soon" for own rates | DHL Express/FedEx/UPS (own acct) | No | Yes (where supported) | Yes, stand-alone API |
| EasyPost | BYO only | DHL Express/FedEx/UPS (own acct) | No | Carrier DDP flag (DHL `duty_payment`) | No |
| ShipStation API | BYO only, paid plan | DHL Express etc. (own acct) | No | Carrier option | No |
| Shippo | BYO only, DHL whitelisting | DHL/FedEx/UPS (own acct) | No | Carrier option | No |
| AfterShip Shipping | BYO only (UNVERIFIED) | DHL Express etc. | No | Carrier option | No |
| Boxme Global | Yes (operator) | Own network + partners | Contract | By agreement | UNVERIFIED |
| Janio | UNVERIFIED | Own network | Contract | Listed | UNVERIFIED |
| EasyParcel, Biteship, Shipper, Sendcloud, nShift, Sendle | **No** | — | — | — | — |

**Ranking for our use case (VN origin, SEA-first, DDP, MoR):**
1. **Boxme Global** — only one that actually moves VN-origin cross-border parcels as
   a business; contract + weak API. (Carrier module, not orchestrator.)
2. **Easyship** — best DDP/landed-cost tooling and signed webhooks; today BYO-only
   from VN, so its value is the Tax & Duty API and future VN rates. Watch.
3. **ShipStation API** — best webhook security (RSA/JWKS), clear pricing, sub-accounts;
   but adds nothing on VN lanes beyond what DHL's own API gives.
4. **EasyPost** — excellent API, BYOCA $20 + $0.08/label; same caveat.
5. **AfterShip Shipping** — fine if bundled with AfterShip Tracking/Returns.
6. **Shippo** — US-centric, HMAC by request only.
(Janio: unranked until VN-origin is confirmed.)

## Cost model: orchestrator overhead vs direct

Carrier cost is identical either way for VN origin (it is our own BYO contract).
The orchestrator only adds a software fee. Estimates assume 3 rate quotes per
label (checkout shows options) and BYO carrier accounts.

| Monthly labels | EasyPost BYOCA | ShipStation API | Easyship (LYOC) |
|---|---|---|---|
| 500 | $20 + 500×0.08 = **$60** ($0.12/parcel) [estimate] | Advanced 1k **$75** ($0.15) [estimate] | Plus $29 + 500×0.05 = **$54** ($0.11); 1,500 rate calls incl. [estimate] |
| 5,000 | $20 + 400 = **$420** ($0.084) [estimate] | Advanced 5k **$325** ($0.065) [estimate] | Premier $69 + 200 + 5k extra rates×0.007 = **~$304** ($0.061) [estimate] |
| 50,000 | ~$4,020 list ($0.080); enterprise lower [estimate] | 10k plan $600 + 40k×0.06 = ~$3,000 ($0.06); enterprise custom [estimate] | Scale 3 $199 + 1,500 + 110k rates×0.006 = **~$2,360** ($0.047) [estimate] |

Add Easyship Tax & Duty at $0.09–0.11 per call if used for landed cost (≈ $50 at
500/mo, ≈ $4.5k at 50k/mo if called per label; more if called per checkout view)
[estimate].

Against a typical VN->SEA DHL Express parcel cost of several USD to tens of USD
(see carrier slices), $0.05–0.15/label is <2% of shipping cost. **The real costs of an
orchestrator are elsewhere:**
- it does not lower carrier rates from VN (no pooled VN contracts);
- it hides carrier-specific DDP/duty billing details we need for reconciliation
  (duty invoices still come from DHL, not from the orchestrator, in BYO mode);
- another vendor with outages, rate-limit and data-processing terms (buyer PII);
- abstraction lag: new carrier features (e.g. DHL paperless trade, IOSS/OSS-like
  fields, SEA local-tax IDs) arrive later or not at all.

**At 500/month** a direct DHL Express integration (MyDHL API REST) is one module
we would write anyway; the orchestrator saves ~1–2 weeks of carrier-integration
work at most, for one carrier. **At 50k/month**, direct contracts are mandatory for
rates, and the orchestrator fee (~$2–4k/mo) buys only API normalization, which we
already have in our own module layer.

## Opinion: hybrid setup and how it maps to one-module-per-courier

Hybrid ("orchestrator for long-tail, direct for main SEA lanes") is sound **in
principle**, but for VN origin today the long tail an orchestrator can serve is
just DHL/FedEx/UPS on our own accounts — the same carriers we would integrate
directly. So the hybrid only becomes worth it when (a) we add non-VN origins
(SG/HK/EU/US merchants) where Easyship/EasyPost sell pooled rates, or (b) we
need many carriers quickly for rest-of-world.

Mapping to our architecture:
- An orchestrator is **one module like any carrier module** (`couriers/easyship.py`)
  implementing the same interface: quote, create shipment + label, cancel, tracking
  webhook decode, invoice import, return label. Its quotes carry the underlying
  carrier as data (`underlying_carrier: CarrierCode`, `service_code`), not as a
  separate module.
- The one place that picks a provider by name (as with PSPs) routes per lane:
  `(origin, destination, weight band, DDP?) -> provider`. The orchestrator is just
  another value in the closed `ShippingProvider` enum.
- Do not route the **same** carrier account through both a direct module and an
  orchestrator: duplicate shipment ids, two tracking feeds, and duty invoices that
  arrive from DHL while the label came via EasyPost make reconciliation ambiguous.
  One carrier account ↔ exactly one module.
- Tracking: normalize into our own closed `TrackingStatus` set in each module; a
  tracking aggregator (17TRACK/AfterShip) would be a separate *tracking source*
  module keyed by `(carrier, tracking_number)`, idempotent on event id.
- Duty/shipping invoices: always imported from the party that bills us. In BYO
  mode that is the carrier, even when the label came via an orchestrator — so the
  carrier still needs an invoice-import module. This is the strongest argument
  against orchestrators for our main lanes.

## Compliance / open questions (do not assume answers)
- Exporter of record from VN is physically the merchant; we are seller and DDP
  duty payer. Whose name goes on the commercial invoice and AWB "shipper" field,
  and whose carrier account (ours, SG/NL entity) can book exports from VN? DHL VN
  may require a VN-registered account holder — ask DHL VN and Boxme.
- Buyer PII flows to every orchestrator/tracker we add (data processing agreements;
  ID/VN/TH data-protection laws).
- Easyship's VN "coming soon": ask sales for date and whether VN-origin rates would
  include DDP to SEA.

## Sources
- https://www.easyship.com/blog/easyships-upgraded-global-shipping-api-for-ecommerce
- https://www.easyship.com/en-vn/couriers
- https://www.easyship.com/en-vn/shipping-rate-calculator/vietnam-to-malaysia
- https://support.easyship.com/hc/en-us/articles/360000487172 (403; via search snippet)
- https://developers.easyship.com/reference/webhooks-guide
- https://support.easypost.com/hc/en-us/articles/39984592062605-EasyPost-Wallet-and-Bring-Your-Own-Carrier-Account-Plans
- https://support.easypost.com/hc/en-us/articles/4407272347661-DHL-Express-Wallet-Guide
- https://docs.easypost.com/carriers/dhl-express-guide
- https://docs.easypost.com/guides/webhooks-guide
- https://support.easypost.com/hc/en-us/articles/39826034964237-Webhook-HMAC-Validation
- https://docs.goshippo.com/carriers/carrier-capabilities.md
- https://docs.goshippo.com/docs/tracking/webhooksecurity
- https://www.shipstation.com/shipping-api/pricing/
- https://docs.shipengine.com/docs/account-country/
- https://docs.shipstation.com/apis/docs/guides/webhooks-validation
- https://www.aftership.com/docs/shipping/carrier-guide-supported-couriers (403)
- https://shipping-helpcenter.aftership.com/en/article/aftership-shipping-and-easyship-integration-1gl0vvt
- https://helpcentre-my.easyparcel.com/support/solutions/articles/9000188598
- https://biteship.com/en/harga
- https://support.sendcloud.com/hc/en-us/articles/36317556679569-How-to-ship-from-Portugal-using-Sendcloud
- https://help.unifaun.com/uo-se/en/integrations/price-via-api.html
- https://boxme.asia/?p=104443 ; https://developers.boxme.asia/api-7188116 ; https://boxme.asia/2018/05
- https://apis.io/providers/janio/ ; https://bitscale.ai/directory/janio-asia
- https://raw.githubusercontent.com/api-evangelist/17track/refs/heads/main/plans/plans.yml
- https://g2.com/products/parcel-perform/pricing
- https://revaddress.pages.dev/blog/shipping-api-comparison-2026/ (3rd-party)
- https://1teamsoftware.com/2026/02/09/easypost-new-pricing-plans-2026/ (3rd-party)

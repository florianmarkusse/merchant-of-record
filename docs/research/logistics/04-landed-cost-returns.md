# 04 — Landed-cost / customs compliance APIs, and cross-border returns

Research date: 2026-11-06. Labels: [published] = vendor or government page,
[calculator] = output of a vendor tool, [estimate] = our own or a third-party
rule of thumb. UNVERIFIED = could not confirm from a primary source in this
session. No vendor was contacted; all prices are list or public prices, and
volume contracts may differ.

---

## Part A — Landed-cost and customs compliance APIs

### A.0 What our checkout actually needs (frames the comparison)

Per order, VN origin -> SG/MY/TH/ID/PH, DDP, apparel:

1. Duty + import tax quote, stored with inputs, rule version and provider
   response id (AGENTS.md: "the rule applied, its version and the
   buyer-location evidence are stored with each transaction").
2. HS code proposed from a product description, confirmed by the merchant,
   stored per SKU (6-digit HS + destination 8/10-digit national code).
3. Restricted/prohibited-goods screening per destination (ID apparel has
   quantity limits; see A.1), plus denied-party screening.
4. Seller-collected low-value-goods tax where WE are registered (SG OVR
   GST, MY LVG sales tax) vs border-collected tax (TH, ID, PH), so the quote
   must know which regime applies and must not double-charge.

Key design consequence: because we are MoR and (if registered) the deemed
supplier under SG OVR / MY LVG, the calculator must be able to run in a
"merchant self-remits under its own tax ID" mode. Several vendors default
to remitting under THEIR registrations when you use their guarantee, which
would make THEM the tax-registered supplier, conflicting with our MoR role.

### A.1 Current SEA regimes the tools must get right (checked this session)

| Country | Regime as found | Source |
|---|---|---|
| SG | GST 9% on LVG ≤ S$400 (sales value, excl. shipping/insurance per Customs) collected by registered overseas vendor (OVR) if global turnover > S$1m and LVG B2C > S$100k/yr [published]. Above S$400 or by sea: import GST at border. | https://www.customs.gov.sg/personal-shipment/buying-from-overseas/gst-on-low-value-goods/ ; IRAS e-Tax guide (below) |
| MY | 10% sales tax on LVG ≤ RM500 (price excl. shipping/insurance), seller registers when LVG sales > RM500k in 12 months; quarterly returns; since 2024-01-01 [published]. No 2025–26 change found (UNVERIFIED that none exists). | https://bdo.global/en-gb/insights/tax/indirect-tax/malaysia-sales-tax-now-levied-on-low-value-goods ; https://rsm.global/malaysia/node/93 |
| TH | THB 1,500 duty exemption abolished from 2026-01-01 (Customs Notification 219/2568); VAT 7% on low-value parcels since July 2024; duty now from first baht [published, secondary]. Further duty increases announced 2026-03-30 (UNVERIFIED for apparel). | https://www.forvismazars.com/th/en/insights/doing-business-in-thailand/tax/thailand-e-commerce-import-duty-2026 ; https://www.tilleke.com/insights/thailand-announces-customs-duty-hikes-for-key-consumer-goods |
| ID | Duty de minimis USD 3 FOB; tax de minimis 0 (VAT on everything). Textiles/apparel, bags, shoes carved out with higher duty (textiles 15–25%, bags 15–20%, shoes 25–30%) and apparel limited to 5 pcs/shipment per Zonos guide [published, vendor]. PMK 4/2025 (effective 2025-03-05) reportedly restructured tiers (0/15/25%) [published, secondary, UNVERIFIED against DJBC text]. Zonos's ID page shows VAT 12% CIF and an inconsistent PPh figure, so treat vendor ID data as needing verification. | https://zonos.com/docs/guides/country-guides/indonesia ; https://www.cekindo.com/blog/import-duty-indonesia |
| PH | De minimis PHP 10,000 (CMTA). A 2025 CAO (reportedly CAO 02-2025, effective 2025-05-07, B2C air only) — scope of VAT waiver disputed between sources (UNVERIFIED). Retailers lobbying to scrap it. | https://janio.asia/articles/philippines-de-minimis-rate/ ; https://business.inquirer.net/525027/boc-urged-to-scrap-de-minimis-rule ; https://treayo.com/en/de-minimis |
| VN (inbound, for returns) | VND 1m express de minimis (Decision 78/2010) repealed by Decision 01/2025/QD-TTg, effective 2025-02-18: low-value express imports now pay duty+VAT [published, gov't news]. Matters for returns shipped back to VN. | https://en.baochinhphu.vn/viet-nam-to-levy-low-value-imports-from-february-18-11125010609453845.htm |
| EU (later) | €150 duty relief ends; flat €3 customs duty on low-value parcels from 2026-07-01 (per item vs per parcel disputed); transitional to ~2028; ~€2 handling fee proposed (UNVERIFIED). | https://help.easproject.com/hc/en-gb/articles/5276913150495 ; https://marosavat.com/vat-news/eu-removal-of-customs-duty-relief-for-low-value-consignments |
| US (later) | Section 321 $800 de minimis suspended for all origins from 2025-08-29 (EO 14324) [published, secondary]. | https://www.claimlane.com/resources/blog/section-321-and-duty-drawback-for-ecommerce-2026 |

Conclusion: SEA rules moved in 2024–2026 (TH, ID, PH, VN all changed).
Whichever vendor we use, we must be able to see the rule it applied, and
our own regime switch (seller-collected vs border-collected) should live in
our per-jurisdiction tax module, not inside the vendor.

### A.2 Vendor-by-vendor

#### Zonos (Landed Cost, Classify, Screen, Restrict)

- API: GraphQL (plus a legacy REST "landed cost REST API"). Flow: send
  parties, items, carton, shipment rating -> `landedCost` quote with a
  `landedCostId`; then `orderCreate` with that id to finalise
  [published]. Passing an HS code: Zonos validates it on the fly.
  https://docs.zonos.com/landed-cost/api-examples/calculate-landed-cost---graphql ;
  https://zonos.com/docs/api-reference/landed-cost-rest-api
- Pricing, Landed Cost: USD 2 per order + 10% of duties, taxes and
  non-Zonos fees, for guaranteed (DDP) quotes; if only DDU available the
  quote is an estimate, USD 2 still applies, 10% does not; if no duty/tax
  due, only USD 2; extended guarantee up to 1 year +10% of duty/tax;
  cross-dock export processing USD 5 [published].
  https://zonos.com/docs/global-ecommerce/landed-cost/pricing
  Worked example from Zonos: 100 USD item, 41 USD D&T&fees -> 6.10 USD fee
  [published]. For our ~USD 30–60 apparel AOV into SEA with D&T maybe
  USD 3–15, fee ≈ USD 2.30–3.50/order [estimate].
- Guarantee mechanics: covers discrepancies for 90 days quote->clearance;
  ONLY if the carrier bills duties/taxes third-party to Zonos and
  shipments are linked by tracking number; DHL/FedEx/UPS account-level
  billing to Zonos "not certified" in some shipping systems [published].
  https://zonos.com/docs/global-ecommerce/landed-cost/guarantee ;
  https://docs.zonos.com/landed-cost/calculate/eligibility-and-coverage
  -> For us this means the guarantee only works if our courier (Janio /
  DHL / Ninja Van etc.) can bill duties to Zonos. For regional SEA
  consolidators this is UNVERIFIED and likely not supported.
- Tax remittance and MoR conflict: With the guarantee, Zonos uses its own
  EU IOSS / UK VAT numbers; for SG it applies its own SG GST ID once the
  merchant crosses the OVR threshold [published]. Without guarantee, the
  merchant enters its own GST number and remits to IRAS itself. Own-ID
  self-remit (`taxIdNumber` + `taxRemittanceCalculation: NO_REMITTANCE`)
  is documented for EU IOSS only; SG/MY equivalents are set in Dashboard
  "Tax IDs" [published].
  https://zonos.com/docs/landed-cost/tax-remittance/singapore-extended-ovr ;
  https://zonos.com/docs/supply-chain/landed-cost/calculate-landed-cost---graphql/merchant-provided-tax-id
  Zonos also now markets itself as MoR for API integrations — i.e. it is
  partly a competitor.
  => Can we use Zonos as a pure calculator while we stay MoR/collector?
  Yes for the estimate path (USD 2/order, our own tax IDs, we pay duties
  via courier DDP). Using the guarantee path pulls Zonos into the payment
  and (for SG/EU/UK) remittance chain, which conflicts with our MoR model
  and must be avoided or contractually carved out. Whether Zonos will
  sell the calculation API stand-alone at USD 2/order without the
  guarantee or a platform fee: UNVERIFIED (ask sales).
- Classify: AI HS classification, claimed >90% accuracy "out of the box",
  +27% average improvement after 3 months of training, 110 ms average
  [published, vendor claims, no methodology]. Pricing: Standard USD 500
  /mo for 10,000 classifications (USD 0.10 overage), 25,000 for USD 1,150;
  Advanced USD 1,000, Premium USD 1,250 /mo; 1,000 free in Dashboard;
  Premium includes manual audit of a subset [published; partly from a
  zh-TW pricing page and a 3rd-party comparison].
  https://zonos.com/docs/supply-chain/classify/classifying-goods ;
  https://zonos.com/zh-tw/docs/supply-chain/classify/pricing ;
  https://gingercontrol.com/blog/duty-tax-api-pricing-comparison
  At 500 parcels/month and maybe a few hundred SKUs, USD 500/month is too
  much; the 1,000 free classifications may cover the first months.
- Screen (denied party): 100+ lists, 30+ countries, fuzzy match with
  REVIEW / NO_MATCHES; Restrict (item restrictions) available via API;
  Greenlight bundles 5 export checks [published]. Pricing not published.
  https://zonos.com/screen ; https://zonos.com/restrict
- SEA coverage: has country guides and rules for all five; the ID page has
  internal inconsistencies (PPh 7.5/10/15%, "textile articles" listed as
  prohibited and also with duty rates) [published] -> data quality for ID
  apparel needs our own spot check.
- Audit data: quote id (`landedCostId`), line-level duties/taxes/fees,
  remittance lines, HS used. Retention period UNVERIFIED.

#### Avalara (AvaTax Cross-Border, Item Classification)

- API: REST, part of AvaTax. Use `SalesOrder` (non-committing) for quote at
  checkout, `SalesInvoice` to record [published, via Violet integration
  guide]. An "importer of record" flag drives whether D&T are calculated
  as collected by seller (DDP) [published].
  https://docs.violet.io/prism/checkout-guides/guides/calculating-duties-with-avalara ;
  https://woocommerce.com/document/landed-cost-avalara/
- Classification: acquired 3CE Technologies (2021) — 3CE is now
  Avalara's classification engine; offers "Managed Tariff Code
  Classification" (human-assisted) and "Trade Restrictions Management";
  powers eBay International Shipping for 200M+ items [published].
  https://www.businesswire.com/news/home/20230427005209/en/
- Pricing: quote-based, nothing published for Cross-Border [published
  absence]. https://gingercontrol.com/blog/duty-tax-api-pricing-comparison
- SEA: Avalara offers SG/MY LVG GST registration and return filing as a
  managed service [published]. https://www.avalara.com/us/en/blog/countdown-to-new-singapore-gst-rules.html
  Good if we want filing outsourced; but AvaTax is US-sales-tax-centric
  and enterprise-priced. Estimated vs guaranteed: estimate only (no
  guarantee product found).
- Audit: AvaTax stores committed transactions (good for audit). Detail of
  which duty rule version is returned: UNVERIFIED.

#### Hurricane Commerce (Aura / Kona API)

- UK, B2B-focused landed-cost engine used by posts/carriers (Royal Mail
  DDP via Aura): duty & tax calc, prohibited/restricted goods, denied
  party screening; 100–500 ms per call, tested ~640 TPS [published, via
  Royal Mail]. Pricing quote-only. Strong for carrier-grade IOSS/UK/EU;
  SEA depth UNVERIFIED.
  https://www.royalmail.com/business/international/guide/delivered-duties-paid/aura-hurricane

#### Descartes CustomsInfo

- Trade content (tariffs, duties, taxes for 190+ countries), AI-enabled
  HS classification, landed cost, denied party screening; integrates into
  ERP. No public checkout API docs or prices found [published absence].
  Enterprise trade-compliance tool, not a checkout API for our size.
  https://www.customsinfo.com/solutions/customs-info-reference/

#### Easyship (Taxes & Duties API, HS lookup)

- REST: `POST https://api.easyship.com/2023-01/taxes_and_duties`; rates
  API returns `incoterms` DDU/DDP, separate DDU estimates
  (`estimated_import_duty`, `estimated_import_tax`) vs DDP charges
  (`import_duty_charge`, `import_tax_charge`) plus `ddp_handling_fee`
  [published]. https://developers.easyship.com/docs/how-to-calculate-taxes-and-duties ;
  https://developers.easyship.com/docs/how-to-display-rates-at-checkout
- Pricing (USD): pay-as-you-go 0.11/T&D call, 0.05/HS call; Plus USD 29
  /mo (125 incl.), Premier 69, Scale 99–199 (1,250–2,500 incl., 0.09
  overage); HS 0.03–0.05 [published, Dec 2025 blog / Feb 2026 sheet].
  https://www.easyship.com/blog/easyships-upgraded-global-shipping-api-for-ecommerce
  At 500 orders/month: ~USD 55/month for quotes pay-as-you-go
  [estimate]. HS "lookup" returns candidate codes, not a classification
  [published, 3rd party].
- Easyship is also a shipping aggregator with a Singapore base; DDP quote
  is "charged" when you buy an Easyship DDP label — i.e. guaranteed only
  if Easyship ships it (UNVERIFIED in contract terms). Dev forum reports a
  DDP-not-sticking bug for AU (unresolved).
- Cheapest proper REST API in this list; SEA lanes are Easyship's home
  market. Audit: no rule-version field found (UNVERIFIED).

#### SimplyDuty

- Duty calc + classification APIs, ~GBP 0.10/call, 5 free/day; plans
  GBP 9.99 (100 calls) to 499.99 (5,000) [published but pricing page
  dated 2019 per 3rd-party comparison]. Small UK vendor; fine as a
  second-opinion/backtest source. https://www.simplyduty.com/about

#### Dutify

- REST landed-cost API; POST = 1 calculation, GET free; Basic USD 15/mo
  (200), Plus USD 75/mo (2,000 calculations, API access), Pro USD 149
  (5,000) [published, via comparison + docs]. No guarantee.
  https://docs.dutify.com/docs/landed-cost-calculator-api
  At 500/month: USD 75/month flat [estimate].

#### 3CE / Classify

- Now part of Avalara (see above); stand-alone 3CE tool pricing not found.

#### Others seen
- KYG Trade, HTS Hero, Tarifflo: classification-only, US-centric, priced
  USD 0.44–125 per item [published via comparison page]; not SEA-focused.
- Swap Commerce: guaranteed landed cost via enterprise API, no public price.

### A.3 MoR competitors as reference designs

#### Global-e
- Roles (from Global-e docs): Global-e = MoR (seller on every order;
  remits taxes in some destinations); merchant = Exporter of Record and
  (or its 3PL) shipper; customer = Importer of Record (except CH, where
  Global-e Switzerland is IOR); for returns, merchant/3PL is the IOR
  back into origin and applies Returned Goods Relief [published].
  https://docs.global-e.com/shipping-non-globale-overview
- Collects D&T upfront ("guaranteed landed cost" via prepaid DDP), deducts
  D&T and fees, remits remainder to merchant [published, 3rd party].
- Returns: customer drops at local hub; returns consolidated per region
  and shipped back in bulk; high-volume merchants get direct-to-merchant
  [published, via search summary; docs page 403'd]. Loop partnership
  (Feb 2025): refunds in original currency; duties NOT refunded by
  default where shown as a separate line; store credit for international
  returns; configurable handling fees [published].
  https://help.loopreturns.com/en/articles/2277313
  Design lesson: separate "refund of goods + seller-collected tax" from
  "refund of duties paid at border", and make duty refund a policy flag.

#### ESW (eShopWorld)
- MoR, takes trading risk, tax/duty/regulatory per market; returns =
  branded portals, country-specific return labels, configurable refund
  rules [published]. No public hub model found. https://esw.com/ecommerce/

#### Passport Global
- Offers "Seller of Record" (SoR) bundle, DDP to 180+ countries,
  Passport Returns with local drop-off, Loop integration; D&T refunds in
  eligible markets when using SoR; 5% fee on GST/VAT (on CIF) for
  shipping-only customers [3rd-party, UNVERIFIED]. US-origin focus; no
  Asia-origin detail found.
  https://passportglobal.com/blog/ior-vs-mor-vs-sor-which-global-ecommerce-model-does-your-brand-need/

#### FlavorCloud (checked 2026-11-10, Vietnam origin only)
- DDP shipping network with guaranteed landed cost at checkout, 220+
  destinations over 300+ carriers [published]. Origins named publicly:
  the US and, since July 2025, Canada (a dedicated outbound DDP network for
  Canadian merchants) [published]. Fulfilment centres in the US, CA, EU, UK
  and AU (ShipBob partnership) [published].
- **Vietnam origin: none found.** VN appears only on its destination list.
  No page lists origin countries [published, UNVERIFIED beyond that].
  https://flavorcloud.com/resources/knowledge-base/where-flavorcloud-will-ship-to-global-origin ;
  https://flavorcloud.com/press-release/flavorcloud-launches-dedicated-outbound-shipping-network-for-canadian-merchants
- Same position as Passport: its guaranteed DDP has it pay the border
  taxes, and it sells to merchants who would otherwise use us. Not a supplier.

#### Flexport
- Not relevant for B2C parcels at our volume (freight forwarding); skipped.

#### SEKO Logistics (checked 2026-11-10, Vietnam origin only)
- Has its own offices in Ho Chi Minh City (near Tan Son Nhat) and Hanoi:
  air and ocean freight, customs clearance, intra-Asia trucking, inbound
  and outbound [published].
- Markets an APAC direct-to-consumer offer for brands manufacturing in
  Greater China and **Vietnam**, delivering to HK, SEA and AU, with DDP
  listed for Asia-bound lanes [published, marketing copy].
- Its cross-border e-commerce product is built mainly for US imports
  (Section 321, Type 86) [published].
- **Vietnam origin: yes, as a freight forwarder; as a per-parcel B2C
  service from VN, UNVERIFIED.** No VN-origin parcel product, API or
  price found. It would fit a later consolidation design (bulk air to a
  hub, then local last-mile), not v1 per-parcel shipping. Worth a sales
  question only when that design comes up.
  https://www.sekologistics.com/asia-en/regions/asia/vietnam/seko-logistics-ho-chi-minh-city/ ;
  https://www.sekologistics.com/hk-en/industries/direct-to-consumer-brands/ ;
  https://www.sekologistics.com/en/ppc/cross-border-ecommerce

### A.4 Comparison and recommendation

| Vendor | API | SEA depth | Guarantee | Price @ 500 orders/mo | Fit |
|---|---|---|---|---|---|
| Zonos | GraphQL, good docs | Guides for all 5; ID data inconsistent | Yes, only if duties billed to Zonos | ~USD 1,000 (USD 2/order estimate) + Classify USD 0–500 [estimate] | Best feature set; MoR conflict on guarantee path |
| Easyship T&D | REST, simple | SEA home market | Only bundled with its labels (UNVERIFIED) | ~USD 55 [estimate] | Cheapest; good v1 |
| Dutify | REST | UNVERIFIED | No | USD 75 flat | Cheap backup |
| Avalara | REST (AvaTax) | SG/MY filing service | No | Quote-only | Overkill v1; consider for filing |
| SimplyDuty | REST | UNVERIFIED | No | ~GBP 50 | Second opinion |
| Hurricane / Descartes | Enterprise | UNVERIFIED | Carrier-grade | Quote-only | Later |

Recommendation for v1:
1. Own the regime logic ourselves (per AGENTS.md: one tax module per
   jurisdiction, effective-dated). SG OVR GST and MY LVG sales tax are
   seller-collected flat rates on goods price; we compute them, we remit
   them. Only the border part (TH duty+VAT, ID duty+VAT+PPh, PH above
   PHP 10k, SG/MY above thresholds) needs a duty engine.
2. Use a vendor only for (a) HS duty rates by destination and (b)
   restricted-item flags. Start with Zonos Landed Cost in non-guaranteed
   mode with our own tax IDs if Zonos will contract it at ~USD 2/order
   without Zonos remitting; else Easyship T&D (USD 0.09–0.11/call) — which
   is also cheap enough to call on every quote and shadow-compare against
   our own figures.
3. HS classification: at a few hundred SKUs, use Zonos Classify's free
   1,000 / Easyship HS lookup to PROPOSE codes, merchant confirms, we
   store per SKU with source + confidence. Apparel classification (knit vs
   woven, fibre content, gender) is decided by facts the merchant must
   supply; collect them as structured fields, not free text.
4. Store with each quote: vendor, vendor quote id, request payload hash,
   HS per line, de minimis/regime applied, rates, FX source/rate,
   our rule version. The vendor response is evidence, our record is the
   truth.
5. Don't depend on a vendor "guarantee". Reconcile courier duty invoices
   against quotes per shipment and book the variance (AGENTS.md: remainder
   booked, never dropped).

---

## Part B — Cross-border returns

### B.1 Physical models

| Model | How | Cost/time | Fit for v1 |
|---|---|---|---|
| 1. Return to VN merchant, single parcel | Buyer gets prepaid express label (DHL/Easyship/Janio), ships to VN | Return roughly 1–2x outbound cost; a USD 30 outbound -> USD 60+ return in one vendor example [estimate]; 5–10 days | Only for high value; VN import formalities each time |
| 2. Regional hub (SG or MY), batch back | Buyer drops locally; hub in SG/MY grades and holds; monthly consolidated shipment to VN or local resale/liquidation | Domestic last-mile + hub handling; consolidated freight cheap per unit [estimate] | Good from ~50+ returns/month; providers: ZigZag (Janio as carrier), ReBound, Janio 4PL |
| 3. Returnless refund ("keep it") | Refund (or partial) without return when return cost > ~30–50% of item value | Cost = item COGS only | Best for low-value apparel; needs fraud rules |
| 4. Local resale / donation in destination | Hub resells locally (marketplaces) or donates/destroys | Recovers some value; tax: becomes a local sale | Later; licensing/tax per country |

Rules of thumb found: returnless when return shipping > 30% of product value;
cross-border return "rarely worth it below ~USD 25 refund value, usually
worth it above ~USD 50" [estimate, vendor blogs].
https://shopappy.com/logistics/returns-reverse-logistics/returnless-refunds ;
https://www.claimlane.com/resources/blog/returnless-refunds

### B.2 Providers

- ZigZag Global: returns platform + hub network (220+ local warehouses,
  German hub 470k parcels in 2024); lists Janio Express / Janio Collection
  as its SEA cross-border return carriers; Shopify tiers USD 0/20/50/199
  /mo, per-return and enterprise pricing not public [published].
  https://www.zigzag.global/carriers/returns-with-janio ;
  https://apps.shopify.com/zigzag-returns-and-exchanges
  Note: a 2026 article refers to a "Shipup deal" (ZigZag acquired or
  merged) — UNVERIFIED. https://wiserreview.com/blog/zigzag-alternatives/
- Janio (SG): SEA 4PL, lists returns and freight consolidation across 15+
  countries; earlier domestic SG return programme; no public cross-border
  returns pricing [published]. https://janio.asia/
- ReBound: cross-border returns manager: local collection/drop-off, hub
  consolidation, grading, customs, consolidated transport; 40+ hubs (NA,
  EU, UK, AU); historically served ANZ from Singapore [published].
  https://reboundreturns.com/returns-management
- Easyship: prepaid return labels via API for countries with negotiated
  rates; used by AfterShip Returns and ReturnGO (LabelGO); price = label
  price from rate calculator [published].
  https://support.easyship.com/hc/en-us/articles/360020926852 ;
  https://www.easyship.com/blog/returngo-preferred-shipping-partner
- DHL Express: return labels/e-Returns in 220+ countries, retailer sets
  the return fee; label-free option since Oct 2024; Asia rate card not
  public [published]. https://www.dhl.com/no-en/home/press/press-archive/2024/dhl-express-introduces-a-new-label-free-service-for-shipment-returns.html
- Loop / ReturnGO: returns portals (policy engine, exchanges, store
  credit); they rely on a carrier/label API (Easyship, Global-e) for
  cross-border legs.
- Swap Commerce: returns + duty drawback on behalf of brand; "Global
  Duty Recovery" credits a flat 75% of import duties on returned orders
  [published, vendor claim]. https://www.swap-commerce.com/global-duty-recovery
- Global-e: hub-and-consolidate returns (see A.3).
- Parcelpoint, SEA-specific return hubs (Ninja Van, SingPost, Locad):
  no cross-border returns product found in this session (UNVERIFIED).
  Asendia + SingPost partnership (APAC gateway) may become one.

### B.3 Fiscal treatment of returns

Destination side (refund of taxes we collected or paid):

- SG, LVG under OVR (we collected GST): issue credit note, refund GST with
  price, reduce supplies (Box 1) and output tax (Box 6) in the period the
  credit note is issued; net refund is carried forward or refunded on
  request (IRAS e-Tax guide §14.8 and the "Refunds" feature row)
  [published]. If GST was double charged (also at border), we must refund
  the customer on evidence and recover it in our next return (§8.4).
  https://www.iras.gov.sg/docs/default-source/e-tax/gst-e-tax-guide_taxing-imported-low-value-goods-by-way-of-the-overseas-vendor-registration-regime_(1st-ed).pdf
- SG, above S$400 (import GST paid at border via courier): refund of
  duties/GST by written application to Singapore Customs within 5 years
  for overpayment; re-export refund for consumer returns specifically
  UNVERIFIED. https://customs.gov.sg/businesses/valuation-duties-taxes-fees/refund-of-duties-and-gst
- MY LVG: analogous credit-note adjustment presumably allowed —
  UNVERIFIED (check RMCD LVG guide).
- TH, ID, PH (border-collected duty/VAT paid by us via DDP courier):
  recovery on re-export is generally impractical at our values; treat as
  sunk cost and decide policy on whether to refund the buyer for it
  (Global-e/Loop default: duties not refunded) — refund mechanisms
  UNVERIFIED per country.
- EU (later): consumer returns of IOSS goods: seller refunds VAT and
  corrects the IOSS return (same period: not reported; later period:
  correct in period of refund) [published, Spanish AEAT]. Non-IOSS
  import VAT recovery relies on invalidating the import declaration
  (UCC-DA Art. 148) [published]. Art. 203 Returned Goods Relief is for
  goods returning INTO the EU, not consumer returns out [published].
  https://sede.agenciatributaria.gob.es/Sede/en_gb/ayuda/manuales-videos-folletos/manuales-practicos/manual-importacion-exportacion-envios-escaso-valor/3-mecanismos-recaudacion-iva/3_1-regimen-ventanilla-unica-importaciones/3_1_7-devolucion-bienes-declarados-ioss.html
- US (later): drawback 19 USC 1313(j)(1) unused merchandise / 1313(c)
  rejected incl. retail returns "regardless of use"; claimable up to 99%;
  now relevant since de minimis ended; needs a licensed broker/claimant
  [published, mixed CBP rulings + vendor].
  https://www.ecfr.gov/current/title-19/chapter-I/part-191

Origin side (VN re-import):

- Decree 134/2016/ND-CP Art. 33 (amended by Decree 18/2021): exported
  goods that must be re-imported are exempt from import duty (and export
  duty paid is refunded) on a dossier proving the original export;
  procedure via Circular 06/2021/TT-BTC, Form 14/TXNK [published,
  secondary: lawnet.vn]. Customs procedure code A31 (re-import of
  returned exports) [published, secondary].
  https://lawnet.vn/ngan-hang-phap-luat/en/tu-van-phap-luat/xuat-nhap-khau/re-import-of-exported-goods-is-import-tax-required-1024916
- If customs cannot link the goods to the export declaration, import duty
  is charged and export duty not refunded [published, secondary].
- Import VAT on re-imported returns: UNVERIFIED (not found). Since
  2025-02-18 the VND 1m express exemption no longer applies, so single
  returned parcels without the A31 dossier will be taxed.
- Practical: per-parcel A31 dossiers are heavy; another reason to batch
  returns at a hub and re-import monthly under one declaration, or not
  bring them back at all. Who is the re-importer (merchant, since VN
  merchant is exporter of record) and whether consolidated mixed-merchant
  re-imports are possible: UNVERIFIED — VN customs broker question.
- Merchant-side VN VAT: the export was 0% rated; the return is a cancelled
  export sale, adjust the merchant's VN books. Out of our scope, but our
  merchant statement must show it.

### B.4 Return rates and costs

- Apparel online overall: 30–40% (2019, domestic/unspecified)
  [estimate, vendor]. https://www.flow.io/blog/how-to-navigate-the-growing-pains-of-reverse-logistics
- Cross-border all-category ~25% (IMRG, unverified), APAC 15–20% (Statista
  2024, all-category), SEA ~10% [estimate, vendor blogs; direction
  disputed — some say cross-border returns are 30–50% LOWER than domestic].
  https://branch8.com/posts/cross-border-returns-management-apac-e-commerce-brands-guide ;
  https://branvas.com/blogs/news/cross-border-ecommerce-returns-report
- 56% of Gen Z/Millennials say they won't return unwanted international
  purchases [published survey]. https://hk.fashionnetwork.com/news/Over-half-of-young-shoppers-won-t-return-unwanted-international-e-purchases,1384840.html
- Planning number for v1: 8–15% return *requests* for SEA apparel,
  of which many resolved without physical return [estimate].
- Cost per physical return SEA -> VN by express single parcel: USD 15–40
  [estimate, UNVERIFIED — needs Janio/DHL/Easyship quote]. Hub drop-off +
  consolidation: USD 4–10 per item [estimate, UNVERIFIED].

### B.5 Recommended returns model for v1

1. Policy per merchant, enforced by us as MoR (we are the seller, so
   consumer-law return rights in each country are OURS — note PH/ID/TH/
   MY/SG consumer law on online returns is UNVERIFIED and a compliance
   item).
2. Default: returnless refund (or partial refund / store credit) for
   items under a threshold (e.g. < USD 40 or when return cost > 40% of
   price) [estimate]; require photo evidence; cap per buyer.
3. Above threshold: prepaid return label to a single SG hub (ZigZag+Janio
   or Janio directly), monthly consolidated shipment to VN under A31 with
   the original export references; or local liquidation if the merchant
   prefers.
4. Refund: goods price + seller-collected tax (SG GST/MY LVG via credit
   note). Border-paid duties/import VAT: not refunded by default (like
   Global-e), configurable per merchant.
5. Ledger: refund, credit note, tax adjustment, return shipping cost, and
   later any duty recovery are separate journal entries; a returned item
   is a state in the order state machine (requested -> approved ->
   in_transit -> received_at_hub -> refunded / keep_refunded), driven by
   messages, idempotent per return id.

### B.6 Biggest unknowns

- Whether Zonos (or anyone) sells the duty calculation API without the
  guarantee/remittance chain that conflicts with our MoR role, and at what
  minimum.
- Accuracy of vendor data for ID apparel (PMK 4/2025 tiers, quantity
  limits, import licence) and TH post-2026 rates — need our own test set.
- Whether our SEA courier (Janio/Ninja Van/DHL) supports DDP with duties
  billed to us (or to Zonos) per lane, and gives per-shipment duty
  invoices we can reconcile.
- MY LVG and other countries' refund/credit-note mechanics for returns.
- VN re-import: VAT treatment, who is the declarant for consolidated
  mixed-merchant returns, time limits.
- Real return costs per lane (no public rate cards): needs quotes.
- Consumer-protection return obligations per country when WE are the
  seller.

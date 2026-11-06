# Research: logistics providers, customs and returns

Status: desk research, 2026-11-06. No decisions are made in this file yet.
The recommendations in §2 wait for confirmation, and most prices wait for
sales quotes (§9). The visual summary is in `docs/logistics.html`.

Scope (agreed 2026-11-06): merchants in Vietnam (other origins are a plus),
apparel and fashion accessories first, buyers in SEA first and then the
rest of the world, DDP sales, low volume at the start (about 500 parcels a
month, growing to 5k–50k). Goal: a merchant can use our integration alone
and still reach all of its customers.

The detailed findings with every source URL are in `docs/research/logistics/`:

| File | Covers |
|---|---|
| `01-sea-carriers.md` | Carriers and cross-border operators VN → SG, MY, TH, ID, PH; VN first mile |
| `02-global-carriers.md` | DHL Express, FedEx, UPS, Aramex, Asendia, Cainiao, YunExpress, 4PX, SingPost, VN Post EMS from VN; published VN rate tables |
| `03-orchestrators.md` | Easyship, EasyPost, Shippo, ShipStation API, AfterShip, regional aggregators, tracking platforms |
| `04-landed-cost-returns.md` | Zonos, Avalara, Easyship tax API and others; Global-e and ESW as reference designs; cross-border returns |
| `05-regulations-sea.md` | SG, MY, TH, ID, PH, plus VN as origin and as the destination of returns |
| `06-regulations-apac.md` | CN, HK, TW, JP, KR, IN, AU, NZ |
| `07-regulations-rest-of-world.md` | US, CA, EU, UK, CH, NO, GCC, BR, MX, CL, CO, ZA, NG |

Source labels used throughout: **[published]** carrier or authority
document, **[calculator]** public quote tool, **[estimate]** inference or a
third-party report. **UNVERIFIED** means it could not be confirmed. Several
carrier sites block automated reads, and the search budget ran out in three
of the seven slices, so the UNVERIFIED items are the first ones to check.

## 1. Main findings

1. **Reaching every country is not the problem; one integrator solves it.**
   DHL Express, FedEx and UPS ship DDP from VN to more than 200 countries
   through a REST API [published]. What limits where a merchant can sell is
   our **tax registrations and product rules per country** (§6), not the
   courier network.
2. **The integrators are too expensive for typical SEA apparel orders.** At
   list price a 0.5 kg parcel to SEA costs about US$47–64 before fuel. The
   DHL VN fuel surcharge is 50%, and the duty-paid fee is about US$23 per
   shipment at DHL and UPS [published]. Even with an estimated 30–50%
   discount, this is too much for a US$30–60 order. **SEA needs a cheaper
   consolidated DDP line** (estimated US$4–9 for a small parcel).
3. **The cheap SEA lines from VN have thinned out.** Ninja Van stopped its
   VN express business on 2025-09-30. DHL eCommerce left VN in 2021. LEX
   stopped VN last mile on 2026-03-31. Flash has no VN origin and left MY in
   January 2026 [published]. The remaining candidates (Janio, Boxme, J&T
   International) sell DDP only on contract, have thin or unconfirmed API
   docs, and publish no rates. Whether VN is a live origin for Janio is
   UNVERIFIED.
4. **Shipping orchestrators add nothing from VN today.** Easyship, EasyPost,
   Shippo, ShipStation API and AfterShip all work from VN only with our own
   DHL, FedEx or UPS accounts (bring your own account) [published]. They
   sell no VN rates and offer no SEA regional carriers from VN. They only
   become useful once merchants outside VN join.
5. **We should own the tax logic and rent only the duty data.** SG GST,
   MY LVG, AU/NZ GST, UK VAT, EU IOSS and NO VOEC are taxes we collect
   ourselves as registered seller. The per-country modules handle these. A
   vendor is needed only for border duty rates and HS classification.
6. **The rules on low-value imports tightened almost everywhere in
   2025–2026, and liability moved to the seller, which is us:**
   - TH removed its de minimis on 2026-01-01.
   - ID charges 25% duty plus VAT and income tax on apparel.
   - The US de minimis ended (indefinitely since 2026-06-24) and a 12.5%
     Section 301 duty applies to VN goods since 2026-07-24.
   - The EU charges €3 per item from 2026-07-01, plus a €2 handling fee from
     2026-11-01.
   - The UK duty relief ends by October 2028.
   - JP moves its tax to the seller in April 2028.
7. **Indonesia is blocked until an adviser has looked at it.** Permendag
   31/2023 sets a minimum FOB price of US$100 per unit for direct
   cross-border e-commerce sales. If it applies to us, most of the range is
   blocked. A foreign e-commerce operator with more than 1,000 parcels a
   year must also appoint a representative in ID.
8. **Returns cannot go back to VN one parcel at a time.** Since 2025-02-18
   VN taxes even small express imports, and duty-free re-import needs an
   A31 file per original export. Workable instead: refund without return
   below a threshold, and a hub in the destination region with monthly
   consolidated return shipments above it.

## 2. Recommended setup (to confirm)

| Layer | Recommendation | Why | Alternative |
|---|---|---|---|
| SEA economy DDP | **One SEA cross-border operator**: ask Janio first, then Boxme, then J&T International | The only route to a US$4–9 parcel; all three claim the five SEA markets | Integrator only, accepting about US$25–40 per parcel |
| Express and rest of world | **One integrator**, chosen on quotes from all three in the same week. FedEx is easiest to integrate (signed webhooks, published VN rates, FICP economy tier, third-party billing so a foreign entity can pay for VN exports). DHL has the strongest Asia network but no tracking webhooks for Express, so we would poll. | Gives every merchant technical reach to about 220 countries from day one | UPS as a second integrator: complete published VN rate tables, easiest to model |
| VN first mile | Only if the chosen operator does not collect from merchants: **GHN** (good API, domestic only) | Merchants are spread across provinces | GHTK, Viettel Post |
| Orchestrator | **None for now.** Revisit Easyship when VN origin goes live there, or when merchants outside VN join | Adds no rates from VN, and hides duty billing we must reconcile | — |
| Tracking aggregator | None at first. Add 17TRACK or AfterShip Tracking once we have more than 3 carriers | Main carriers push or are polled directly | — |
| Landed cost | Our own per-country modules for taxes we collect. **Easyship Taxes & Duties API** (US$0.09–0.11 per call) or **Zonos without its guarantee** (US$2 per order) for border duty | The Zonos guarantee makes Zonos the tax remitter, which conflicts with us being the merchant of record | Dutify (US$75 a month) as a cheap second opinion |
| HS codes | Propose with Zonos Classify (1,000 free) or Easyship HS lookup; the merchant confirms; we store per SKU with source and confidence | The merchant guarantees accuracy (§6.5 of the PSP plan) | — |
| Returns | Refund without return below a threshold (about US$40, or when return shipping is more than 40% of the price). Above it: a local return address or hub (Janio, Boxme or ZigZag), then a monthly consolidated shipment to VN or local liquidation. Integrator return labels for high-value goods and for outside SEA | Single return parcels to VN cost US$15–40 and need customs paperwork each | Asendia e-PAQ Returns (local return addresses) for the EU, UK and US later |

Why one module per provider still holds: each provider above is one courier
module behind the interface from §6.5 of `plan-psp-integration.md`. One
place chooses a provider per lane, from `(origin, destination, weight band,
service level)`. An orchestrator, if we add one later, is one more module,
and its quotes carry the underlying carrier as data. One carrier account
belongs to exactly one module.

## 3. Provider landscape

| Type | Providers checked | From VN today | Fit |
|---|---|---|---|
| Global integrators | DHL Express, FedEx, UPS | Yes, DDP, REST API, sandbox (DHL test 500 calls a day) | Express and rest of world. Expensive in SEA |
| Integrator economy tiers | FedEx International Connect Plus (since 2025-02), UPS Worldwide Economy (US only) | FICP yes; UPS WWE US only | FICP barely cheaper than International Priority at low weights |
| SEA cross-border operators (4PL) | Janio (SG), Boxme (VN) | Claimed; VN as origin UNVERIFIED for Janio. Boxme does DDP "upon agreement" | Main SEA candidates, RFQ needed |
| SEA regional carriers | J&T International (VN), Ninja Van, Flash, SPX, LEX, Kerry VN, BEST VN | J&T VN to SG, MY, TH, PH, **not ID**. Ninja Van and LEX exited VN. SPX and LEX only inside their marketplaces. Flash none | J&T: RFQ for DDP and API. Ninja Van: possibly last mile behind a 4PL |
| E-commerce postal | VN Post EMS, Asendia, SingPost, Cainiao, YunExpress, 4PX | EMS is duties-unpaid and has no international API. Asendia sells in VN with DDP. The others are unconfirmed or unlikely | Asendia for EU, UK and US economy and returns (RFQ). YunExpress VN worth an RFQ |
| VN domestic | GHN, GHTK, Viettel Post | Domestic only, good APIs | First mile |
| Orchestrators | Easyship, EasyPost, Shippo, ShipStation API, AfterShip Shipping | Only with our own DHL, FedEx or UPS account | Not now |
| Regional aggregators | EasyParcel (MY), Biteship and Shipper (ID), Sendcloud and nShift (EU) | No VN origin | Only for domestic return legs or later origins |
| Tracking | AfterShip Tracking, 17TRACK, Parcel Perform | Track VN carriers | Later |
| Landed cost | Zonos, Easyship T&D, Avalara, Dutify, SimplyDuty, Hurricane, Descartes | n/a | Easyship or Zonos (estimate mode) |

### 3.1 VN → SEA lane matrix

| Provider | SG | MY | TH | ID | PH | DDP | API for international from VN |
|---|---|---|---|---|---|---|---|
| Janio | claimed | claimed | claimed | claimed | claimed | "duty handling per country" | REST API, docs on request (old docs host is broken) |
| Boxme | claimed | claimed | claimed | claimed | claimed | "upon agreement" | Open API v2 (markets PH, TH, VN); static webhook token |
| J&T VN International | yes | yes | yes | **no** | yes | UNVERIFIED | none public (web portal) |
| DHL Express, FedEx, UPS | yes | yes | yes | yes | yes | yes, duty-paid fee | yes |
| VN Post EMS | yes | yes | yes | yes | yes | no (duties unpaid) | none international |
| Ninja Van, DHL eCommerce, LEX | exited VN | | | | | | |

## 4. Cost

### 4.1 Per parcel

Chargeable weight decides the price above about 1 kg. Integrators use a
volumetric divisor of 5000 and round up to 0.5 kg. EMS uses 6000.

| Parcel | Actual | Billed (÷5000, 0.5 kg steps) | Billed (÷6000) |
|---|---|---|---|
| XS 20×15×3 | 0.25 kg | 0.5 kg | 0.25 kg |
| S 25×20×5 | 0.5 kg | 0.5 kg | 0.5 kg |
| M 30×20×10 | 1 kg | **1.5 kg** | 1.0 kg |
| L 35×25×15 | 2 kg | **3.0 kg** | 2.5 kg |
| XL 40×30×20 | 5 kg | 5.0 kg | 5.0 kg |

Merchants should flat-pack apparel in poly mailers. We should ask every
partner for actual-weight or ÷6000 billing up to 2 kg.

UPS Worldwide Express Saver from VN, **list price** before discount, fuel
and VAT [published, 2025 guide; expect about +6% for 2026]:

| Billed weight | SG, TH, HK | MY, ID, PH | CN, JP, KR, TW, AU, NZ | IN | US, CA | EU, UK, CH | GCC | LatAm | Africa |
|---|---|---|---|---|---|---|---|---|---|
| 0.5 kg (XS, S) | $58 | $64 | $70 | $74 | $90 | $85 | $94 | $100 | $111 |
| 1.5 kg (M) | $71 | $77 | $92 | $94 | $128 | $124 | $137 | $144 | $161 |
| 3.0 kg (L) | $89 | $96 | $126 | $125 | $183 | $178 | $203 | $211 | $234 |
| 5.0 kg (XL) | $112 | $119 | $169 | $168 | $256 | $242 | $290 | $300 | $323 |

FedEx International Priority 0.5 kg promo rates for VN payers are about
US$47 (SG) to US$90 (GCC, Africa) [published 2026, read from search
extracts of the PDFs; open them to confirm]. DHL VN publishes no rate card.

Add for integrators: fuel (DHL VN 50% in November 2026), a duty-paid fee of
about US$23 per shipment (DHL: VND 600,000 or 2% of duties, whichever is
higher; UPS: VND 600,425), residential delivery (DHL VND 150,000), and
remote-area surcharges. Discounts at about 500 parcels a month are
estimated at 30–50% off list, and 60–75% at 5k or more [estimate].

**Open question that could save the US$23:** on lanes where nothing is
collected at the border because we charge the tax at checkout (SG and MY
once registered, AU, NZ, UK ≤ £135, NO, HK), is the duty-paid service and
its fee needed at all? Ask each integrator (UNVERIFIED).

SEA consolidated DDP line, **estimate only** (no provider publishes VN→SEA
rates), before duties and tax:

| Parcel | SG, MY, TH | PH, ID |
|---|---|---|
| XS, S | US$4–7 | US$5–9 |
| M | US$6–10 | US$8–13 |
| L | US$10–16 | US$13–22 |
| XL | US$18–28 | US$22–38 |

Janio adds a platform fee from US$0.50 per shipment [published].

### 4.2 Integration and software

| Item | Cost |
|---|---|
| Courier module with a real REST API (quote, label, tracking, duty invoice import, return label) | 8–15 engineering days each [estimate] |
| Partner without real API docs (CSV or portal, manual reconciliation) | add 5–10 days [estimate] |
| VN first-mile module (GHN, GHTK) | 3–5 days [estimate] |
| Integrator platform fees | none [published] |
| Janio Platform Access | from US$0.50 per shipment, no minimum, month to month; Enterprise "typically US$10k+ a month" at 50k+ parcels [published] |
| Easyship Taxes & Duties API | US$0.09–0.11 per call, about US$55 a month at 500 orders [published/estimate] |
| Zonos Landed Cost | US$2 per order (estimate mode); guarantee adds 10% of duties and tax [published] |
| Zonos Classify | 1,000 free; from US$500 a month for 10,000 [published] |
| Orchestrator, if ever | US$0.05–0.15 per label at 500 a month, under 2% of shipping cost [estimate] |
| 17TRACK | about CNY 299 a year for 10,000 numbers [UNVERIFIED] |

## 5. Speed (business days, door to door)

| Destination | Integrator express | FedEx FICP / UPS Expedited | SEA consolidated line | VN Post EMS |
|---|---|---|---|---|
| SEA | 1–3 | 3–5 | 3–9 [estimate]; Boxme publishes 5–15 | UNVERIFIED |
| East Asia | 1–3 | 3–5 | — | UNVERIFIED |
| India, AU, NZ | 2–4 | 3–6 | — | 4–8 (AU) |
| US, CA, EU, UK | 2–4 (DHL claims 2–3) | 3–7 | — | 3–12 |
| GCC, LatAm, Africa | 2–7 | 4–10 | — | UNVERIFIED |

Integrator express times are mostly estimates. UPS publishes "end of day
guaranteed" for Saver and "as little as 3 days within Asia" for Expedited.
No VN→SEA operator publishes an SLA.

## 6. Customs and regulations by destination

Ease for us as a foreign merchant of record selling DDP B2C apparel from
VN. "Register" means we collect the tax at checkout and file returns
ourselves. Every row becomes an effective-dated rule in its per-country
module.

### 6.1 SEA

| Country | Ease | Duty / tax at the border | Our registration | Notes |
|---|---|---|---|---|
| SG | Easy | No duty on apparel. GST relief up to S$400 CIF by air | OVR above S$1m global and S$100k to SG: 9% GST, quarterly | Threshold per item, on sales value excluding shipping, but GST is charged on the price including shipping. Our GST number and a "GST-paid" flag must reach the courier, or Customs taxes the parcel again |
| MY | Easy | Free up to RM 500 CIF per consignment (air courier) | LVG above RM 500k in 12 months: 10%, quarterly | LVG number on the consignment note. Per item or per order: UNVERIFIED |
| TH | Medium | **No de minimis since 2026-01-01** (Notification 219/2568): duty up to about 30% on apparel plus 7% VAT | none for goods | A plan to raise duties to 30–40% and a possible flat parcel rate are pending. ATIGA 0% is worth claiming |
| PH | Medium | Free up to PHP 10,000 FOB; informal entry up to PHP 50,000; accredited importer above | none for goods | CAO 02-2025 is a fee schedule, not a VAT ruling (corrects our earlier note). Pressure to scrap the de minimis |
| ID | **Hard, blocked** | Duty free under US$3 only. Apparel, bags and shoes: **25% duty + 11% effective VAT + 5% income tax**, about +45–50% | none, but a foreign e-commerce operator with more than 1,000 parcels a year must appoint a representative | **Permendag 31/2023: minimum FOB US$100 per unit** for direct cross-border sales. Halal certification for clothing, headwear and accessories from 2028-10-17 (moved by PP 40/2026; leather goods, footwear and bags unconfirmed). Used clothing banned |

ATIGA preference (0% on VN-origin goods): consignments up to FOB US$200
need only a simplified origin declaration, no Form D. This only applies to
goods actually made in VN, so it is decided per merchant and per SKU.

### 6.2 Rest of Asia-Pacific

| Country | Ease | Key rules |
|---|---|---|
| HK | Easy | No duty or tax on apparel |
| AU | Easy | No duty or border GST up to A$1,000. We register above A$75k (simplified, quarterly, our ARN on the documents) |
| NZ | Easy | No duty up to NZ$1,000 per item. We register above NZ$60k and collect 15% |
| TW | Medium-easy | Free up to NT$2,000, at most 6 per buyer per half-year. Buyers confirm through the EZ WAY app. A reform is under review |
| KR | Medium-easy | Free up to US$150 including shipping; above that the whole value is taxed. **PCCC mandatory** per parcel, with name, phone and postal code matching (2026). Risk: customs may combine orders from different merchants because we are one supplier |
| JP | Medium | Free up to ¥10,000, **but knitwear, leather bags and leather shoes are taxed at any value**. From 2028-04-01 we register and charge 10% JCT (likely through a tax agent) |
| IN | Hard | No de minimis for sales; about 30–45% landed on apparel; consignee KYC (avoid storing Aadhaar) |
| CN | Hard | The cross-border e-commerce channel needs a registered platform, a Chinese agent and Chinese payment providers, plus the buyer's ID (PIPL). Personal parcels are taxed at 20% and are not meant for sales |

### 6.3 Rest of the world

| Market | Ease | Key rules | Entity matters? |
|---|---|---|---|
| UK | Easy, Medium from 2028 | We charge VAT on orders up to £135, registering from the first sale. No duty until October 2028 at the latest | No |
| CH | Easy | 0% duty on apparel since 2024. VAT through the courier until CHF 100k, then registration and a tax representative | No |
| NO | Easy | VOEC: 25% VAT at checkout, no representative | No |
| CL | Easy-medium | 19% VAT at checkout up to US$500 since 2025-10-25, simple registration, no duty | No |
| EU | Medium | IOSS up to €150. **€3 duty per tariff line** from 2026-07-01 to 2028-07-01, then full duty (12% on apparel; EVFTA 0% only with VN or EU fabric). **€2 handling fee** from 2026-11-01. GPSR responsible person, textile EPR (FR and NL now, all EU countries by 2028-04) | **Yes: an NL BV needs no IOSS intermediary and can itself be the GPSR responsible person and EU importer** |
| CA | Medium | Duty and tax from CAD 20; 17–18% apparel duty; CPTPP hard to claim (yarn-forward rule); bilingual labels | No |
| UAE, SA | Medium | 5% duty; courier DDP works; VAT registration for a non-resident unclear | No |
| CO | Medium | Courier regime, changes often | No |
| US | Hard | **No de minimis** (indefinite since 2026-06-24). MFN 16–32% **plus 12.5% Section 301** on VN goods since 2026-07-24, about 30–45% in total. Another Section 301 determination due **2026-11-29**. From 2027-01-20 VN goods by post need formal entry, so express only | No |
| BR | Medium-hard | 0% federal tax up to US$50 (law since 2026-09-10) but 17–20% state tax; needs Remessa Conforme certification through a partner | No |
| MX | Hard | 33.5% flat rate on courier imports; NOM labelling needs a Mexican importer tax ID | No |
| ZA | Hard | About 40–45% duty on clothing | No |
| NG | Not viable | Garments appear on the import prohibition list | No |

### 6.4 Suggested market order

Combining logistics cost, rules and our registrations:

1. **Launch:** SG, MY, PH, HK (SEA line or integrator), plus AU, NZ, UK,
   CH, NO through the integrator. Rules are clear, and below the
   registration thresholds nothing has to be filed yet.
2. **Next:** TH (high duty but clear), TW, KR (PCCC field), JP (watch the
   exclusions; register before 2028-04), CL, CA.
3. **With the entity decision:** EU (NL BV strongly preferred).
4. **Express only, priced carefully:** US (re-price after 2026-11-29).
5. **After advice or through a partner:** ID, BR, IN, CN, MX, ZA. Not NG.

So a merchant can sell to "all customers": the integrator covers every
country in steps 1–4 from day one. What remains closed is closed for legal
reasons, and checkout should say so per country.

## 7. Returns

| Model | When | Cost |
|---|---|---|
| Refund without return | below about US$40, or return shipping more than 40% of the price; photo evidence; cap per buyer | the goods |
| Local return address, then a hub | above the threshold, SEA | about US$4–10 per item [estimate] |
| Monthly consolidated shipment to VN under A31 (duty-free re-import of own exports, Decree 134/2016 Art. 33), or local liquidation | from the hub | VAT on re-import UNVERIFIED |
| Integrator return label to VN | high-value goods; outside SEA | US$15–40+ per parcel [estimate] |

What we refund: the goods price plus the tax we collected ourselves (SG and
MY by credit note, AU/NZ/UK/IOSS by adjustment on the next return).
Duties paid at the border are kept only on voluntary change-of-mind
returns where that was disclosed before purchase. Correction 2026-11-06
(see `research-product-compliance.md` §5): a withdrawal in the EU, UK, NO,
TW and probably KR, and every refund for defective goods, must include
duties and tax. Global-e also refunds duties to shoppers and charges them
back to the merchant. Recovering border duty is
only worth it in batches: US drawback 99%, KR re-export within one year, JP
for wrong or defective goods. Planning figure: 8–15% return requests for
SEA apparel, many settled without the item coming back [estimate].

Consumer law on returns per country binds **us**, because we are the
seller. See `research-product-compliance.md` §5.

## 8. What this means for our design

- **Courier interface** (§6.5 of the PSP plan) confirmed: quote, create
  with label, cancel, tracking, duty and shipping invoice import, return
  label. Some carriers push events (FedEx with HMAC-signed webhooks, Ninja
  Van, GHN) and some must be polled (DHL Express). **A poll worker must
  produce the same inbox messages as a webhook**, idempotent on the
  carrier's event id.
- **The shipment carries our tax IDs per destination:** SG GST number plus a
  GST-paid flag per item, MY LVG number, AU ARN, NZ IRD number, UK VAT, EU
  IOSS, NO VOEC. If any is missing, the parcel is taxed a second time at the
  border.
- **Product data per SKU:** HS code (8 digits where possible) with source
  and confidence, country of origin, the facts behind an origin claim
  (fabric origin), fibre content, knit or woven, leather or not (JP
  exclusions, ID halal), restricted-goods category.
- **Buyer fields per country:** KR PCCC (format check, exact name, phone,
  postal code), ID NIK (UNVERIFIED), TW EZ WAY notice, IN KYC left to the
  courier. These are personal data under PIPA, PIPL, DPDP and the ID and VN
  laws: compliance flags, do not assume an answer.
- **Threshold tests differ per country:** per item or per consignment,
  including or excluding shipping, CIF or FOB, all-or-nothing (KR). Each one
  is an effective-dated rule.
- **Turnover tracking across all merchants** per jurisdiction, for the SG,
  MY, AU, NZ, CH and NO registration thresholds.
- **Ledger:** the difference between the duty quote and the courier's duty
  invoice goes to `duty_variance`. Also book DDP admin fees, return freight,
  refunds without return, and later any duty recovery, each as its own
  entry.
- **Returns state machine:** requested → approved → in_transit →
  received_at_hub → refunded | refunded_without_return, driven by messages
  and idempotent per return id.
- **Entity and exporter of record (compliance, do not assume):** the
  merchant physically exports from VN, but we are the seller and the payer
  of duties. Whose name goes in the shipper field and on the commercial
  invoice? Can an SG or NL entity hold VN export accounts directly? FedEx
  publishes third-party billing for VN; DHL and UPS UNVERIFIED.

## 9. Next steps: quotes to request

Send in the same week so the rates can be compared:

| Provider | Ask |
|---|---|
| Janio | Is VN a live origin? Carriers per lane? DDP with duties billed to us and one duty invoice per shipment? API docs, sandbox, webhook signing? Rates for the five reference parcels to the five SEA countries? Local return addresses? |
| Boxme | Same, plus MY, SG and ID coverage, and destination warehouses for returns |
| J&T VN / J&T International | Is ID really unavailable? Is there a DDP line and an API for international orders? |
| DHL Express VN, FedEx VN, UPS VN | Discounted rates at 500, 5k and 50k parcels a month; whether the duty-paid fee applies on lanes with nothing to collect; duty invoice format (EDI/CSV); fields for our IOSS, UK VAT, AU ARN and SG GST numbers; whether a foreign entity can hold the account; DHL push tracking |
| Asendia VN, YunExpress VN | Economy DDP to EU, UK and US; returns |
| Zonos | Will they sell landed cost in estimate mode at US$2 per order with our own tax IDs and no remittance by Zonos? |

## 10. Questions for advisers

1. **ID:** does Permendag 31/2023 (US$100 minimum) apply to an offshore
   merchant of record selling through its own checkout? The representative
   rule, NIK, ATIGA for parcels, halal scope.
2. **TH:** status of the 30–40% duty plan and of the seller registration
   bill; Thai labelling for cross-border B2C.
3. **PH:** is VAT collected below PHP 10,000 in practice?
4. **MY:** is the RM 500 LVG test per item or per order?
5. **SG:** SG entity vs NL BV for GST (local registration vs OVR).
6. **VN:** 0% export VAT evidence when merchants sell to our foreign
   entity; VAT on re-imported returns; who declares a consolidated return
   from several merchants; status of the e-commerce customs decree.
7. **EU:** the choice of entity (NL BV favoured); refund of the €3 and €2 on
   returns; EPR registrations.
8. **US:** importer of record under carrier DDP; the Section 301 result due
   2026-11-29; state sales tax nexus as a remote seller.
9. **JP:** registration and tax agent for 2028-04-01.
10. **All:** consumer-law return rights per country when we are the seller;
    data-protection rules for buyer IDs (KR, CN, IN, ID).

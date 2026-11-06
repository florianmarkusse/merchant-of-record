# Research: product rules, consumer law and liability

Status: desk research, 2026-11-06. No decisions in this file yet. It
follows `docs/research-logistics.md`, which covers customs, tax and
couriers. This file covers what a product must meet to be sold to a
consumer in each destination, the consumer law we owe as seller, and our
liability. The visual summary is in `docs/product-compliance.html`.

Scope (agreed 2026-11-06): adult apparel, footwear, bags and leather
goods, fashion jewellery and accessories (belts, hats, scarves,
sunglasses). Children's clothing is flagged only. Cosmetics, fragrance and
anything with a battery are out of scope. Deep on the launch markets (SG,
MY, PH, HK, AU, NZ, UK, CH, NO) plus the EU and US; lighter on the rest.

The detailed notes with every source are in `docs/research/product-compliance/`:

| File | Covers |
|---|---|
| `01-eu-uk-ch-no.md` | GPSR, REACH, textile and footwear labelling, EPR, ESPR, green claims, forced labour; UK, CH, NO |
| `02-us-ca.md` | CPSC and eFiling, FTC Textile, Wool and Care rules, Prop 65, state PFAS bans, UFLPA; Canada |
| `03-sea-vn.md` | SG, MY, TH, ID, PH, HK, and VN as origin |
| `04-apac-oceania.md` | AU, NZ (deep); JP, KR, TW (medium); CN, IN (light) |
| `05-materials-ip-row.md` | CITES, fur, hallmarking, sunglasses, heavy metals, counterfeits, courier rules, forced labour; GCC, LatAm, ZA |
| `06-consumer-law-liability.md` | Withdrawal rights, guarantees and price display per market; product liability; how Global-e splits duties with merchants |
| `07-keycaps-ceramics-lacquer.md` | Added 2026-11-06 for the candidate merchants: resin keycaps and keyboards, ceramic tableware, lacquerware and wooden goods (short pass) |

**How reliable this is.** The search budget shared between the six
researchers ran out early (after 4 to 20 searches each). After that the
researchers fetched official pages directly where they could, and
otherwise wrote from their own knowledge of the laws. Those points are
marked UNVERIFIED or [K] in the notes. This affects most of all: Thailand,
Malaysia, Hong Kong, Korea, Taiwan, China, India, the GCC, Latin America,
South Africa, the US state PFAS laws, Prop 65 detail, and most per-market
consumer law. The points confirmed against a primary source are named in
§1. Everything here needs counsel before launch, as listed in §10.

## 1. Main findings

1. **Most of the product duties land on us.** As the seller, and usually as
   the importer, we are the party that consumer and product-safety law
   reaches in nearly every market. The VN merchant's duties to us are
   contractual. Confirmed for: AU (ACL reaches foreign online sellers;
   ACCC), PH (Internet Transactions Act covers foreign merchants and makes
   them primarily liable), SG (CGSR names any "supplier", including the
   seller), US (FTC: the importer and seller own textile labelling), CA
   (CCPSA puts incident reporting on the importer when the maker is
   abroad), EU (GPSR importer duties if a Dutch BV imports).
2. **One design decision drives most of the rest: who is importer of
   record.** Being importer of record (IOR) means being the party customs
   records as the importer.
   - **If we are the IOR**, we are the US CPSA "manufacturer" (we certify
     and eFile), the EU importer and GPSR responsible person (Dutch BV
     only), the "importer" for SEA labelling rules, and the strictly liable
     importer for product liability in the EU, UK, AU, CH, NO, JP, KR, TW
     and TH.
   - **If the buyer is the IOR** (Global-e's model: the shopper is
     importer, Global-e is exporter), several of these tiers may be empty.
     We still owe consumer law, listing duties and textile labelling as
     the seller.
   - The researchers lean towards owning the IOR role in the US and Canada,
     because the duties follow us anyway. For the EU it depends on the
     entity. This needs counsel (§10).
3. **Our planned returns rule is unlawful in several markets.** Not
   refunding border duties is fine only for voluntary change-of-mind
   returns that were disclosed beforehand. In the EU, UK, NO and TW, and
   very likely KR, a withdrawal must refund everything the buyer paid,
   duties and tax included. Every market requires a full refund for
   defective or misdescribed goods. Returnless refunds and local hubs are
   lawful everywhere. This corrects §7 of `research-logistics.md`.
4. **Some categories are hard enough to gate at launch:**
   - Excluded: children's clothing, CITES-listed exotic leathers (python,
     crocodile), real fur, ivory and similar.
   - Gated per SKU: sunglasses (separate certificates for the EU, AU and
     US), and precious-metal wording on jewellery.
   - Allowed with lab reports: jewellery and metal parts (nickel, lead,
     cadmium) and leather (chromium VI, azo dyes).
5. **Cotton from Vietnam is a US forced-labour risk.** VN apparel often
   uses Chinese yarn, and VN is among the top origins of UFLPA detentions.
   The importer must prove the cotton's origin from bale to garment. The
   EU Forced Labour Regulation follows from 2027-12-14.
6. **The EU is the heaviest market and the Dutch BV is the simpler route
   there.** A BV is itself the GPSR responsible person and importer. An SG
   entity would need each VN merchant to appoint an EU representative.
   The EU also brings:
   - REACH chemical limits, with the PFHxA restriction from 2026-10-10;
   - French and Danish PFAS bans;
   - textile and packaging producer registrations (Germany requires
     registration before the first parcel);
   - the ban on unproven green claims from 2026-09-27;
   - the withdrawal button since 2026-06-19.
7. **Launch markets (SG, MY, PH, HK) have light product rules but real
   seller duties:**
   - PH: registration with the E-Commerce Bureau.
   - SG and MY: price and identity disclosures.
   - AU: a care label on apparel and injury reporting within 2 days.
   - NZ: care, fibre and origin labels.
   - TH: may need Thai labels and a 7-day (possibly 14-day) withdrawal
     right.
   - JP: needs a Japan-based "labeler", a grey area for us.
8. **Liability: copy Global-e's contract split, then harden it.** The
   merchant warrants the product and its data, indemnifies us, carries
   insurance, and pays refunds and chargebacks through payout deductions.
   Small VN merchants make an indemnity weak, so we add a rolling reserve,
   arbitration enforceable in VN, and our own product liability insurance.

## 2. The importer of record decision

| Topic | We are IOR | Buyer is IOR (carrier clears for the buyer) |
|---|---|---|
| US CPSC certificate and eFiling | We certify and file | Legally unclear for a DDP seller; the carrier still asks for CPSC fields |
| US Textile Act, Prop 65 | Ours as importer and seller | Ours as seller |
| US UFLPA proof | Ours | Still blocks the parcel; we carry the loss |
| EU GPSR responsible person, REACH importer | Ours with a Dutch BV; impossible with an SG entity | Unclear; the EU representative becomes the target |
| EU, UK, AU product liability | Strictly liable as importer | Liable as distributor or supplier if we cannot name the maker within 1 month (EU) or 30 days (AU) |
| SEA label duties (TH, ID) | Ours | The buyer's, in practice unenforced |
| Canada CCPSA incident reports, records | Ours (records "at a Canadian place of business": need an exemption or agent) | Unclear |
| Customs-side setup | US Form 5106 and bond, CA business number, EU EORI | Simpler |

Whichever way we decide, keep per SKU the manufacturer's legal name and
address, the merchant, and the batch or lot. Then we can always name the
manufacturer within the EU (1 month) and AU (30 days) deadlines and avoid
being treated as the producer by default.

## 3. Catalogue gating at launch

| Category or attribute | Rule at launch | Why |
|---|---|---|
| Children's clothing (under 13–14) | **Exclude** | US CPSIA third-party testing per SKU; drawstrings, flammability and small parts everywhere |
| CITES-listed materials (python, crocodile, cobra, coral, giant clam) | **Exclude** | A VN export permit per shipment, an EU import permit, a US wildlife licence; couriers need pre-approval |
| Real fur, including trims | **Exclude** | California and Israel sale bans; FedEx and UPS restrict it; labelling rules |
| Ivory, tortoiseshell, shark, whale, migratory-bird feathers | **Hard block** | Banned nearly everywhere |
| Sunglasses | **Gate per SKU and market** | EU: CE mark under the PPE rules (EN ISO 12312-1) and an EU operator. AU: AS/NZS 1067.1 mandatory standard. US: FDA medical device with drop-ball test and registration. Without the document, block that market for the SKU |
| Jewellery and metal parts | Allow with a lab report per material | EU nickel release, lead and cadmium; California jewellery law and Prop 65; SG lists ASTM F2999 |
| Leather | Allow with a lab report per supplier | EU chromium VI under 3 mg/kg; azo dyes |
| Precious-metal words ("gold", "silver", "925") | Allow only with fineness and weight; otherwise "gold-tone" or "plated" | UK Hallmarking Act and similar rules in IE, NL, FR and others; US FTC Jewelry Guides |
| Water-repellent finishes | PFAS-free declaration; test DWR items; block PFAS-treated SKUs from US, FR, DK | EU PFHxA from 2026-10-10; FR and DK bans; US state bans |
| Cotton-bearing items to the US | Supply-chain declaration and documents on request | UFLPA |
| Third-party brands | Allow only with proof of ownership or authorisation | As seller and importer we are the direct infringer; card-scheme brand programmes put our acquiring at risk |
| "Dupe", "inspired by", "replica" wording; unproven green claims | Block in listing copy | Trademark use; EU ban on generic green claims from 2026-09-27 |

## 4. Rules per market

### 4.1 Launch markets

| Market | Product rules | Seller duties | Withdrawal right |
|---|---|---|---|
| SG | CGSR: post-market general safety. Apparel and costume jewellery named; ASTM F2999 for jewellery. Test reports on request | All-in price (no drip pricing); true trade descriptions | None online |
| MY | No mandatory certification found; Bahasa labels expected for domestic goods (UNVERIFIED) | Electronic Trade Transactions Regulations 2012: identity, total price, delivery estimate, error correction | None |
| PH | Nothing in our categories on the mandatory certification list | **Internet Transactions Act**: publish name and address, submit them to the E-Commerce Bureau; merchant primarily liable; defect returns at no cost to the buyer | None (paid orders generally cannot be cancelled) |
| HK | General safety requirement; warnings in English and Chinese; gold and platinum marking if claimed (UNVERIFIED) | Trade Descriptions Ordinance | None online |
| AU | **Care Labelling Standard 2023** for apparel, scarves and leather garments (ISO 3758 or AS/NZS 1957 since 2025-03-05; footwear, hats, belts and bags exempt). **Sunglasses standard** AS/NZS 1067.1. PFOS, PFOA and PFHxS banned from 2025-07-01 | **Report serious injuries within 2 days**, including overseas incidents. Statutory guarantees cannot be excluded; say "your rights are unaffected" | None online; guarantees for defects |
| NZ | **Care, fibre content and country of origin labels** on clothing; origin on footwear; English, letters at least 1.5 mm | Recall notice within 2 working days; Consumer Guarantees Act | None online |
| UK | GB on the 2005 product safety rules; no UK responsible person for ordinary goods. UK REACH without PFHxA. English fibre labels. Hallmarking. Packaging EPR only if we get a UK branch, warehouse **or returns address** | DMCC Act: all-in prices, fake reviews banned | 14 days; duties refunded |
| CH | Safety law plus contact-material limits like the EU; fur declaration mandatory; fibre labels voluntary | Identity, CHF prices including VAT | None online; 2-year warranty |
| NO | REACH and textile labelling via the EEA; GPSR not yet in the EEA; Norwegian labels | Norwegian expected | 14 days; up to 5 years for durable goods |

### 4.2 EU and US

| Market | Must do before selling |
|---|---|
| EU | Choose the GPSR responsible person: the Dutch BV itself, or an EU representative per merchant under an SG entity. On every listing: manufacturer and responsible person contacts, product id and photo, warnings, fibre composition in the local language, footwear material codes. RSL declaration and ISO 17025 lab reports per material (azo dyes, chromium VI, nickel, cadmium, lead, PAHs, phthalates, NPE, CMR substances, PFHxA from 2026-10-10; FR and DK PFAS bans). SVHC declaration (and SCIP as importer). Textile EPR: FR (Refashion) and NL (UPV Textiel) now, all member states by 2028-04-17. Packaging EPR under the packaging regulation from 2026-08-12, with Germany's LUCID registration before the first parcel. Block unproven green claims. 14-day withdrawal with the online withdrawal button. 2-year conformity. Forced Labour Regulation from 2027-12-14. Destruction ban on unsold apparel and footwear, which includes returns, for large companies since 2026-07-19 (we are small at first) |
| US | **CPSC eFiling** since 2026-07-08 per variant: a certificate reference, or Disclaim A (footwear, bags, jewellery, belts, hats) or B (adult apparel exempt from flammability testing). Lightweight silk, rayon and cotton (scarves) and raised-surface fabrics need a 16 CFR 1610 test and our certificate. Labels: fibre %, "Made in Vietnam", maker or our name, care. Every product page says "imported". Prop 65 warnings if we have 10+ employees. PFAS-free declarations (CA, NY, MN, ME, VT, CO). UFLPA cotton traceability. FTC Mail Order Rule for shipping promises. California fur ban |
| CA | Bilingual fibre labels with our name and address; "Made in Vietnam" marking; flammability basis; records kept 6 years in Canada (exemption or agent); incident notice in 2 days and report in 10; French for Quebec |

### 4.3 Rest of Asia and the world

| Market | Main points |
|---|---|
| TH | Garments, footwear and bags probably need a Thai label with importer details (UNVERIFIED). Direct marketing law: 7-day withdrawal and possible registration; a draft open until 2026-11-27 would make it 14 days and require a local coordinator for offshore merchants on platforms |
| ID | Still blocked (US$100 minimum price, see `research-logistics.md`). **Halal deadline for clothing, headwear and accessories moved to 2028-10-17** (PP 40/2026, enacted 2026-10-14); leather goods, footwear and bags unconfirmed. Parcels need no importer licence (Permendag 16/2025) |
| JP | Household Goods Quality Labeling Act: Japanese labels naming a labeler with a **Japanese address and phone**. Personal imports are outside it, but sales aimed at Japan may be inside: get a Japanese lawyer's view. Harmful-substances limits (formaldehyde in underwear and socks, azo dyes). Customs seizes counterfeits sent to individuals. SCTA seller page and final confirmation screen; without a displayed policy, an 8-day return at the buyer's cost |
| KR | Personal direct purchases are exempt from KC certification; authorities test and block specific products instead. 7-day withdrawal; 3 months for defects at our cost; refund within 3 business days |
| TW | Labelling and inspection bind domestic sale and infant textiles. **7-day no-reason withdrawal at our cost**; we collect within 15 days, refund within 15 days |
| CN, IN | CN: cross-border channel needs a domestic agent and a risk notice. IN: footwear needs a BIS licence held by the foreign manufacturer; listings must show origin; a grievance officer |
| GCC, BR, MX, CL, ZA | Not researched in depth (UNVERIFIED). Common pattern: fibre, origin, care and importer identity **in the local language on the article**; MX needs a Mexican importer tax ID; KSA has its own conformity scheme |

## 5. Consumer law: what the returns policy must allow

| Market | Minimum | Duties may be kept? |
|---|---|---|
| EU, NO | 14-day withdrawal from delivery. The buyer pays return postage only if told beforehand. Refund within 14 days including standard outbound shipping and all duty and tax in the price. EU withdrawal button. 2-year conformity at our cost (NO up to 5 years) | No |
| UK | 14-day cancellation; 30-day right to reject defective goods | No |
| TW | 7-day withdrawal, **we pay the return**; collect in 15 days, refund in 15 | No |
| KR | 7-day withdrawal (buyer pays postage); 3 months for defects (we pay); refund in 3 business days | Almost certainly no |
| TH | Probably 7-day cancellation, refund in 15 days; may become 14 days | Assume no |
| JP | Any policy, if displayed on the seller page and final screen; otherwise 8 days at the buyer's cost | Yes, if displayed |
| US, CA, AU, NZ, SG, MY, ID, PH, HK, CH | No change-of-mind right; post the policy before purchase; full refund for defects; in AU and NZ, say statutory rights are unaffected | Yes for change of mind if disclosed; no for defects |

Price display: the all-in DDP price must be the headline price in the EU,
UK, AU, SG and California. Showing duties only at the end of checkout is
drip pricing.

The choice between an SG entity and a Dutch BV does **not** change which
consumer law applies: the buyer's home rules apply whenever we target
their country.

## 6. Liability and the merchant contract

**Our exposure.** Where we are the importer we are strictly liable for
defective products in the EU (new Product Liability Directive, applies to
products placed after 2026-12-09), UK, AU (importer deemed manufacturer),
CH, NO, TH, JP, KR and TW. In the US any seller that holds title is
strictly liable, and "innocent seller" defences usually fail when the
maker is foreign. Fashion goods are low hazard; the risks are jewellery
metals, flammable fabrics, and strap or buckle failures.

**Global-e's split (terms v3, 2026-01-26).** The merchant warrants
compliance, labelling, origin and IP; is liable for product data errors;
indemnifies, including product liability; and carries worldwide product
liability insurance. Refunds including duties, and chargebacks, are
deducted from payouts. Global-e caps its own liability at 12 months of
fees. The shopper is importer of record.

**Recommended additions for small VN merchants:**
1. Arbitration in Singapore (SIAC), which VN enforces under the New York
   Convention.
2. A rolling reserve of 5–10% for 90–180 days, plus set-off rights.
3. Merchant product liability cover of at least US$1m, us as additional
   insured; or a master policy we buy and recharge per order.
4. The merchant answers traceability questions within 5 business days.
5. The indemnity survives termination for the EU's 10-year long-stop.
6. Our right to delist and block SKUs per market.

**Our own cover.** A seller's product liability policy with worldwide
territory including the US and Canada, about US$1–2m. A few thousand US$ a
year is an estimate, not a quote; get broker quotes.

**What we do ourselves**, whatever the contract says: the consumer
contract and every statutory remedy; merchant identity checks; category
gating; lab reports for jewellery and leather; counterfeit screening;
recall monitoring (EU Safety Gate, UK OPSS, CPSC, ACCC); incident intake
with the AU 2-day and CA 2/10-day deadlines; storing the returns-policy
version shown at checkout.

## 7. Product data to collect per SKU

| Group | Fields | Needed for |
|---|---|---|
| Identity | manufacturer legal name and address; merchant; model, batch or lot; photo; GTIN | GPSR listing, CPSC certificate, PLD and ACL identification deadlines, recalls |
| Category | product type; age grade (adult only at launch); is_sunglasses; has metal parts in skin contact; pierced post | gating, CPSC disclaim code, chemical tests |
| Materials | fibre % by component (closed list of fibre names, translated per market); fabric weight and surface (plain or raised); leather type (genuine, split, bonded, imitation); animal species for any animal material; real fur yes/no; footwear material codes (upper, lining, sole) | labels everywhere, US flammability exemption, EU footwear, CITES, fur bans, ID halal |
| Origin | country of manufacture; fabric country; operations done in VN; cotton origin by tier | "Made in Vietnam" claims (VN Decree 111/2021 and US marking), UFLPA, EU forced labour |
| Labels | care symbols (ISO 3758, JIS L 0001:2024, KS K 0021); label photos; Thai, Japanese or other local label text where required | AU, NZ, US, CA, JP, TH |
| Chemicals | RSL declaration versioned against the EU Candidate List; lab reports (lab, ISO 17025 accreditation, standard, date) per material and colour; PFAS-free declaration; Prop 65 decision and chemical name | EU REACH, US states, Prop 65, SG CGSR, JP |
| Certificates | CPSC certificate reference or disclaim code with intended-use code; sunglasses: EU DoC and EN ISO 12312-1, AS/NZS 1067.1 report, FDA registration and drop-ball certificate | US eFiling, sunglasses gating |
| Claims | brand and relationship (own, authorised) with evidence; precious metal, fineness and weight; gemstone claim; any green claim with its certificate | IP, hallmarking, green claims |
| Packaging | material and weight per packaging type | EU packaging EPR |

The catalogue computes **eligibility per SKU and market** from these
fields, the same way the tax modules decide per country. Each rule is
effective-dated and the version applied is stored.

## 8. What this means for our design

- **Catalogue:** the fields in §7 plus a computed `market_eligibility`; a
  closed fibre list with official translations; listing templates that
  render per-market disclosures (GPSR block, US "imported", fibre
  composition, Prop 65 for California addresses).
- **Checkout:** headline all-in DDP price; seller identity and the
  per-market policy pages (JP seller page, final confirmation screen);
  store the returns-policy version with the order.
- **Withdrawal and returns:** a server-rendered, no-login EU withdrawal
  flow ("withdraw from contract here" → "confirm withdrawal") with an
  emailed acknowledgement; deadlines computed from the carrier's delivery
  event; the refund amount computed per reason and market (withdrawal in
  EU/UK/NO/TW/KR includes duties; defects always everything; voluntary
  returns per policy). Refunded duties are booked as a loss or a
  receivable (drawback claim or merchant deduction), never dropped.
- **Incidents:** an intake with SLA timers (AU 2 days; CA 2 and 10 days;
  EU via Safety Business Gateway) and recall monitoring that can block
  SKUs.
- **Merchant onboarding:** identity checks, signed warranties, insurance
  certificate, document uploads per SKU, a rolling reserve setting.
- **US entries:** pass the CPSC value per variant to the carrier with each
  shipment.

## 9. Corrections to earlier documents

- `research-logistics.md` §6.1, Indonesia: the halal deadline for
  clothing, headwear and accessories is now **2028-10-17** (PP 40/2026),
  not 2026-10-17.
- `research-logistics.md` §7, returns: border duties may be kept only on
  voluntary change-of-mind returns that were disclosed; withdrawals in the
  EU, UK, NO, TW and probably KR, and every defect refund, must include
  them.

Both are updated in that file.

## 10. Questions for counsel

1. **Importer of record:** should we be the IOR in the US, CA, EU and AU,
   or leave the buyer as importer like Global-e? What does each choice do
   to GPSR, PLD, CPSA and SEA label duties?
2. **EU:** confirm the Dutch BV as GPSR responsible person; whether returns
   count as "unsold" under the destruction ban; whether bags fall under
   textile EPR.
3. **JP:** does the Household Goods Quality Labeling Act apply to a
   foreign seller with a Japanese storefront? Who can be the labeler?
4. **TH:** label-controlled goods and who is "importer" on a DDP parcel;
   direct marketing registration for a foreign seller; the 14-day draft.
5. **ID:** halal date for leather goods, footwear and bags; Permendag
   31/2023 (as in the logistics questions).
6. **US:** Prop 65 employee count for a foreign company; exact state PFAS
   dates; CPSC enforcement on express parcels; HTS headings where fabric
   origin decides "Made in".
7. **CA:** records "at a Canadian place of business"; whether a foreign
   address works on textile labels; Quebec French.
8. **KR:** whether a withdrawal refund must include duties.
9. **Insurance:** broker quotes for our own policy and for a master policy
   for merchants.

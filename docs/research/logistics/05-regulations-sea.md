# 05 — Destination rules SEA (SG, MY, TH, ID, PH) + VN origin/returns

Research date: 06-Nov-2026. Scope: B2C cross-border apparel / fashion accessories
(HS ch. 61, 62, 64, 42.02) shipped from Vietnam by air/express, sold DDP by us
as merchant of record (MoR), legal entity SG or NL (undecided).

Legend: **[P]** primary source (authority / gazette / official guide),
**[A]** adviser or law firm, **[N]** press / logistics provider,
**UNVERIFIED** = could not confirm in a source during this run.

Research limit: the shared web search budget ran out during this run. Points
marked UNVERIFIED below could not be checked a second time. They are the first
things to re-check or hand to an adviser.

---

## 0. Comparison table

| | SG | MY | TH | ID | PH |
|---|---|---|---|---|---|
| Duty de minimis | Apparel is 0% duty anyway (only liquor, tobacco, motor vehicles, petroleum are dutiable) | CIF ≤ RM 500 **per consignment**, air courier/postal only: no import duty and no import sales tax [N] | **None since 01-Jan-2026**: the exemption applies only to items worth ≤ THB 1 (Customs Notification 219/2568) [A] | FOB < USD 3 per recipient per shipment, **duty only** (PMK 4/2025) [P] | FOB/FCA ≤ PHP 10,000 (CMTA s.423) [N; statute not re-read] |
| Tax de minimis at the border | Import GST relief at CIF ≤ S$400 by air/post; under OVR, GST is charged at the point of sale instead [P] | ≤ RM 500 is exempt at the border, but LVG sales tax applies at the point of sale [A] | **None**: 7% VAT from the first baht [A] | **None**: VAT always applies [P/A] | Statute says "duties and taxes" (VAT included). Whether VAT applies below PHP 10k is contested; no official text confirmed |
| Foreign-seller regime | OVR: global turnover > S$1m **and** B2C to SG > S$100k (12 months). 9% GST, quarterly simplified returns [P] | LVG: > RM 500k LVG sales in 12 months → registered seller. 10%, quarterly LVG-02 [A] | No LVG seller registration in force for goods (Revenue Code change proposed, not confirmed enacted). Customs collects at import [A] | No VAT registration. A foreign PPMSE (e-commerce operator) importing > 1,000 consignments/yr must partner with DJBC and appoint a representative in Indonesia; otherwise the recipient is the importer [P/N] | None for goods (RA 12023 covers digital services only) |
| Apparel duty (MFN) | 0% | Many lines 0%: UNVERIFIED per line | Up to 30% for apparel/footwear, ~20% bags [N]; Mar 2026 plan to raise to 30–40% ceilings | **25% flat** for HS 61–63, 64, 42.02 parcels (PMK 4/2025) + VAT ~11% effective + PPh 5% [P] | ~10–20%: UNVERIFIED |
| ATIGA 0% for VN origin | n/a (already 0%) | Yes with proof of origin | Yes with proof of origin; very valuable now | Unclear for the parcel (barang kiriman) channel: UNVERIFIED | Yes with proof of origin |
| Proof-of-origin waiver | — | FOB ≤ USD 200: no CO needed, a simplified declaration is enough (ATIGA OCP, as implemented by VN Circular 10/2022/TT-BCT) [A] | same | same in principle | same |
| Big apparel-specific risk | Low | Low | Duty rising; Thai-language labels | **Permendag 31/2023 minimum FOB USD 100 per unit** for cross-border direct sale via PPMSE; used-clothing ban; halal for leather (deadline 17-Oct-2026) | Uncertainty over the de minimis rule |

---

## 1. Singapore

**De minimis / valuation**
- Import GST relief: goods imported by air with total **CIF** ≤ S$400 (liquor
  and tobacco excluded). No import permit is needed for non-controlled goods.
  [P: Singapore Customs, OVR page]
- Apparel, footwear and bags carry no customs duty (SG duties apply only to
  liquor, tobacco, motor vehicles and petroleum), so duty is not an issue.

**OVR regime for low-value goods (in force since 01-Jan-2023)** [P: IRAS e-Tax
Guide "Taxing imported low-value goods by way of the OVR regime", **5th ed.,
published 30-Jan-2026**]
- LVG = goods outside SG at the point of sale, delivered by air or post, not
  dutiable (or duty waived), not GST-exempt, and with value ≤ S$400.
- **The threshold applies per item, not per consignment** (guide §16: the entry
  value threshold is applied to each item even if the combined consignment is
  above S$400).
- The S$400 test uses the **sales value**, *excluding* separately charged
  transport and insurance, GST and duties. A vendor may instead elect the
  CIF-based import value with the "Election form for LVG" (guide §5.4).
- GST (9%) is charged on the **value of supply, which includes** shipping and
  insurance charged to the customer. Example in the guide: S$200 dress + S$30
  shipping → GST S$20.70.
- Registration: global turnover > S$1m **and** B2C remote services + LVG to
  SG > S$100k. "Pay-only" simplified regime with **quarterly** returns, due
  1 month after the period ends, and no input tax claims.
- Marketplace operators and redeliverers can be deemed the supplier under
  certain conditions. As MoR we are the supplier anyway.
- Customer location: two non-conflicting proxies (billing address, IP, card).
- **Avoiding double tax**: the vendor's GST number and a "GST-paid" flag per
  item must travel through the logistics chain. For air express the GST number
  goes into the ACCESS declaration. For couriers it goes into the parcel list
  or TradeNet permit. For post it goes into the CN22/CN23 address field.
  Otherwise Customs charges import GST again and the vendor must refund the
  customer (guide §8.3–8.4). For consignments > S$400, the permit uses
  declaration type APS, place of receipt OVR and CA/SC code = vendor GSTN.
- Edge case: sales value < S$400 but CIF > S$400 (freight pushes it over)
  leads to double tax unless handled. Either elect import-value valuation, or
  segregate GST-paid LVG from other goods when shipping.
- Returns: the vendor refunds GST to the customer, issues a credit note and
  adjusts the next return (guide §14.8).

**Entity note:** an SG entity would be a normal GST-registered business, not an
OVR vendor. It would charge GST on LVG as a "local supplier with goods outside
Singapore" (guide §5.5). The SG local registration threshold (S$1m taxable
turnover) is a different test: confirm with an adviser.

**Documentation:** commercial invoice with the GSTN and GST-paid indicator per
item. No buyer tax ID is needed for B2C.

**Corrections to our notes:** none. Add: per-item test, valuation basis, the
need to pass GST information to the courier, quarterly filing.

---

## 2. Malaysia

**LVG sales tax (in force since 01-Jan-2024; Sales Tax (Amendment) Act 2022)**
[A: Grant Thornton MY, L&Co FAQ, KPMG LVG guide 2022; RMCD portal
mylvg.customs.gov.my had an expired TLS certificate during this run]
- LVG = goods sold at a price ≤ RM 500 and brought into MY by land, sea or air
  (liquor, tobacco and vape products excluded). Rate: **10%** on the sale price
  *excluding* tax, duty, shipping and insurance.
- Whether the RM 500 test applies per item or per order: **UNVERIFIED**. The
  sources speak of goods "sold at a price not more than RM500". Check the RMCD
  LVG guide.
- Registration: a seller (local or foreign, **selling via an online platform**,
  which per the 2023 amendment covers the seller's own website) or a marketplace
  operator with LVG sales > **RM 500,000 in any 12-month period** registers as
  a "Registered Seller" via form LVG-01. Taxable period is **3 months**
  (quarterly LVG-02 return), paid in MYR.
- Sellers who sell only via a marketplace do not register; the marketplace
  collects. As MoR on our own checkout, we would be the seller.
- The LVG registration number should go in the exporter/consignor field of the
  consignment note so that Customs does not charge again [N: Ninja Van and
  logistics guidance].

**Border de minimis** [N]
- Air courier and postal: CIF ≤ **RM 500 per consignment** is exempt from
  import duty and import sales tax. Land and sea shipments do not qualify.
- Above RM 500 CIF: normal import duty + sales tax (10%, or 5% for some goods,
  after the 2025 SST scope changes) on CIF + duty, cleared by the courier.

**Duty on apparel:** the aggregator data seen shows 0% MFN on many 42.02 and
62 lines. **UNVERIFIED** line by line (check the Customs Duties Order 2022 /
JKDM tariff database). ATIGA 0% for VN-origin goods with proof of origin.

**Corrections to our notes:** the threshold and rate are confirmed. Add:
quarterly filing, the courier de minimis is per consignment on CIF, and the LVG
number belongs on the consignment note. No 2026 change to LVG found
(Budget 2026 SST items concern other products).

---

## 3. Thailand

**De minimis abolished on 01-Jan-2026** [A: Tilleke & Gibbins, 01-Apr-2026;
Forvis Mazars, 17-Dec-2025; N: Khaosod, regfollower, VATupdate, DHL TH; govt PR
thailand.prd.go.th]
- **Customs Notification No. 219/2568** (Royal Gazette ~11-Dec-2025) narrowed
  the "Type 12" exemption so that it applies only where an item's value is
  **≤ THB 1**. In practice duty + 7% VAT apply from the first baht for every
  parcel. **Our note "from first baht" is correct.**
- Before this: from 05-Jul-2024, interim measures already collected 7% VAT on
  goods worth THB 1–1,500 (Customs collecting) [A: EY, BDO].
- Valuation: CIF; VAT = 7% × (CIF + duty + any excise). Standard Thai rule;
  not re-read in a primary text here, so **UNVERIFIED** for e-commerce parcels.
- Rates reported for low-value parcels: **apparel and footwear up to 30%,
  bags ~20%** [N].
- **30-Mar-2026**: Customs announced a three-phase plan: (1) de minimis removal
  (done), (2) raise duties on sensitive consumer goods to their statutory
  ceilings, **often 30% or 40%**, which needs Cabinet approval, (3) a
  longer-term Customs Tariff Decree overhaul with a possible **flat rate for
  small e-commerce parcels** in place of HS classification (20–30% flat floated
  in Dec 2025). Status of phases 2–3 as of Nov 2026: **UNVERIFIED** (no
  effective date found).
- Customs is integrating data with Shopee, Lazada and TikTok Shop, which add
  duty + VAT at checkout. Tilleke: the "burden of duty accuracy shifts … to the
  platform and the merchant of record". Under-declared values are an
  enforcement focus.

**Foreign-seller VAT regime for goods:** a Revenue Code amendment that would
make e-marketplaces and foreign sellers register and collect 7% VAT on LVG,
OECD-style, was proposed (PwC 2024; ITR 2026 "proposal"). **Not confirmed
enacted.** The existing e-service VAT (Revenue Code No. 53, THB 1.8m threshold)
covers services, not goods. For now: **Customs collects at import, through the
courier.**

**ATIGA:** VN-origin apparel is 0% under ATIGA. With MFN duty at 20–30% (and
possibly 30–40%), proof of origin is worth a lot in Thailand. FOB ≤ USD 200
consignments: a simplified origin declaration instead of Form D (see §7). How
Thai Customs accepts this for express B2C entries in practice: **UNVERIFIED**,
ask the courier.

**Labelling:** apparel, shoes and bags are "label-controlled goods" under the
Consumer Protection Board (Thai-language label) when offered for sale in
Thailand. Whether this applies to cross-border B2C parcels: **UNVERIFIED**,
adviser item.

**Corrections to our notes:** correct, plus: legal basis is Notification
219/2568; planned duty increases toward 30–40%; flat-rate regime under study;
no LVG seller registration (yet).

---

## 4. Indonesia

**Parcel ("barang kiriman") regime: PMK 96/2023, amended by PMK 111/2023 and
PMK 4/2025** (PMK 4/2025 signed 06-Jan-2025, promulgated 03-Feb-2025, **in force
05-Mar-2025**) [P: Kemenkeu JDIH abstract of PMK 4/2025; A/N: DDTC, pajak.com,
SIP Law, setkab.go.id]

- **De minimis**: FOB < **USD 3 per recipient per shipment**. Below it: no
  import duty, **VAT still due** (PPh not charged). No VAT de minimis.
- FOB USD 3–1,500 for general goods: duty **7.5%** flat, no additional duty
  (BMT), no PPh, VAT per normal rules.
- **Bags (42.02), textiles/garments (HS 61–63), footwear (HS 64)** (and
  bicycles): duty **25% flat** (PMK 4/2025 merged the earlier 15–30% bands).
  Exempt from BMT. VAT per normal rules. **PPh art. 22: 5%** (abstract: "PPh
  sebesar 5% bagi komoditas tertentu").
  - The DDTC summary says "VAT and PPh both at a flat 5%"; the official
    abstract says VAT follows the normal rules. Treat VAT as normal; adviser to
    confirm.
- **VAT rate**: statutory 12% since 01-Jan-2025, but for non-luxury goods the
  base is 11/12 of the import value (PMK 131/2024), so the **effective rate is
  11%**. [A: PwC, EY, RSM, KPMG]. Our note's "rate?" → **11% effective**.
- Import value = CIF + duty (customs value is CIF; the de minimis test uses
  FOB). The CIF basis is **UNVERIFIED** in the PMK text.
- Above FOB USD 1,500: the PIBK / normal import route.
- Rough landed cost for a USD 30 garment: 25% duty + 11% VAT on (CIF + duty) +
  5% PPh ≈ **+45–50%** of CIF.

**E-commerce operator (PPMSE) obligations** [P abstract; N: Kontan, Bisnis,
pajak.com]
- A PPMSE (marketplace **or online retailer**) importing **> 1,000 consignments
  per calendar year** must partner with DJBC and exchange its e-catalogue and
  e-invoices (seller name, recipient, invoice number and date, description,
  item code, quantity, unit price).
- PMK 4/2025: a **foreign PPMSE must appoint a representative** in the customs
  area. The representative handles duties and taxes. If no representative is
  appointed, **the recipient is treated as the importer** and is liable.
- Implication: at scale we either appoint an ID representative (a DDP
  structure we control) or the buyer is legally the importer, and our "DDP"
  becomes us paying the courier on the buyer's behalf.
- Recipient ID: older manifest rules ask for the consignee's **NIK or passport
  number** (or NPWP). **UNVERIFIED** for 2025–26. Plan to collect the NIK at
  checkout for ID and ask the courier.

**Trade rules affecting apparel (major risk)**
- **Permendag 31/2023** (e-commerce, PMSE): a PPMSE selling **cross-border
  directly** into Indonesia must apply a **minimum price of FOB USD 100 per
  unit** for finished goods, unless the goods are on the positive list
  (Kepmendag 1998/2023: books, films, software, music, i.e. not apparel). This
  would **block most of our assortment** if we count as a PPMSE doing direct
  cross-border trade. Whether an offshore MoR's own web shop is caught, and
  whether the rule changed in 2025–26: **UNVERIFIED, adviser item #1 for ID.**
- Import policy: Permendag 36/2023 was replaced by **Permendag 16/2025** (in
  force 30-Aug-2025, amended by Permendag 18/2026), with sector rules for
  textiles/TPT in **Permendag 17/2025**. Parcel-specific quantity limits for
  apparel, footwear and bags under these rules: **UNVERIFIED**.
- **Used clothing**: import ban (HS 6309; Permendag 40/2022 amending 18/2021;
  criminal sanctions under Trade Law). Nov 2025: the minister told platforms to
  remove imported used clothing. Relevant if a merchant sells vintage or
  pre-owned items.
- **Halal**: mandatory halal certification for imported consumer goods
  (barang gunaan), including **clothing and accessories**, from **17-Oct-2026**
  (GR 42/2024 timeline; reported). For goods it applies to products made from
  or containing animal materials (leather bags/shoes, wool, silk?). Scope for
  our goods: **UNVERIFIED, adviser item.**
- Labels: Bahasa Indonesia labels and SNI for some textiles (e.g. children's
  clothing): **UNVERIFIED**.

**ATIGA:** whether Form D / origin declarations can be claimed in the barang
kiriman (CN) channel at all, or only via PIB: **UNVERIFIED**. Assume the 25%
parcel rate.

**Corrections to our notes:** duty de minimis USD 3 confirmed. VAT rate =
12% nominal / **11% effective**. Add the 25% duty and 5% PPh for our
categories, the foreign-PPMSE representative rule, and the USD 100 minimum
price risk.

---

## 5. Philippines

**De minimis** [N: DHL PH, multiple guides; statute CMTA (RA 10863) s.423 not
re-read here]
- **FOB/FCA ≤ PHP 10,000 per shipment**: "not subject to duties and taxes"
  (CMTA s.423, implemented by CAO 02-2016 and successors).
- Above PHP 10,000 up to < PHP 50,000: **informal entry** (CAO 02-2021,
  personal or non-commercial). **≥ PHP 50,000**: formal entry, and the
  consignee must be an **accredited importer** (BOC), which is impractical for
  consumers.
- **CAO 02-2025** turns out to be the BOC **schedule of dues, fees and
  charges** (revising the suspended CAO 02-2024). It covers import processing
  charges, including de minimis goods, effective around May 2025. **It is not
  a decision on VAT below de minimis.** Our note "VAT below it disputed (CAO
  02-2025)" should be re-worded: the statute exempts duties *and taxes*. There
  is policy pressure (Philippine Retailers Association position paper of
  06-May-2025, a draft CAO consultation) to lower or scrap the de minimis and to
  tax goods sold via platforms. **No enacted change found.** Whether BIR/BOC
  collect VAT below PHP 10k in practice: **UNVERIFIED**.
- Anti-splitting: several shipments to the same consignee may be aggregated
  (consumer guides). **UNVERIFIED**.
- RA 12023 (2024, VAT on digital services) does **not** cover imported goods.

**Above de minimis:** duty (MFN, apparel roughly 10–20%: **UNVERIFIED**; ATIGA
0% for VN origin with proof) + 12% VAT on landed cost (dutiable value + duty +
charges), collected by the courier/broker.

**Documentation:** consignee name and phone. Accreditation for formal entry.
Some commodities need import clearances (not typical for apparel).

**Corrections to our notes:** threshold confirmed. Fix the attribution of
CAO 02-2025 (fees), and note that VAT below the threshold is exempt by statute
but under political pressure.

---

## 6. Vietnam (origin, and destination for returns)

**Export side**
- No export duty on apparel. Express/postal exports are declared by the
  courier or post as a customs agent (simplified low-value declarations under
  the express-goods rules). Exact declaration type for B2C e-commerce exports:
  **UNVERIFIED**.
- **E-commerce customs decree**: the Ministry of Finance draft "Decree on
  customs management of import/export goods traded via e-commerce" (published
  26-Mar-2025, 30 articles / 7 chapters, exporters/declarants register on an
  e-commerce data system, special procedure for non-Group-2 goods) [A: EY
  April 2025, Baker McKenzie]. **No issued decree number or effective date
  found as of 2026-11; treat as still pending (UNVERIFIED).** Decree 169/2026
  (customs administrative sanctions, in force 01-Jul-2026) is unrelated but
  relevant for penalties.
- **0% VAT on export** (VAT Law 48/2024, Decree 181/2025 and Circular
  69/2025, all in force **01-Jul-2025**) [A: KPMG VN, July 2025]. General
  conditions: an export contract, **non-cash payment** evidence, a customs
  declaration, and goods consumed abroad. Decree 181 adds specific input-VAT
  credit conditions for **goods exported via foreign e-commerce platforms**
  (Art. 27–28; details not read).
  - **Design point:** under our MoR model the VN merchant sells to *our*
    foreign entity (SG/NL) and is paid by bank transfer from us. That fits the
    classic B2B export 0% conditions better than a VN merchant selling B2C
    directly. The merchant's evidence pack: contract or terms with us, our
    payout record as non-cash payment, and the courier's export declaration
    per parcel or batch. Adviser to confirm (adviser item).
- **VN import side (for returns and context)**: the VND 1m VAT exemption for
  low-value express imports was **abolished from 18-Feb-2025** (PM Decision
  01/2025/QĐ-TTg abolishing Decision 78/2010; the decision number is
  UNVERIFIED in sources read). The **import duty** exemption (≤ VND 1m, or
  duty ≤ VND 100k) remains. **Sept 2026 proposal**: lower it to customs value
  ≤ VND 100,000 or tax ≤ VND 10,000 per shipment (not adopted as far as seen).

**Re-import of returned exports into VN**
- Law on Export and Import Duties 2016 Art. 19 + **Decree 134/2016 Art. 33**
  (as amended by Decree 18/2021): goods exported and **re-imported** are **not
  subject to import duty**, and export duty paid is refunded. Customs must be
  able to match the goods to the original export declaration (unused,
  unprocessed). The file includes the original export declaration, the
  re-import declaration and the reason for return. [A: LuatVietnam, LawNet]
- **VAT on re-import** of returned goods: **UNVERIFIED** (not confirmed in a
  source; adviser item). Practical note: for low-value B2C parcels,
  re-importing returns into VN as a formal return may cost more than the
  goods. Destination-country returns hubs or local liquidation are worth
  comparing (logistics design).

---

## 7. Cross-cutting

**ATIGA preferential origin for e-commerce parcels**
- ATIGA Operational Certification Procedures, as implemented in VN by
  **Circular 10/2022/TT-BCT (Appendix I, Art. 16)**: **consignments with FOB ≤
  USD 200 are waived from a Certificate of Origin**. A simplified declaration
  by the exporter is accepted instead, and postal goods are treated the same
  [A: LawNet; Vietnam Briefing]. ASEAN-wide self-certification (AWSC,
  certified exporters declaring on the invoice) has run since 20-Sep-2020.
- Rules of origin: apparel ch. 61/62 under ATIGA usually requires
  substantive local working (e.g. 40% RVC or a change of tariff heading with
  cut-and-sew). Each merchant must be able to substantiate VN origin; imported
  finished goods resold from VN do not qualify.
- Practical acceptance in express B2C clearance (TH, MY, PH; ID uncertain) is
  **UNVERIFIED**: ask DHL/FedEx/UPS/Ninja Van whether they file the
  preference on low-value entries and what wording they need on the invoice.
  The ATIGA Upgrade (signed 2025) may change the OCP: not checked.

**Who is importer of record in practice (DDP B2C)**
- Express carriers usually declare with the **consignee (buyer) as importer**
  and bill duty and tax to the shipper's account for DDP ("duties and taxes
  paid by shipper"), adding a disbursement or DTP fee. In ID, without a PPMSE
  representative, the recipient is legally the importer (PMK 4/2025). In SG
  and MY the OVR/LVG number on the documents prevents a second tax at the
  border. Carrier-specific fees: see the courier slice of this study
  (**UNVERIFIED** here).

**Commercial invoice minimum (all five):** seller (us, as MoR, with tax IDs:
SG GSTN, MY LVG number), buyer name, address and phone, item description,
**HS code (6-digit minimum; 8-digit ASEAN AHTN preferred)**, quantity, unit
price, currency, total, Incoterm (DDP), origin country (VN), origin declaration
text when claiming ATIGA, and a "GST-paid" flag per item for SG. ID: e-invoice
fields per PMK 96/2023 and the recipient's NIK (to confirm).

---

## 8. Items needing an adviser

1. **ID**: does Permendag 31/2023 (min FOB USD 100/unit) catch an offshore MoR
   selling via its own checkout? Current status after 2025–26 reviews?
2. **ID**: appoint a PPMSE representative (and who), PMK 4/2025 duties and
   liabilities, NIK collection, whether ATIGA applies in the CN channel, halal
   scope for leather and wool goods after 17-Oct-2026, Permendag 17/2025
   apparel limits.
3. **TH**: status of the Revenue Code LVG registration bill and of the
   30–40% duty increase / flat parcel rate. Do Thai labelling rules apply to
   cross-border B2C?
4. **PH**: is VAT actually collected below PHP 10k? Is there a pending CAO
   or EO to lower the de minimis?
5. **MY**: is the RM 500 LVG test per item or per invoice? Line-level MFN
   duties for ch. 61/62/64/42.
6. **SG**: SG entity vs NL entity: local GST registration test vs OVR; the
   "local supplier with goods outside SG" treatment.
7. **VN**: export 0% VAT evidence for merchants selling to our foreign entity
   (Decree 181 Art. 17–18, 27–28); VAT on re-imported returns; status of the
   e-commerce customs decree; ATIGA self-declaration for ≤ USD 200 by small
   merchants.

---

## Sources

- IRAS e-Tax Guide, *GST: Taxing imported low-value goods by way of the OVR
  regime*, 5th ed., 30-Jan-2026 [P] —
  https://www.iras.gov.sg/docs/default-source/e-tax/gst-e-tax-guide_taxing-imported-low-value-goods-by-way-of-the-overseas-vendor-registration-regime_(1st-ed).pdf
- IRAS, GST on imports of low-value goods [P] —
  https://www.iras.gov.sg/taxes/goods-services-tax-(gst)/gst-and-digital-economy/gst-on-imports-of-low-value-goods
- Singapore Customs, OVR regime [P] —
  https://www.customs.gov.sg/businesses/customs-schemes-licences-framework/iras-schemes/overseas-vendor-registration-regime/
- Grant Thornton MY, Sales tax on LVG (Dec 2023) [A] —
  https://www.grantthornton.com.my/globalassets/1.-member-firms/malaysia/publications/tax-2023/sales-tax-on-low-value-goods-lvg.pdf
- L&Co, FAQ on LVG [A] — https://landco.my/taxation-en/faq-on-low-value-goods-sales-tax-lvg/
- KPMG MY, Sales tax guide on LVG (2022) [A] —
  https://assets.kpmg.com/content/dam/kpmg/my/pdf/sales-tax-guide-on-lvg-21-dec-2022.pdf
- RMCD LVG portal [P] — https://mylvg.customs.gov.my/ (TLS certificate expired during this run)
- Tilleke & Gibbins, Thailand announces customs duty hikes (01-Apr-2026) [A] —
  https://www.tilleke.com/insights/thailand-announces-customs-duty-hikes-for-key-consumer-goods/
- Forvis Mazars TH (17-Dec-2025) [A] —
  https://www.forvismazars.com/th/en/insights/doing-business-in-thailand/tax/thailand-e-commerce-import-duty-2026
- Thai Govt PRD [P-ish] — https://thailand.prd.go.th/en/content/category/detail/id/2078/iid/440097
- regfollower, Thailand revises duty exemption rules [N] —
  https://regfollower.com/thailand-revises-duty-exemption-rules-for-low-value-goods/
- The Star / The Nation (30-Mar-2026) [N] —
  https://www.thestar.com.my/aseanplus/aseanplus-news/2026/03/30/thai-customs-to-abolish-tax-loophole-and-impose-40-duty-on-imported-parcels
- US-ASEAN Business Council [N] —
  https://www.usasean.org/article/thailand-reshapes-e-commerce-landscape-imposing-duties-all-low-value-imports
- PwC TH Tax Insight 2024-03 (LVG VAT proposal) [A] —
  https://www.pwc.com/th/en/pwc-tax-insights/2024/tax/eng/2024-pwc-tax-insight-03.pdf
- Kemenkeu JDIH, abstract of PMK 4/2025 [P] —
  https://jdih.kemenkeu.go.id/api/download/10a0db67-f63f-43f3-a3c7-1fd0b91479f3/4%20Tahun%202025.pdf
- BPK, PMK 4/2025 [P] — https://peraturan.bpk.go.id/Details/313252/pmk-no-4-tahun-2025
- DDTC, PMK 4/2025 changes [A] —
  https://news.ddtc.co.id/berita/nasional/1809250/pmk-42025-berlaku-djbc-beberkan-8-perubahan-ketentuan-barang-kiriman
- DJP, PMK 4/2025 article [P] —
  https://www.pajak.go.id/id/artikel/menyoal-pokok-perubahan-perpajakan-barang-kiriman-dalam-pmk-42025
- Setkab, de minimis USD 3 [P] — https://setkab.go.id/en/govt-to-cut-import-tax-threshold-for-e-commerce-goods/
- EY / RSM / KPMG ID on PMK 131/2024 (11/12 base) [A] —
  https://www.ey.com/en_id/technical/tax-alerts/PMK-131-New-Calculation-of-VAT-Payable
- DDTC on Permendag 31/2023 USD 100 minimum [A/N] —
  https://news.ddtc.co.id/berita/nasional/1797480/kemendag-batasi-impor-e-commerce-di-bawah-us100-tak-boleh-masuk
- Mondaq, Permendag 16/2025 [A] —
  https://webiis08.mondaq.com/export-controls-trade-investment-sanctions/1721278/the-new-import-regulation-under-ministry-of-trade-regulation-no-16-of-2025
- Hukumonline, used clothing [A] —
  https://hukumonline.com/klinik/a/importing-used-clothes-in-indonesia-what-is-the-legality--lt6437e7eb9233e
- Jakarta Post 07-Nov-2025, used clothes on platforms [N] —
  https://www.thejakartapost.com/business/2025/11/07/minister-tells-e-commerce-platforms-to-stop-sale-of-imported-used-clothes
- DFDL / Mondaq, Indonesia halal 17-Oct-2026 [A] — https://www.dfdl.com/?p=67700
- PortCalls / AISL on CAO 02-2025 (fees) [N] — https://portcalls.com/boc-amends-schedule-of-dues-fees-charges/
- DHL PH de minimis guide [N] —
  https://www.dhl.com/discover/en-ph/logistics-advice/essential-guides/de-minimis-thresholds-guide
- Philstar 12-May-2025, retailers push de minimis removal [N] —
  https://www.philstar.com/business/2025/05/12/2442350/retailers-push-de-minimis-removal-gradual-reforms/amp/
- KPMG VN, Decree 181/2025 and Circular 69/2025 [A] —
  https://assets.kpmg.com/content/dam/kpmg/vn/pdf/2025/07/decree-181-and-circular-69-en.pdf
- EY VN, Draft decree on customs management of e-commerce (Apr 2025) [A] —
  https://www.ey.com/en_vn/technical/tax/tax-and-law-updates/draft-government-decree-on-customs-management-of-e-commerce-transactions
- Vietnam News, VAT on low-value express imports (Feb 2025) [N] —
  https://vietnamnews.vn/economy/1692284/viet-nam-to-impose-vat-on-low-value-express-imported-goods.html
- Vietnam News, proposal VND 100,000 (Sept 2026) [N] —
  https://vietnamnews.vn/economy/1799240/ministry-proposes-lowering-tax-exemption-threshold-to-vnd100-000-for-imports-amid-e-commerce-boom.html
- LuatVietnam, duty refund cases (re-import) [A] —
  https://english.luatvietnam.vn/legal-news/5-cases-of-duty-refund-for-import-duty-and-export-duty-4729-91229-article.html
- LawNet, ATIGA origin declaration / USD 200 waiver [A] —
  https://lawnet.vn/en/laws/vietnam-what-are-the-regulations-on-origin-declaration-under-the-rules-of-origin-in-the-asean-trade-52869.html

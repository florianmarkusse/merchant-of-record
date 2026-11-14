# 06 — Destination rules: East Asia, South Asia, Oceania

Researched 06-Nov-2026. Scope: B2C apparel and fashion accessories (HS 61, 62,
64, 42) shipped from Vietnam by express courier or e-commerce post, sold DDP by
a foreign merchant of record (MoR, a SG entity or a Dutch BV), with volume
added up across all merchants.

Notation: **[O]** means confirmed against an official source (customs or tax
authority) in this session. **[A]** means an adviser or a reputable secondary
source. **UNVERIFIED** means the claim comes from background knowledge or a
single weak source and was not confirmed. Rates are indicative. Classify per
SKU against each country's tariff before going live.

General point that holds for every country here: for B2C parcels, FTA
preference is almost never worth claiming. Either the duty is already zero
under the de minimis (AU, NZ, HK, KR up to USD 150, JP up to ¥10,000 for
non-excluded goods, TW up to NT$2,000), or the parcel route does not accept
the proof (CN personal articles, IN courier). The exceptions are JP and KR
above their thresholds. There, origin claims under VJEPA, CPTPP, RCEP or
VKFTA can be worth it, but only for goods that pass textile origin rules
(two-stage or yarn-forward, so fabric made in VN, KR or JP), and Vietnamese
apparel often uses fabric from China.

---

## 1. China (mainland)

**Status: two different import channels, and neither is built for a small
foreign MoR shipping from its own checkout.**

### 1.1 De minimis and valuation
- **Personal postal and express articles (行邮, "personal articles")**: no
  value-based de minimis. Tax is waived only when the computed tax is
  **≤ RMB 50** [O: Shanghai Municipal Government FAQ, Sept 2025,
  english.shanghai.gov.cn]. Value limit per parcel: RMB 1,000 per parcel
  from abroad, RMB 800 from HK, Macau and TW (GAC Announcement 2010 No. 43;
  **UNVERIFIED as current**. The Shanghai FAQ quotes RMB 2,000 for inbound
  parcels from other countries, so the sources conflict). A single
  indivisible item above the limit can still clear. The postal articles tax
  (行邮税) is one rate that replaces duty, VAT and consumption tax. Since
  09-Apr-2019 textiles and clothing pay **20%** [A: China Daily 2019-04;
  official 2026 schedule not found]. The base is the customs "complete tax
  price" list (完税价格表), not the invoice. The channel is meant for goods
  for personal use "in reasonable quantity", not for commercial sales.
- **Cross-border e-commerce retail import (CBEC, 跨境电商零售进口, customs
  codes 9610 direct mail and 1210 bonded)**: limit of **RMB 5,000 per
  transaction and RMB 26,000 per person per year** (财关税〔2018〕49号,
  effective 01-Jan-2019) [A: China Law & Practice; Mondaq]. Within the limit:
  **0% duty**, and import VAT and consumption tax at **70%** of the normal
  amount. Clothing has no consumption tax, so the cost is 13% × 70% =
  **9.1%** (derived from the 2016/2018 notices). A single item above
  RMB 5,000 can still enter if the buyer's annual limit is not exceeded,
  but then it pays full general-trade tax.
  - One trade-press article (JCtrans, 24-Apr-2026) reports the limits raised
    to **RMB 8,000 and RMB 40,000**. No MOF or GAC notice was found and the
    page now returns 404. **UNVERIFIED. Treat 5,000 and 26,000 as current.**
  - Returns: MOF/GAC/STA notice **No. 16 of 2026** covers *export* CBEC
    returns (goods exported from CN and returned within 6 months are free
    of import duty and VAT, valid 2026–2027) [A: Bloomberg Tax, VATupdate
    2026-02/03]. It does not apply to our case.

### 1.2 LVG and foreign seller registration
- No VAT registration regime for foreign sellers. Tax is collected at the
  border. Under CBEC, the **e-commerce platform, logistics company or a
  domestic agent registered with customs is the withholding agent** (代收代缴)
  [A]. The overseas e-commerce enterprise must **register with China Customs
  through a domestic agent** (境内代理人). That agent carries joint
  liability for tax and product-quality claims [A, background].
- CBEC needs the "three-order match" (三单对碰). Order data from a platform
  registered with customs, payment data from a licensed payment institution
  (Alipay, WeChat Pay, UnionPay and similar), and waybill data from a
  registered logistics company all go to customs and must match the buyer.

### 1.3 Duty on apparel and FTAs
- MFN on HS 61/62 is roughly 6–8% after the 2018 cuts (UNVERIFIED
  per-line). ACFTA (ASEAN–China) and RCEP give 0% on most VN apparel lines,
  but only on general-trade imports with a certificate of origin. **Not
  usable for personal articles or CBEC**: CBEC duty is 0% anyway, and the
  postal articles tax ignores origin.

### 1.4 Buyer data
- CBEC: the buyer's **real name and Chinese ID card number** (身份证), and
  the payer must be the same person as the order person, checked by customs
  against the annual limit [A, background]. Personal articles by express:
  the consignee's ID is generally required at clearance (UNVERIFIED for
  each courier).
- Data residency and transfer: PIPL governs any personal data we collect
  from Chinese buyers. Moving ID numbers abroad is a cross-border transfer
  of sensitive personal information (security assessment or standard
  contract). This is a **compliance flag** under AGENTS.md: do not assume an
  answer.

### 1.5 Restrictions
- CBEC goods must be on the **CBEC retail import positive list** (正面清单).
  Apparel, shoes and bags are on it. CBEC goods are exempt from
  first-import licensing and registration, and from Chinese-language
  labelling (a notice to the consumer that labels may be foreign is
  required).
- Personal articles: commercial quantities get rejected or reclassified as
  general trade. Counterfeits are seized, and CN customs enforces IP
  actively.

### 1.6 DDP in practice
- **Express**: China customs sorts express consignments into Class A
  (documents), B (personal articles) and C (low-value goods ≤ RMB 5,000
  needing no licences). Anything above that goes to general trade (formal
  entry, with the consignee holding customs registration). DHL, FedEx and
  UPS DDP shipments to individuals clear mostly as Class B personal
  articles at the 20% postal rate. Couriers often ask for ID and may hold
  shipments (UNVERIFIED in detail).
- **CBEC 9610 direct mail** is the legitimate B2C route. In practice it
  means selling through Tmall Global, JD Worldwide, Douyin or Kuaishou
  global, or through a Chinese cross-border logistics provider that acts as
  the domestic agent and pushes the platform data. A foreign MoR with its
  own checkout would need its site registered as a CBEC platform, plus a
  Chinese payment partner, which is not practical at low volume.

### 1.7 Returns
- CBEC: returns to the customs-supervised area within the return window
  (usually 30 days after release, 45 days to arrive back) get the CBEC tax
  cancelled or refunded (GAC Announcement 2020 No. 45, **UNVERIFIED**).
  Personal articles: no practical refund.

**Rating: HARD.** CBEC needs a platform, a domestic agent, Chinese payment
rails and checks on the buyer's ID. Personal-article parcels are taxed at
20%, are not meant for commercial sales, and carry seizure and delay risk.
PIPL limits on data transfer add more. Only practical later, through a
Tmall Global or JD Worldwide type partner.

---

## 2. Hong Kong

### 2.1 De minimis and valuation
- Free port. **No GST, VAT or sales tax** [A]. Customs duty applies only to
  4 groups: liquor, tobacco, hydrocarbon oil and methyl alcohol [O:
  customs.gov.hk, Dutiable Commodities Ordinance]. **Apparel, shoes and
  bags: 0%.**

### 2.2 LVG and foreign seller registration
- None. HK has no indirect tax.

### 2.3 Duty and FTAs
- Duty is 0, so the ASEAN–HK FTA is irrelevant.

### 2.4 Buyer data
- None beyond normal address and phone.

### 2.5 Restrictions
- Trade Descriptions Ordinance (false trade descriptions, counterfeit
  goods) is strongly enforced by C&ED. No mandatory textile content
  labelling for imports (UNVERIFIED).

### 2.6 DDP in practice
- An import declaration (TDEC) is due within 14 days, with a small
  declaration charge [O: customs.gov.hk lodgement page]. Courier shipments
  are covered by the carrier's consolidated filings. Some express carriers
  charge an admin fee. In practice DDP and DAP are the same here: nothing
  to collect except carrier fees.

### 2.7 Returns
- There is no tax, so nothing to refund.

**Rating: EASY.** No duty, no tax, no registration. Only counterfeit and
trade-description risk.

---

## 3. Taiwan

### 3.1 De minimis and valuation
- Express or post consignments with a dutiable value **≤ NT$2,000** are
  free of duty, commodity tax and business tax, limited to **6 times per
  person per half-year** (Jan–Jun, Jul–Dec). From the 7th shipment the
  goods are taxed even under NT$2,000 [A: Comgateway 2026, PTS News;
  matches the long-standing MOF rule]. Above it: duty on CIF value plus
  **5% business tax** (VAT) on CIF + duty.
- **2025–2026 reform status:** the MOF said in April 2025 that it was
  studying changes, including a lower NT$1,000 threshold for goods from
  China and other dumping-prone origins. In January 2026 the Finance
  Minister said cross-ministry review was ongoing [A: CTEE 17-Apr-2025,
  TechNews 10-Apr-2025, Want Daily 07-Jan-2026]. **No enacted change found as
  of the latest sources (early to mid 2026). Assume NT$2,000 + 6/half-year
  is still in force, but re-check before launch.** Any differential change
  would most likely target CN and HK, not VN (UNVERIFIED).

### 3.2 LVG and foreign seller registration
- Taiwan's foreign-seller VAT registration covers **cross-border electronic
  services** only, not physical goods (UNVERIFIED for 2026, but no goods
  regime was found). Tax on goods is collected at the border through the
  courier.

### 3.3 Duty and FTAs
- MFN on apparel is roughly 10–12% on most HS 61/62 lines, about 5–10% on
  bags, and higher on some footwear (**UNVERIFIED** per line). There is no
  FTA between VN and TW, and TW is in neither RCEP nor CPTPP.

### 3.4 Buyer data
- **Real-name authentication (EZ WAY 易利委)**: since about 2020, the
  consignee of an express or postal import must confirm through the EZ WAY
  app that the declaring courier may file in their name, linked to their
  national ID or ARC [A: Spaceship vendor page; official page not
  reached]. This is also how customs counts the 6-per-half-year rule. A
  missing EZ WAY confirmation is the main cause of held parcels
  (UNVERIFIED in detail).

### 3.5 Restrictions
- Commodity labelling rules (商品標示法) apply to goods sold in TW.
  Enforcement on cross-border B2C parcels is light (UNVERIFIED). Normal IP
  enforcement.

### 3.6 DDP in practice
- Express parcels are classed **C1** (documents), **C2** (low value, mostly
  tax-free) and **C3** (dutiable, simplified entry) up to about
  NT$50,000 FOB (**UNVERIFIED**). Above that, formal entry. DHL, FedEx and
  UPS bill duties to the shipper. Local e-commerce lines (via HK or direct
  from VN) work too.

### 3.7 Returns
- Duty refund on re-export of returned goods needs proof of identity of
  the goods and a customs claim. Not practical per parcel (UNVERIFIED).

**Rating: MEDIUM-EASY (EASY at the low end).** Most apparel orders are
under NT$2,000 (about US$62) and pay no tax. The friction is EZ WAY
onboarding for each buyer, the 6-per-half-year counter, and the risk of a
reform. Above NT$2,000 the duty is only moderate.

- Checked 10-Nov-2026: NT$2,000 and six duty-free parcels per half-year
  still apply. The Finance Minister promised in April 2025 to consider
  changes, and a Taipei Times editorial of 03-Feb-2026 says none has been
  made [S: Taipei Times; Kaohsiung Customs notice via search].

---

## 4. Japan

### 4.1 De minimis and valuation (current, until 31-Mar-2028)
- Goods with a total customs value **≤ ¥10,000** are exempt from **both
  customs duty and consumption tax** [O: Japan Customs FAQ 1006,
  customs.go.jp].
- **Excluded from the exemption, so taxed at any value: leather bags and
  handbags, gloves, knitted apparel (T-shirts, sweaters), ski boots, leather
  shoes and leather-soled footwear** [O: Japan Customs 1006]. This hits our
  category directly: knitwear (HS 61), leather bags (4202) and leather
  shoes (6403) are taxed even at ¥3,000.
- Valuation: for **personal-use** imports, the taxable value is **60% of
  the retail price** [O: Japan Customs]. For commercial imports, full
  transaction value.
- Consumption tax 10% (7.8% national + 2.2% local) on CIF + duty.

### 4.2 Reform: FY2026 tax reform (announced, effective 01-Apr-2028)
- Ruling-party proposals of **19-Dec-2025** [A: BDO, PwC JTU 14-Jan-2026,
  VATupdate 2026-01]:
  - Mail-order goods shipped from abroad to Japan at **≤ ¥10,000 per item**
    (excluding JCT) become a taxable transfer in Japan from
    **01-Apr-2028**. The seller charges JCT at the point of sale.
  - Foreign sellers register as **"Specified Low-Value Asset Sellers"**.
    Their goods are then exempt from JCT at importation if the import
    declaration carries the seller's registration number and a statement
    that the goods relate to a transfer of specified low-value assets. The
    model is like Australia's.
  - **Platform deemed supplier**: platforms with sales above **¥5 billion**
    become liable (Type II platform operators).
  - The 60% personal-use valuation rule is abolished and one ¥10,000
    threshold is set [A: VATupdate].
- **Enactment:** one secondary source marks it "Enacted" as part of the
  FY2026 amendments (Diet usually passes these in March) [A: lookuptax].
  Not confirmed against MOF or NTA. **Treat as probably enacted,
  UNVERIFIED.** It reportedly keeps the customs-duty exemption at import
  for ≤ ¥10,000 (only JCT moves to the seller) [A, UNVERIFIED].
- **What it means for us:** from April 2028 the MoR must register in Japan
  and file JCT on parcels ≤ ¥10,000. Japanese JCT for non-residents
  generally needs a **tax agent (納税管理人)**. Volume is added up across
  merchants. No small-seller threshold has been seen yet for the LVG rule
  (UNVERIFIED: whether the ¥10M exempt-business threshold still applies).

### 4.3 Duty on apparel and FTAs
- MFN: knitted apparel about 7.4–10.9%, woven about 4.4–13.4%, leather
  bags about 8–16%, leather footwear about 30% or ¥4,300 per pair,
  whichever is higher, with tariff quotas (UNVERIFIED per line). **Simple
  (consolidated) tariff rates** apply to consignments ≤ ¥200,000 and are
  easier: clothing about 10% (UNVERIFIED).
- VN–JP: **VJEPA, AJCEP, CPTPP, RCEP** give 0% on most VN apparel lines,
  subject to textile origin rules: VJEPA and AJCEP need two processes
  (fabric made in VN, JP or ASEAN), CPTPP is yarn-forward with a
  short-supply list. Japan generally **waives documentary proof of origin
  for consignments ≤ ¥200,000** (**UNVERIFIED**), so for qualifying goods a
  preference claim through the courier is feasible above ¥10,000. Origin
  must still be true, so this is a merchant-by-merchant question.

### 4.4 Buyer data
- Name, address and phone. No ID number. A Japanese phone number helps
  courier delivery.

### 4.5 Restrictions
- **Household Goods Quality Labeling Act** (家庭用品品質表示法): textile
  content and care labels in Japanese are required for apparel *sold in
  Japan*. For cross-border B2C the obligation is debated and enforcement
  is weak, but platforms expect it (UNVERIFIED). Customs enforces IP
  strictly and, since Oct 2022, seizes counterfeits even for personal use.
- Leather footwear has tariff quotas and high out-of-quota duty, which
  makes it expensive.

### 4.6 DDP in practice
- Small-value simplified declarations for ≤ ¥200,000. Formal entry above.
  Couriers offer DDP (DTP) with the bill going to the shipper. The consignee
  is the legal importer. From 2028 the declaration must carry the MoR's
  JCT registration number for the exemption at import. Non-resident
  importer status (if we were the importer) needs a tax representative.

### 4.7 Returns
- **Refund of duty and JCT on re-export** of goods that do not match the
  contract (defective or wrong goods), if re-exported within 6 months in
  unchanged condition (Customs Tariff Act Art. 20, **UNVERIFIED**).
  Returns for change of mind generally get no refund. From 2028 the JCT on
  sales below ¥10,000 is adjusted on the MoR's return.

**Rating: MEDIUM.** Many orders under ¥10,000 enter free today, but
knitwear, leather bags and leather shoes are excluded and pay duty + 10%
JCT. From April 2028 we need a JCT registration (likely with a tax agent)
for parcels ≤ ¥10,000. Labelling expectations are high.

---

## 5. South Korea

### 5.1 De minimis and valuation
- Personal-use goods with a **total customs value ≤ USD 150** (including
  shipping) are exempt from duty and VAT. For goods shipped from the US the
  limit is **USD 200** under list clearance (KORUS), not relevant for
  VN-origin shipments [O: Korea Customs Service, customs.go.kr cntntsId
  2733; Customs Act Enforcement Rules Art. 45(2)1, version in force from
  31-Jul-2026].
- **Above USD 150 the whole value is taxed**, not just the excess [O/A:
  KCS example]. Duty is on CIF and **VAT 10%** on CIF + duty.
- **Combined taxation (합산과세)**: the rule taxing items that arrive on the
  same day was narrowed on 17-Nov-2022. Goods from different suppliers are
  not combined. Same-supplier, same-day or split shipments can be combined
  [A: braindetox.kr citing KCS press release 16-Nov-2022]. A MoR that serves
  many merchants looks like **one supplier** to KCS, so a buyer's two
  orders from two of our merchants could be combined (risk, UNVERIFIED).

### 5.2 LVG and foreign seller registration
- Korea's foreign-seller VAT registration (simplified business
  registration) covers **electronic services only**. No LVG regime for
  physical goods [background, UNVERIFIED for 2026]. VAT is collected at the
  border above USD 150.

### 5.3 Duty on apparel and FTAs
- MFN on apparel is **13%** on most HS 61/62 lines (a historical figure
  confirmed by Korea JoongAng Daily at the time VKFTA was signed). Bags
  about 8%, shoes about 13% (UNVERIFIED). Personal-use goods can use
  **simplified tariff rates** (간이세율) covering duty and VAT, about 20%
  for clothing (UNVERIFIED).
- **VKFTA and AKFTA** (and RCEP) give 0% on most VN apparel, subject to
  origin rules (VKFTA textile rules generally need fabric made in VN or
  KR). Korea's FTA Act lets importers claim origin **without a
  certificate for consignments ≤ USD 1,000** (**UNVERIFIED**). Workable
  through couriers in principle, but it is a merchant-level origin
  question.

### 5.4 Buyer data: PCCC (개인통관고유부호)
- **Mandatory** for every personal express import, list clearance
  included, whatever the amount [A: FedEx KR notice; Daeryun Law FAQ]. A
  13-character code starting with "P", issued through KCS UNI-PASS or the
  app.
- **2026 changes** [A: braindetox.kr citing Policy Briefing, Jan, Mar and
  Aug 2026]:
  - **One-year validity** from 2026. Existing codes expire on the holder's
    birthday in 2027, with renewal needed each year.
  - From **02-Feb-2026** the **delivery postal code must match** the
    registered address, or one of up to 20 pre-registered delivery
    addresses.
  - The name and phone number must match (already enforced).
  - A one-time verification number pilot started 28-Aug-2026.
- So checkout must collect the PCCC, the consignee name exactly as
  registered, the phone number and the postal code, and should validate the
  PCCC format. KCS offers a verification API to e-commerce operators
  (UNVERIFIED for foreign sellers). This is personal data covered by
  Korea's PIPA.

### 5.5 Restrictions
- Apparel needs no KC certification for personal imports. Some children's
  products need KC safety certification (UNVERIFIED for personal imports).
  Counterfeits are seized. Personal-use quantities are expected: many
  identical items look commercial.

### 5.6 DDP in practice
- **List clearance (목록통관)** for ≤ USD 150 personal-use goods (apparel is
  eligible). **General (simplified) declaration** above it, or for excluded
  goods. Formal entry for commercial goods. The consignee is the importer.
  Express couriers offer DTP billed to the shipper. The PCCC is needed in
  both cases.

### 5.7 Returns
- Duty and VAT on goods imported for personal use and **re-exported within
  1 year**, unused and unchanged, can be refunded (Customs Act Art. 106-2;
  covers change-of-mind returns of e-commerce purchases) (**UNVERIFIED**
  article reference). Needs the export declaration and proof the goods are
  the same, so it can be done for higher-value parcels.

**Rating: MEDIUM-EASY.** USD 150 covers most apparel orders. No foreign
seller registration. Above the threshold, 13% duty + 10% VAT, or 0% with
VKFTA origin. The main work is in the data: a mandatory PCCC with name,
phone and postal code matching (2026 tightening), and a combined-taxation
risk because the MoR looks like one supplier.

---

## 6. India

### 6.1 De minimis and valuation
- **No de minimis for purchased goods.** The **Rs 5,000** exemption (post
  and courier) covers **bona fide gifts** only. Samples have their own
  limits. E-commerce purchases are dutiable from the first rupee [A: The
  Core 2025-02; Mumbai Customs notice reported by EximGuru; consistent with
  Notification 171/93-Cus as amended]. Some couriers and secondary sources
  report "Rs 5,000 duty-free" for all courier parcels, which is wrong for
  sales (UNVERIFIED nuance; customs officers apply it unevenly).
- Valuation: CIF transaction value (Customs Valuation Rules 2007). Air
  freight in the CIF value is subject to the 20%-of-FOB rule where actual
  freight is unknown (UNVERIFIED for courier).
- Tax stack on apparel: **Basic Customs Duty (BCD)** + **Social Welfare
  Surcharge 10% of BCD** + **IGST** on (CIF + BCD + SWS).
  - BCD on HS 61/62 is 20% or a specific rate per piece, whichever is
    higher, on many lines (UNVERIFIED for 2026 after the Feb 2025 and Feb
    2026 budget rationalisations). Footwear 20–35%, bags 20%.
  - IGST after GST 2.0 (Notification 9/2025-CT(R), effective
    **22-Sep-2025**): apparel and footwear **5% if the value per piece or
    pair is ≤ Rs 2,500, 18% above** [A: Bata compliance note, Amazon Seller
    Central, Khaitan & Co]. How the per-piece test works for imports
    (assessable value against retail price) is unclear (UNVERIFIED).
  - Landed cost is often **about 30–45%** on apparel.

### 6.2 LVG and foreign seller registration
- No LVG or foreign-seller GST regime for goods. Tax is collected at
  import. (The 2% equalisation levy on e-commerce operators was abolished
  from 01-Aug-2024, background.) A foreign MoR does not register for GST to
  sell goods by courier DDP. Duty and IGST go through the courier.

### 6.3 FTAs
- **AIFTA (ASEAN–India)**: most apparel lines are excluded or have small
  cuts, and preference needs an AIFTA Form AI certificate per shipment.
  **Not practical for courier parcels.** Do not plan on it.

### 6.4 Buyer data: KYC
- Courier imports need **consignee KYC**. For individuals, any one of
  **Aadhaar, passport, PAN, driving licence or Voter ID**, with the
  courier's delivery address taken as proof of address [O/A: CBIC Circular
  02/2018-Customs and later updates; Chennai Customs FAQ 2025-10]. CBIC has
  told couriers not to demand more than this list.
- Express couriers usually ask the consignee for KYC after arrival, which
  causes delays and abandoned parcels. Collecting KYC at checkout means
  handling **Aadhaar numbers**, which the Aadhaar Act restricts. Collect at
  most a PAN or passport number, or let the courier do KYC. Compliance flag
  under DPDP Act 2023 data rules.

### 6.5 Restrictions
- Legal Metrology (Packaged Commodities) Rules: MRP and importer
  declarations apply to pre-packaged goods sold in India. Applying them to
  direct B2C imports is unclear (UNVERIFIED). BIS quality control orders
  cover some textiles, fibres and footwear (QCOs on footwear since 2023).
  Several were pulled back in 2025 (UNVERIFIED for 2026). Counterfeits are
  seized.

### 6.6 DDP in practice
- **Courier Imports and Exports (Electronic Declaration and Processing)
  Regulations, 2010**: Class A documents, Class B gifts and samples, and
  **Class C dutiable goods**, with a per-consignment value cap (commonly
  cited as Rs 10 lakh for imports; UNVERIFIED). Notification
  33/2026-Customs (NT), effective 01-Apr-2026, removed the Rs 10 lakh cap
  on courier **exports** [A: Tribune India, The Week 31-Mar-2026]. Above the
  cap, a formal Bill of Entry.
- DHL, FedEx and UPS offer DTP to India. Post (India Post via VNPost/EMS)
  is cheaper but slow and duty-on-delivery is common. Many express couriers
  restrict B2C imports for individuals or route them as personal imports.

### 6.7 Returns
- Duty drawback on re-export under **Section 74 Customs Act** (98% of duty
  if re-exported within the time limit with the goods identified) and
  IGST refund. A per-parcel claim is not workable for B2C. Treat duty as
  lost on returns.

**Rating: HARD.** No de minimis for sales. About 30–45% landed cost on
apparel. KYC friction for each consignee (Aadhaar sensitivity). Uneven
courier clearance, returns that cannot be refunded, and FTA preference
that cannot be used.

---

## 7. Australia

### 7.1 De minimis and valuation
- Goods in a consignment with a **customs value ≤ A$1,000**: no duty or
  import GST at the border. Instead, **GST 10% is charged at sale by the
  foreign supplier** (LVG regime, in force since **01-Jul-2018**) [O: ATO
  page "GST on low value imported goods", updated 11-Sep-2025]. Customs
  value is the price less freight and insurance from the place of export,
  so roughly FOB. Note that **GST is charged on the full price including
  shipping** to the customer, while the A$1,000 test uses customs value.
- Above A$1,000 per consignment: the importer (customer) pays duty, GST and
  clearance charges at the border. The supplier does not charge LVG GST
  [O: ATO].

### 7.2 LVG and foreign seller registration
- Non-residents must register once **GST turnover reaches A$75,000** in 12
  months. The turnover counts LVG sales and digital services to Australian
  consumers [O: ATO non-resident page]. **Simplified GST registration**
  (no ABN, an ATO Reference Number (ARN), **quarterly** simplified returns,
  no input tax credits) is available for non-residents that do not
  warehouse goods in Australia [O: ATO]. Quarterly lodgement and payment is
  the norm for simplified registration (background).
- Electronic distribution platforms are liable when they facilitate the
  sale, and redeliverers in some cases. **As a MoR selling on its own
  checkout, we are the supplier and register ourselves**, with turnover
  added up across all merchants.
- The supplier's **ARN or ABN must go on the customs documentation** (the
  commercial invoice and the self-assessed clearance (SAC) declaration),
  with a flag that GST was paid at the point of sale [A: PwC alert
  2017-11; ABF Customs Notice 2018-13]. That stops GST being charged
  twice.

### 7.3 Duty and FTAs
- MFN on apparel is 5% (most lines), footwear 5%, bags 5% (background).
  **Not relevant at ≤ A$1,000**, where there is no duty. Above A$1,000,
  AANZFTA, RCEP and CPTPP (VN–AU) give 0%, and AU accepts origin
  declarations. For B2C it rarely matters.

### 7.4 Buyer data
- None beyond name, address and phone. Keep evidence of the buyer's
  location for the GST decision (address, payment card country, IP), the
  "reasonable belief" rule.

### 7.5 Restrictions
- **Care labelling is mandatory** for textile apparel and footwear
  (Consumer Goods (Care Labelling) Information Standard 2023, transition
  ended 01-Jun-2025?) (**UNVERIFIED**: date and coverage of imports sold
  B2C). Customs seizes counterfeits under the Trade Marks Act. The
  Australian Consumer Law (ACL) gives buyers guarantees, so refunds for
  faulty goods are mandatory.

### 7.6 DDP in practice
- Under the LVG regime "DDP" just means we charged GST at checkout. The
  courier clears under SAC with our ARN, and nothing is collected on
  delivery. Couriers (DHL, FedEx, UPS, Aramex, AusPost through VNPost/EMS)
  handle this routinely. Formal Full Import Declaration above A$1,000, plus
  ABF processing charges.

### 7.7 Returns
- Refund to the buyer means a **decreasing GST adjustment** on the next
  return. No customs refund is needed below A$1,000. Easy.

**Rating: EASY.** One clear registration (simplified, quarterly, ARN). No
duty below A$1,000. Mature courier processes. Below A$75,000 aggregated
turnover there is no registration duty at all (then GST is not charged at
the border either, below A$1,000).

---

## 8. New Zealand

### 8.1 De minimis and valuation
- Goods bought from overseas websites with a value **≤ NZ$1,000** enter
  **free of duty**. **The overseas supplier collects GST (15%)** at sale
  [O: NZ Customs "Duty and GST" page; IRD LVG page]. Tobacco and alcohol
  are excluded.
- **NZ$1,000 is tested per item** (excluding GST), not per order. A cart
  above NZ$1,000 made of items each ≤ NZ$1,000 is still LVG [O: IRD].
  Customs can combine separately bought items arriving together, which can
  push a consignment over NZ$1,000 [O: NZ Customs].
- Above NZ$1,000 per item: GST and duty at the border (formula: customs
  value + freight + insurance + duty, × 15%) [A]. Registered suppliers can
  opt to charge GST on items above NZ$1,000 at sale [A: Mondaq].
- A Customs and MPI **Goods Levy** applies to imports, passed on by
  brokers (O; amount and threshold not stated on the page).

### 8.2 LVG and foreign seller registration
- Overseas suppliers must register when supplies to NZ exceed **NZ$60,000
  in 12 months** (rules in force from **01-Dec-2019**) [A: calculate.co.nz.
  The official page says registration depends on total supplies without
  naming the figure. NZ$60,000 matches the general GST threshold; treat as
  confirmed by background]. The supplier gets an IRD number, files GST
  returns (usually every 2 months, or 6-monthly/monthly options) and
  issues receipts [A]. Marketplaces and redeliverers can be liable instead.
- The MoR's NZ GST number goes on the commercial invoice (background,
  UNVERIFIED as a formal rule).

### 8.3 Duty and FTAs
- MFN on apparel 10% (some 5%), footwear about 5–10% (background,
  UNVERIFIED). **Not relevant at ≤ NZ$1,000**, where there is no duty.
  AANZFTA, RCEP and CPTPP give 0% above that.

### 8.4 Buyer data
- None special.

### 8.5 Restrictions
- Fibre content labelling (Consumer Information Standards (Fibre Content
  Labelling) Regulations 2000) and care labelling (2000) apply to goods
  supplied in NZ (UNVERIFIED for cross-border B2C). The Consumer Guarantees
  Act applies.

### 8.6 DDP in practice
- Low-value consignments clear on simplified entries. GST is already paid,
  so nothing is collected on delivery. Couriers and NZ Post handle this
  routinely.

### 8.7 Returns
- A refund means a GST credit adjustment on the return. No border tax to
  reclaim.

**Rating: EASY.** Same model as AU. A small market, but straightforward
once aggregated sales pass NZ$60,000.

---

## Comparison table

| Country | Duty/tax de minimis (current) | Foreign seller / LVG registration | Indirect tax rate | Apparel duty (indicative) | FTA usable for parcels? | Buyer data required | Announced changes | Rating |
|---|---|---|---|---|---|---|---|---|
| China | Personal articles: tax waived if ≤ RMB 50 tax. CBEC: 0 duty, 70% VAT within RMB 5,000/26,000 [8,000/40,000 UNVERIFIED] | None, but CBEC needs a domestic agent plus a platform registered with customs | Postal articles tax 20% (textiles); CBEC 9.1% | MFN about 6–8% | No | Real name + Chinese ID; payer = buyer (CBEC) | CBEC limit raise reported, UNVERIFIED | **Hard** |
| Hong Kong | Everything (no tax; duty only on 4 excise groups) | None | 0% | 0% | n/a | None | None | **Easy** |
| Taiwan | ≤ NT$2,000, max 6 per buyer per half-year | None for goods | 5% business tax | about 10–12% (UNVERIFIED) | No FTA | EZ WAY real-name consent | Lower threshold under review (NT$1,000 for CN talked about); not enacted | **Medium-easy** |
| Japan | ≤ ¥10,000 (duty + JCT), **but knitwear, leather bags, leather shoes excluded**; 60% valuation for personal use | None today. **From 01-Apr-2028 the seller registers for JCT on ≤ ¥10,000 goods** | 10% JCT | knit about 7–11%, woven about 4–13%, leather shoes about 30% | VJEPA/CPTPP/RCEP, origin-dependent; small consignment waiver (UNVERIFIED) | Name, address, phone | FY2026 reform, LVG from 01-Apr-2028 (likely enacted) | **Medium** |
| South Korea | ≤ USD 150 total customs value (all-or-nothing) | None for goods | 10% VAT | 13% | VKFTA/AKFTA/RCEP, origin-dependent; ≤ USD 1,000 waiver (UNVERIFIED) | **PCCC mandatory**; name/phone/postal code must match (2026) | PCCC 1-year validity (2026–27), postal code match from 02-Feb-2026 | **Medium-easy** |
| India | None for sales (Rs 5,000 only for gifts) | None for goods | IGST 5% (≤ Rs 2,500/piece) or 18%, + BCD + SWS | BCD about 20% + 10% SWS | AIFTA impractical | **Consignee KYC** (Aadhaar/PAN/passport/DL/Voter ID) | GST 2.0 slabs from 22-Sep-2025 | **Hard** |
| Australia | No duty or border GST ≤ A$1,000 (customs value) | **Seller registers above A$75,000** (simplified, quarterly, ARN on docs) | 10% GST at sale | 5% (only above A$1,000) | Not needed | None | None found | **Easy** |
| New Zealand | No duty ≤ NZ$1,000 per item | **Seller registers above NZ$60,000** (IRD number) | 15% GST at sale | about 10% (only above NZ$1,000) | Not needed | None | None found | **Easy** |

## Implications for the MoR design (for the planner)

- **Tax jurisdiction modules** needed first: AU GST LVG and NZ GST LVG
  (collect at sale, aggregated turnover tracking against A$75,000 and
  NZ$60,000, quarterly or 2-monthly returns, ARN or IRD number printed on
  the commercial invoice). JP JCT LVG module for 01-Apr-2028. KR, TW and
  IN: border-collected duty and VAT estimates (landed-cost quote, not our
  output tax).
- **Per-item threshold tests** differ: AU per consignment on customs value;
  NZ per item excluding GST; JP per item from 2028 (today per consignment);
  KR per consignment including shipping, all-or-nothing; TW per
  consignment plus a per-buyer frequency counter.
- **Checkout data fields by country**: KR PCCC (+ name, phone, postal code
  match); TW EZ WAY prompt; IN KYC (prefer the courier handles it; avoid
  storing Aadhaar); CN ID (only if CBEC is ever pursued, plus a PIPL
  transfer review).
- **Aggregation risk**: KR combined taxation and AU/NZ registration
  thresholds both see the MoR as one supplier across all merchants.
- **Compliance flags (do not assume an answer)**: CN PIPL cross-border
  transfer of ID data; IN Aadhaar and DPDP handling; KR PIPA for the PCCC;
  JP tax agent requirement for non-resident JCT registration.

## Sources

Official:
- Japan Customs FAQ 1006 (¥10,000 exemption and exclusions):
  https://www.customs.go.jp/english/c-answer_e/imtsukan/1006_e.htm
- Korea Customs Service, list clearance: https://www.customs.go.kr/english/cm/cntnts/cntntsView.do?cntntsId=2733
- NZ Customs, duty and GST: https://www.customs.govt.nz/personal/duty-and-gst/
- NZ IRD, GST on low-value imported goods: https://www.ird.govt.nz/gst/gst-for-overseas-businesses/gst-on-low-value-imported-goods
- ATO, GST on low value imported goods: https://www.ato.gov.au/businesses-and-organisations/international-tax-for-business/gst-on-imported-goods-and-services/gst-on-low-value-imported-goods
- ATO, non-resident businesses and GST: https://www.ato.gov.au/businesses-and-organisations/international-tax-for-business/non-resident-businesses-and-gst
- HK Customs, declaration types: https://customs.gov.hk/en/cargo_clearance/declaration/lodgement/types
- Shanghai Government FAQ on personal postal articles: https://english.shanghai.gov.cn/en-FAQs-StudyinShanghai/20250925/3c88e77555e84dd3b515eaa0c9b02ca9.html
- Chennai Customs courier FAQ (KYC): https://chennaicustoms.gov.in/wp-content/uploads/2025/10/Faq.pdf

Advisers and secondary:
- BDO, Japan 2026 tax reform JCT: https://www.bdo.global/en-gb/insights/tax/indirect-tax/japan-2026-tax-reform-includes-jct-changes-that-affect-nonresident-sellers
- PwC Japan Tax Update 14-Jan-2026: https://www.pwc.com/jp/en/taxnews/pdf/jtu-20260114-en.pdf
- VATupdate, Japan ¥10,000 removal from April 2028: https://www.vatupdate.com/2026/01/07/japan-to-remove-%C2%A510000-tax-exemption-for-e-commerce-imports-from-april-2028/
- lookuptax, Japan 2028 (marked enacted): https://lookuptax.com/tax-changes/japan/low-value-import-platform-rules-2028
- Baker McKenzie, Japan de minimis customs review: https://globalimportblog.bakermckenzie.com/2026/01/15/japan-considers-revising-de-minimis-customs-threshold-to-address-e-commerce-surge/
- braindetox.kr, KR 2026 PCCC and threshold (citing KCS and Policy Briefing): https://braindetox.kr/en/posts/black_friday_2026_overseas_direct_purchase_customs_limit.html
- FedEx Korea regulatory notice (PCCC): https://www.fedex.com/en-kr/support/clearance/regulatory-services.html
- Daeryun Law FAQ (PCCC): https://foreign.daeryunlaw.com/en/faq/detail/513
- China Law & Practice, CBEC circular 2018: https://chinalawandpractice.com/2018/12/19/circular-on-improving-the-import-tax-policy-on-cross-border-e-commerce-retail
- China Daily, postal articles tax cut 2019: https://www.chinaservicesinfo.com/s/201904/06/WS5caaf9a4498e079e6801ce2c/personal-postal-articles-tax-to-be-cut.html
- VATupdate, China CBEC export returns No. 16/2026: https://www.vatupdate.com/2026/02/13/china-grants-tax-waivers-for-returned-cross-border-e-commerce-exports-within-six-months/
- Taiwan MOF review: https://www.ctee.com.tw/news/20250417700134-430104 ;
  https://finance.technews.tw/2025/04/10/small-packages-tax-free-may-canceled ;
  https://wantrich.chinatimes.com/news/20260107900289-420501
- Comgateway, TW NT$2,000 and 6/half-year: https://www.comgateway.com/sv/blogs/how-much-tax-and-duty-will-i-pay-for-shipping-from-the-usa-to-taiwan/
- The Core, India de minimis: https://www.thecore.in/business/shein-temu-hit-as-us-ends-de-minimis-what-are-indias-rules-like-820262
- Khaitan & Co, India GST rate change (retail) 19-Sep-2025: https://www.khaitanco.com/sites/default/files/2025-09/Ergo - GST Rate Change-LM-Retail sector - 19 September 2025.pdf
- Tribune India, Notification 33/2026-Customs (NT): https://www.tribuneindia.com/news/business/cbic-operationalises-reforms-to-boost-e-com-exports-from-april-1/
- PwC Australia GST update 2017-11 (ARN on documents): https://www.pwc.com.au/tax/taxtalk/assets/alerts/gst-update-14nov17.pdf

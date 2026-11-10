# 02 — Global express integrators and cross-border e-commerce / postal networks, origin Vietnam

Researcher slice: DHL Express, FedEx, UPS, Aramex, SingPost/Quantium, Asendia, Cainiao,
YunExpress, 4PX, Vietnam Post EMS, and e-commerce economy services from VN.
Research date: 2026-11-06.

**Limits of this research (read first).**
- fedex.com rate PDFs and the dhl.com / ups.com marketing pages blocked automated
  fetches (Akamai 403, timeouts). FedEx numbers below come from search-engine extracts of
  the official PDFs, not from opening the PDFs. Before you rely on them, open the PDFs in
  a browser.
- The shared web-search budget ran out partway through. Returns products (FedEx Global
  Returns, UPS Returns from VN), UPS Track Alert signing and Aramex API details were not
  researched online. Where this file states them it says so, and they are UNVERIFIED.
- The UPS guide used here is the **2025** Vietnam Rate and Service Guide (effective
  2024-12-22), downloaded and parsed in full. A 2026 guide (effective 2025-12-21) exists
  but would not download. Assume roughly +5–6% for 2026 [estimate, based on DHL's
  published +5.9% GRI for VN 2026].
- FX used for the USD figures: **1 USD ≈ 26,300 VND** [estimate]. All carrier list prices
  are in VND, exclude VAT and fuel, and are **before discount**.

Source-type labels: **[published]** carrier rate card or doc, **[calculator]** public quote
tool (none was reachable for this slice), **[estimate]** inference or third-party report.

---

## 0. Key findings

1. **Only the three integrators (DHL Express, UPS, FedEx) give proven, self-serve DDP from
   VN to the whole world** with a REST API. Each has a published per-shipment fee for
   billing duties to the shipper, about **VND 600k ≈ USD 23 per shipment** (DHL "Duty Tax
   Paid": VND 600,000 or 2% of duties/taxes, whichever is higher [published 2026]; UPS
   "Duty/Tax Forwarding Surcharge": VND 600,425 [published 2025]; FedEx: the fee exists,
   amount UNVERIFIED). On a USD 30–60 apparel order, that fee alone is a large share of
   margin.
2. **There is no ready-made economy e-commerce product from VN** comparable to the
   China-origin ones:
   - **DHL eCommerce stopped operating in Vietnam in 2021** ([VIR](https://vir.com.vn/dhl-ecommerce-stops-operation-in-vietnam-88608.html)).
   - UPS Worldwide Economy from VN covers **US only**.
   - FedEx International Connect Plus (FICP) launched in VN in Feb 2025 and is the closest
     "economy express" product.
   - Asendia has a VN sales presence and says it offers DDP.
   - For Cainiao, YunExpress and 4PX, VN-origin products for third-party merchants are
     UNVERIFIED.
3. **List prices are high and the fuel surcharge is huge.** The DHL VN fuel surcharge is
   **50.00% for 9–15 Nov 2026** [published,
   [DHL VN surcharges](https://mydhl.express.dhl/vn/en/ship/surcharges.html)]. The UPS list
   price for a 0.5 kg parcel ranges from about USD 58 (SG/TH/HK) to USD 111 (Africa) before
   fuel. Negotiated or reseller discounts of 40–70% off list are common in VN
   [estimate, UNVERIFIED].
4. **Volumetric weight matters for M and L parcels.** All three integrators use divisor
   **5000** and round up to the next 0.5 kg (express) or 1 kg (UPS Expedited):
   - M (30×20×10) bills **1.5 kg** instead of 1 kg.
   - L (35×25×15) bills **3.0 kg** instead of 2 kg.

   VN Post EMS uses divisor **6000** (M bills 1.0 kg, L bills 2.5 kg).
5. **Regulatory tailwinds for express DDP:**
   - The US ended de minimis for all countries on 2025-08-29, and the postal flat-rate
     option ended 2026-02-28.
   - The EU introduced a €3 duty per low-value item from 2026-07-01, with VAT from the
     first euro and IOSS still in place.

   Both make carrier-handled DDP the default path, and both broke many postal routes
   ([CBS](https://www.cbsnews.com/news/de-minimis-us-tariffs-exemption-low-value-imports-ends),
   [UPU webinar](https://www.upu.int/getmedia/20be6793-594a-4f0c-aca3-cd7f17d21270/2025-08-27_WebinarUsDeMinimisSuspension_Presentations_IB-UPU.pdf),
   [DHL on EU 2026](https://www.dhl.com/discover/en-cz/logistics-advice/import-export-advice/eu-2026-duty-de-minimis-removal),
   [Easyship on EU €3](https://www.easyship.com/blog/eu-150-de-minimis-ends-for-ecommerce-sellers)).
   - **US tariff on VN goods:** one source reports that the country-specific IEEPA
     tariffs were struck down and replaced by a 10% global tariff from 2026-02-24
     (UNVERIFIED, single source). Our landed-cost engine needs this rate.

---

## 1. Reference parcels: billable weight

| Parcel | Actual | Dims (cm) | Vol ÷5000 | Billable, express (0.5 kg steps) | Billable, UPS Expedited (1 kg steps) | Vol ÷6000 (EMS) | Billable, EMS |
|---|---|---|---|---|---|---|---|
| XS | 0.25 | 20×15×3 | 0.18 | 0.5 | 1 | 0.15 | 0.25 (EMS steps UNVERIFIED) |
| S | 0.5 | 25×20×5 | 0.50 | 0.5 | 1 | 0.42 | 0.5 |
| M | 1.0 | 30×20×10 | 1.20 | **1.5** | **2** | 1.00 | 1.0 |
| L | 2.0 | 35×25×15 | 2.63 | **3.0** | **3** | 2.19 | **2.5** |
| XL | 5.0 | 40×30×20 | 4.80 | 5.0 | 5 | 4.00 | 5.0 |

Divisors are [published]:
- UPS: 5,000 ([UPS VN RSG 2025](https://www.ups.com/assets/resources/webcontent/en_GB/Rates_and_Service_Guide_preview_VN.pdf)).
- UPS Worldwide Economy: 5,000 ([UPS WWE VN](https://ups.com/assets/resources/webcontent/en_GB/WWE_Rates_and_Service_Guide_VN.pdf)).
- EMS VN: 6,000 ([vietnamspot.ru, Oct 2026](https://vietnamspot.ru/en/blog/vietnam-parcels), third party).
- DHL and FedEx: 5,000 is their standard international divisor [estimate, not confirmed on
  a VN page].

**Action for merchants:** flat-pack apparel in poly mailers. M and L in boxes waste
0.5–1 kg of billed weight.

---

## 2. Region × provider summary

Prices are **list, before discount, excluding fuel and VAT**, per parcel. Transit is
business days door to door. "–" means not offered or unknown.

### 2a. Price by region for the S parcel (0.5 kg billable)

| Region (example) | UPS Worldwide Express Saver 0.5 kg [published 2025] | UPS Worldwide Expedited 1 kg [published 2025] | FedEx IP 0.5 kg [published 2026, promo sheet, via search extract] | FedEx FICP 1 kg [published 2026, promo sheet, via search extract] | VN Post EMS 1 kg [estimate, 3rd-party read of EMS calculator, Oct 2026] |
|---|---|---|---|---|---|
| SEA: SG, TH (UPS z1) / MY, ID, PH (UPS z2) | VND 1,534,315 (~$58) / 1,675,315 (~$64) | 1,345,610 (~$51) / 1,572,385 (~$60) | Zone A (SG): 1,245,900 (~$47). MY/TH/ID/PH zone UNVERIFIED | Zone A (SG/HK/S.China): 1,274,000 (~$48) | UNVERIFIED |
| East Asia: HK (z1); CN, JP, KR, TW (z3) | HK z1 ~$58; others 1,852,740 (~$70) | HK ~$51; others 1,862,610 (~$71) | B (CN/JP/TW): 1,467,600 (~$56) | B: 1,623,700 (~$62) | UNVERIFIED |
| South Asia: IN (z4) | 1,937,340 (~$74) | 1,919,010 (~$73) | C (AU/IN/NZ): 1,536,400 (~$58) | C: 1,687,800 (~$64) | UNVERIFIED |
| Oceania: AU, NZ (z3) | 1,852,740 (~$70) | 1,862,610 (~$71) | C: ~$58 | C: ~$64 | AU 1,383,739 (~$53) |
| North America: US, CA, MX (z5) | 2,356,580 (~$90) | 1,971,650 (~$75) | D: 1,689,100 (~$64) | D: 1,854,500 (~$71) | US 1,831,162 (~$70); CA 1,712,362 (~$65) |
| Europe: EU, UK, CH (z6) | 2,229,210 (~$85) | 2,049,670 (~$78) | E (DE/FR/UK): 1,572,700 (~$60) | E: 1,752,200 (~$67) | UK 1,310,299 (~$50); DE 1,696,334 (~$65) |
| GCC: AE, SA (z7) | 2,466,560 (~$94) | 2,188,555 (~$83) | H (SA): 2,355,700 (~$90) | H: 2,632,800 (~$100) | UNVERIFIED |
| LatAm: BR etc. (z8) | 2,628,710 (~$100) | 2,662,315 (~$101) | G (BR/CL): 2,102,600 (~$80) | G: 2,370,100 (~$90) | UNVERIFIED |
| Africa: ZA, NG, KE (z9) | 2,918,230 (~$111) | 3,043,955 (~$116) | H (ZA/NG): ~$90 | H: ~$100 | UNVERIFIED |

Notes on the table:
- **UPS zones** come from the 2025 VN Worldwide Zone Chart (export, Saver column) [published]:
  - z1: SG, TH, HK
  - z2: MY, ID, PH, Macau
  - z3: CN, JP, KR, TW, AU, NZ
  - z4: IN
  - z5: US, CA, MX
  - z6: EU, UK, CH
  - z7: AE, SA
  - z8: BR and LatAm
  - z9: ZA, NG, KE
  - z10: Southern China
- **FedEx zone letters** (A to H) come from the 2026 VN zone index
  (`sources/fedex/fedex-rates-zi-en-vn-2026.pdf`), the same letters for the list
  tariff and the promo sheets: MY, TH, ID, PH, SG A; JP, KR B; AU C; US D; DE, NL,
  GB E; AE H [published, verified 2026-11-10]. The index has no FICP column; FICP
  zones are assumed equal to IP.
  - Sources: [fedex-rates-exp-en-vn-2026.pdf](https://www.fedex.com/content/dam/fedex/international/rates/fedex-rates-exp-en-vn-2026.pdf),
    [fedex-rates-ficp-exp-en-vn-2026.pdf](https://www.fedex.com/content/dam/fedex/international/rates/fedex-rates-ficp-exp-en-vn-2026.pdf),
    [fedex-rates-box-en-vn-2026.pdf](https://www.fedex.com/content/dam/fedex/international/rates/fedex-rates-box-en-vn-2026.pdf).
  - All three are effective 2026-01-05 and exclude fuel and special fees. Saved by hand
    2026-11-10 and loaded 0.5–20 kg as `fedex_ip_vn_2026_promo`, `fedex_ie_vn_2026_promo`
    and `fedex_ficp_vn_2026_promo` (verified). "Qualified customers" means any VN FedEx
    account holder in good standing who settles invoices promptly in Vietnam
    (Miscellaneous Information in the surcharge PDF).
- **FedEx FICP is about 18–20% below the IP promo rate at every weight** (verified
  2026-11-10): 0.5 kg zone A VND 1,137,400 vs IP 1,407,800; 1 kg zone D 1,854,500 vs
  2,291,100. The earlier "barely cheaper" reading compared FICP 1 kg with the FedEx
  Envelope rate, which our unverified data had loaded as IP 0.5 kg. IE promo is only
  1–3% below IP promo. FICP has no Envelope or Pak rates and takes no dangerous goods.
- **FedEx has two sheets per service.** A separate FICP "tariff" sheet
  ([fedex-rates-ficp-cm-en-vn-2026.pdf](https://www.fedex.com/content/dam/fedex/international/rates/fedex-rates-ficp-cm-en-vn-2026.pdf))
  shows 0.5 kg values of "84.30 … 151.50". These look like USD, which would put the tariff
  about 40% above the VND promo sheet. Currency UNVERIFIED.
- **DHL Express**: no VN rate card could be retrieved. DHL VN does not publish one online;
  the press release only gives the +5.9% average GRI from 2026-01-01
  ([DHL VN press](https://www.dhl.com/vn-en/home/press/press-archive/2025/dhl-express-announces-annual-price-adjustments-for-2026-in-vietnam.html)).
  Expect DHL list prices close to UPS Saver [estimate]. **DHL per-parcel prices are
  UNVERIFIED.**

### 2b. UPS published export list rates for all reference parcels (2025, VND, ~USD)

**UPS Worldwide Express Saver** (non-documents, billable weight per §1) [published]

| Billable kg (parcel) | z1 SG/TH/HK | z2 MY/ID/PH | z3 CN/JP/KR/TW/AU/NZ | z4 IN | z5 US/CA/MX | z6 EU/UK/CH | z7 GCC | z8 LatAm | z9 Africa |
|---|---|---|---|---|---|---|---|---|---|
| 0.5 (XS, S) | 1,534,315 (~$58) | 1,675,315 (~$64) | 1,852,740 (~$70) | 1,937,340 (~$74) | 2,356,580 (~$90) | 2,229,210 (~$85) | 2,466,560 (~$94) | 2,628,710 (~$100) | 2,918,230 (~$111) |
| 1.5 (M) | 1,872,010 (~$71) | 2,018,885 (~$77) | 2,428,960 (~$92) | 2,474,080 (~$94) | 3,355,095 (~$128) | 3,251,225 (~$124) | 3,607,015 (~$137) | 3,792,430 (~$144) | 4,246,920 (~$161) |
| 3.0 (L) | 2,351,410 (~$89) | 2,520,140 (~$96) | 3,304,100 (~$126) | 3,290,470 (~$125) | 4,825,725 (~$183) | 4,681,200 (~$178) | 5,326,980 (~$203) | 5,549,760 (~$211) | 6,166,635 (~$234) |
| 5.0 (XL) | 2,949,485 (~$112) | 3,132,315 (~$119) | 4,450,195 (~$169) | 4,419,645 (~$168) | 6,733,925 (~$256) | 6,372,025 (~$242) | 7,631,155 (~$290) | 7,888,950 (~$300) | 8,496,660 (~$323) |

**UPS Worldwide Expedited** (non-documents, rounded up to whole kg) [published]

| Billable kg (parcel) | z1 | z2 | z3 | z4 | z5 | z6 | z7 | z8 | z9 |
|---|---|---|---|---|---|---|---|---|---|
| 1 (XS, S) | 1,345,610 (~$51) | 1,572,385 (~$60) | 1,862,610 (~$71) | 1,919,010 (~$73) | 1,971,650 (~$75) | 2,049,670 (~$78) | 2,188,555 (~$83) | 2,662,315 (~$101) | 3,043,955 (~$116) |
| 2 (M) | 1,607,870 (~$61) | 1,868,955 (~$71) | 2,363,630 (~$90) | 2,385,955 (~$91) | 2,652,680 (~$101) | 2,816,240 (~$107) | 3,014,110 (~$115) | 3,640,385 (~$138) | 4,183,470 (~$159) |
| 3 (L) | 1,857,675 (~$71) | 2,142,260 (~$81) | 2,874,520 (~$109) | 2,862,535 (~$109) | 3,329,715 (~$127) | 3,510,900 (~$133) | 3,835,435 (~$146) | 4,606,235 (~$175) | 5,241,675 (~$199) |
| 5 (XL) | 2,330,025 (~$89) | 2,662,315 (~$101) | 3,871,860 (~$147) | 3,845,070 (~$146) | 4,646,420 (~$177) | 4,778,960 (~$182) | 5,494,535 (~$209) | 6,547,805 (~$249) | 7,222,255 (~$275) |

Expedited zone assignment is assumed equal to Saver for these countries (it was in the
extracted chart). Source:
[UPS VN Rate and Service Guide 2025](https://www.ups.com/assets/resources/webcontent/en_GB/Rates_and_Service_Guide_preview_VN.pdf).

**UPS Worldwide Economy, VN → US 48 states only, DDP** [published, effective 2024-07-15]
- Rates: 0.5 kg VND 1,541,694 (~$59); 1 kg 1,869,378 (~$71); 1.5 kg 2,194,923 (~$83);
  2 kg 2,515,040 (~$96); 2.5 kg 2,844,534 (~$108); 3 kg 3,157,084 (~$120); 5 kg 4,405,310
  (~$168).
- Service terms:
  - Fuel surcharge 5%.
  - Average transit 5–8 business days.
  - Maximum value USD 800 per package; liability up to USD 100.
  - Divisor 5,000.
- **DDP is mandatory**: duties, taxes and clearance fees are billed to the shipper.
- Using UPS's Landed Cost Calculator adds a fee of 10% of the duties.
- The pricing is uncompetitive against discounted Saver, and it is a single lane. Low
  value for us.
- Source: [UPS WWE VN](https://ups.com/assets/resources/webcontent/en_GB/WWE_Rates_and_Service_Guide_VN.pdf).

**VN Post EMS** [estimate, a third party reading the EMS VN calculator, 2026-10-24]

| Destination | 1 kg | 5 kg |
|---|---|---|
| UK | VND 1,310,299 (~$50) | 3,165,826 (~$120) |
| AU | 1,383,739 (~$53) | 3,367,267 (~$128) |
| DE | 1,696,334 (~$65) | 4,002,437 (~$152) |
| CA | 1,712,362 (~$65) | 4,277,103 (~$163) |
| US | 1,831,162 (~$70) | 4,871,103 (~$185) |

- EMS is not cheap from VN. It is roughly equal to a discounted express rate, with worse
  service and a postal DDP path that is UNVERIFIED.
- Prices for registered air parcels and small packets are not published (counter only).
- Source: [vietnamspot.ru](https://vietnamspot.ru/en/blog/vietnam-parcels).

### 2c. Transit times (business days, door to door, from HCMC/Hanoi)

| Region | DHL Express Worldwide | UPS Saver | UPS Expedited | FedEx IP | FedEx FICP | VN Post EMS | Asendia e-PAQ |
|---|---|---|---|---|---|---|---|
| SEA | 1–3 [estimate] | 1–3 [estimate] | 3–5 [published "as little as 3 days within Asia"] | 1–3 [estimate] | 2–5 [estimate] | UNVERIFIED | UNVERIFIED |
| East Asia | 1–3 [estimate] | 1–3 [estimate] | 3–5 [published] | 1–3 [estimate] | 2–5 [estimate] | UNVERIFIED | UNVERIFIED |
| India | 2–4 [estimate] | 2–4 [estimate] | 3–6 [estimate] | 2–4 [estimate] | 3–6 [estimate] | UNVERIFIED | UNVERIFIED |
| Oceania | 2–4 [estimate] | 2–4 [estimate] | 3–6 [estimate] | 2–4 [estimate] | 3–6 [estimate] | 4–8 [estimate, 3rd party] | UNVERIFIED |
| US/CA | 2–3 [published DHL VN marketing: "Europe and Americas 2–3 working days"] | 2–4 [estimate] | 3–5 to major centres [published] | 2–4 [estimate] | 4–7 [estimate] | 6–12 [estimate, 3rd party] | UNVERIFIED |
| Europe | 2–3 [published, same DHL statement] | 2–4 [estimate] | 3–5 to major centres [published] | 2–4 [estimate] | 4–7 [estimate] | 3–8 central/west, 6–10 north/south [estimate, 3rd party] | UNVERIFIED |
| GCC | 2–4 [estimate] | 3–5 [estimate] | – | 3–5 [estimate] | 4–7 [estimate] | UNVERIFIED | UNVERIFIED |
| LatAm | 3–6 [estimate] | 3–6 [estimate] | 4–7 [estimate] | 3–6 [estimate] | 5–9 [estimate] | UNVERIFIED | UNVERIFIED |
| Africa | 3–7 [estimate] | 4–7 [estimate] | – | 4–7 [estimate] | 5–10 [estimate] | UNVERIFIED | UNVERIFIED |

UPS service windows are from the UPS VN guide: Saver is "guaranteed by end of day";
Expedited is "as little as 3 business days within Asia, and from Asia to major business
centres in EU / North and South America". UPS Worldwide Economy VN → US: 5–8 days
[published].

---

## 3. Provider details

### 3.1 DHL Express (Vietnam)

**Integration**
- API: MyDHL API v3.x, REST over HTTPS with JSON.
- Auth: Basic Auth with credentials issued by DHL.
- Environments:
  - Test: `https://express.api.dhl.com/mydhlapi/test`, limited to 500 calls per day
    per credential set.
  - Production: `https://express.api.dhl.com/mydhlapi`.
- Endpoints: rating, landed cost (duties and taxes estimate from HS code), shipment
  (label plus customs documents, PLT invoice upload, pickup), tracking, address
  validation, service points, get image (waybill, invoice, customs entry), and label-less
  returns.
- **An active DHL Express account is required before API access.** Activation takes
  about 1–2 business days after the account exists, and may require an API usage
  declaration. ([DHL developer portal](https://developer.dhl.com/api-reference/dhl-express-mydhl-api);
  [AfterShip guide](https://support.aftership.com/en/articles/15380679-dhl-express-developer-guide-api-credentials))
  [published / 3rd party]
- Labels: PDF is confirmed. ZPL/EPL are UNVERIFIED (commonly supported).
- **Webhooks:** none confirmed for DHL Express.
  - The DHL "Shipment Tracking – Unified Push" API delivers HTTP POSTs that must be
    answered with 200 within 5 s, with a retry after 1 h.
  - A DHL support article says the push API was in beta, and its scope table says Express
    does not support subscription by shipment or account
    ([DHL support](https://support-developer.dhl.com/support/solutions/articles/47001224414-is-there-an-api-with-push-notifications-or-updates-for-tracking-shipments-)).
  - **Plan for polling the tracking API for DHL Express.** Push signature scheme: not
    documented (UNVERIFIED).
- Minimum volume: none published. Account opening needs a VN billing entity, or
  third-country billing to a DHL account abroad (e.g. DHL NL or SG). Whether a SG or NL
  entity can hold a VN export account is UNVERIFIED. Ask DHL VN sales (hotline
  1800 1530).

**Coverage and DDP**
- About 220 countries. DHL clears customs at destination.
- **DDP = "Duty Tax Paid" (DTP)** [published, effective 2026-01-01]: VND 600,000 or 2% of
  fiscal charges, whichever is higher, per shipment, billed to the shipper.
- With DTP, extra clearance charges are also billed to the DTP shipper when they occur:
  - Clearance processing: VND 280,000.
  - Physical intervention: VND 575,000.
  - Non-routine entry: VND 1,215,000.
  - Multiline entry: VND 150,000 per line above 5 lines.
  - Permits: VND 1,300,000.
  - Post-clearance modification: VND 2,300,000.

  Sources: [optional services](https://mydhl.express.dhl/vn/en/ship/optional-services.html),
  [customs services](https://mydhl.express.dhl/vn/en/ship/customs-services.html).
- Without DTP, the default is DTU (receiver pays, plus a "Duty Tax Processing" fee at
  destination). That is a bad experience for buyers who already paid tax at our checkout.
- Duties appear on a separate duty invoice to the DTP account. Invoice import format
  (EDI or CSV) is UNVERIFIED.

**Surcharges (VN 2026)** [published,
[surcharges](https://mydhl.express.dhl/vn/en/ship/surcharges.html)]
- Fuel: 50.00% (week of 9–15 Nov 2026), indexed weekly to US Gulf Coast jet fuel.
- Remote area: VND 735,000 or 15,000/kg, whichever is higher.
- Elevated risk / restricted destination: VND 918,000.
- Address correction: VND 350,000.
- Oversize piece (>100 cm): VND 615,000.
- Demand surcharge: per kg, for 2026-10-01 to 2027-02-05; amount not captured.
- Residential delivery: VND 150,000 [published, optional services page]. This matters
  because B2C is all residential.

**Returns**
- MyDHL API can create return labels on demand or in advance, including label-less /
  QR returns (needs a DHL agreement). Return label validity is 3, 6 or 12 months
  ([ShipStation docs](https://docs.shipstation.com/dhl-express-mydhl)).
- International returns run on the import product ("Import Express"), billed to an
  import account. Inbound returns into VN need VN customs re-import handling, an
  UNVERIFIED cost.

**Claims / restricted goods**
- Shipment Value Protection: VND 315,000 or 1.5% of insured value, whichever is higher.
- Apparel and accessories are unrestricted. Watch for leather/exotic skins (CITES) and
  brand-name goods (counterfeit checks).

**Cost to integrate**
- No platform fee [published: none mentioned].
- Engineering: about 8–12 days for rate, ship, label, poll tracking, DTP and returns
  [estimate].

### 3.2 FedEx (Vietnam)

**Integration**
- FedEx Developer Portal: REST/JSON with OAuth2 client credentials, sandbox available.
- APIs: Ship, Rate, Track, Pickup, Trade Documents Upload (ETD), Global Trade (duties
  estimate) and Advanced Integrated Visibility (AIV) webhooks.
- **AIV webhooks are HMAC-signed with a 26–100 character shared secret.** Subscription is
  by account or by tracking number, and missed events are retried
  ([FedEx AIV](https://developer.fedex.com/api/en-us/catalog/shipment-visibility-webhook/docs/tracking-number-subscription/csp-tracking-number-subscription.html),
  [service description](https://developer.fedex.com/api/en-us/legal/ShipmentVisibilityWebhookServiceDescription.html))
  [published]. This is the best webhook story of the three. AIV may carry a fee
  (UNVERIFIED).
- Labels: PDF/PNG/ZPLII/EPL2 (generally documented for FedEx Ship API; not re-verified
  for VN).
- **A FedEx account number is required for FICP non-document shipments (from
  2025-09-15).**
- **The FedEx duties-estimate API may not be resold through a 3rd-party reseller
  solution** ([FedEx APAC PDF](https://www.fedex.com/content/dam/fedex/apac-asia-pacific/downloads/fedex-api-estimated-duties-taxes-en-apac.pdf)).
  Relevant: as MoR we calculate landed cost ourselves.
- Whether VN accounts can get Developer Portal production keys is UNVERIFIED (not found).
- FedEx VN publishes "Global Third-Party pricing", so a non-VN account can be billed for
  VN-origin shipments
  ([FedEx VN G3P](https://www.fedex.com/en-vn/shipping/rates/global-third-party.html)).
  This is a possible route for a SG or NL entity (UNVERIFIED in detail).

**Services from VN**
- International Priority (IP) and International Priority Express (IPE).
- International Economy (IE).
- **FedEx International Connect Plus (FICP)**: day-definite e-commerce service, launched
  in VN 2025-02-04
  ([VIR](https://vir.com.vn/fedex-expands-international-connect-plus-service-to-vietnam-122288.html)).
  - No dangerous goods; no Envelope or Pak.
  - Promo export rates are published for 2026.
  - Origin/destination pairs are limited and must be confirmed with sales.

**DDP**
- Bill duties to the sender or a third party: FedEx advances duties and charges the payer
  the duties plus an "ancillary clearance / disbursement" surcharge.
- **The VN amount is UNVERIFIED** (the VN surcharge page is
  [here](https://www.fedex.com/en-vn/shipping/rates/customized-rates/surcharges.html)).
  Customs clearance surcharges were revised 2026-07-20
  ([FedEx PDF](https://www.fedex.com/content/dam/fedex/apac-asia-pacific/downloads/fedex-customs-clearance-surcharge-20july2026.pdf)).
- A third-party billing surcharge applies when billed to a third party.

**Returns**
- FedEx Global Returns (return labels created by the shipper through the API, printed or
  emailed to the buyer). Availability from SEA destinations back to VN is UNVERIFIED (not
  researched; search budget exhausted).

**Demand surcharges**
- FedEx VN publishes rolling demand-surcharge sheets (e.g. Oct 19–Nov 1 and from Nov 2,
  2026 [published PDFs listed on fedex.com, amounts not read]).

**Cost to integrate**
- No platform fee. Engineering: about 8–12 days, fewer if webhooks replace polling
  [estimate].

### 3.3 UPS (Vietnam)

**Integration**
- developer.ups.com: REST with OAuth2 and a sandbox (CIE).
- APIs: Rating, Shipping, Tracking, Paperless Invoice, Landed Cost Quote, Pickup,
  Address Validation, and Track Alert (webhook).
- Track Alert payload signing: UNVERIFIED.
- Labels: GIF/PNG/ZPL/EPL/SPL (general UPS docs, not re-verified).
- A UPS account is needed. Production access follows account approval [estimate, about
  1–2 weeks].
- The VN developer page exists, but returned 403 to the fetcher.

**Services from VN** (2025 guide)
- Worldwide Express Plus / Express / **Express Saver** (end-of-day guaranteed, 200+
  countries).
- **Worldwide Expedited** (day-definite, 3–5 days, money-back guarantee only for selected
  lanes).
- Worldwide Economy (US only, DDP mandatory).

**DDP**
- Billing option "Duty/Tax Forwarding": **VND 600,425 surcharge per shipment billed to
  the shipper**, plus the duties and taxes themselves [published 2025].
- Disbursement fee: 2.4% with a minimum of VND 215,140. This is quoted for *imports into
  VN*; the export-side equivalent at destination is UNVERIFIED.
- Other relevant fees:
  - Residential surcharge: VND 92,355 per shipment.
  - Extended area: VND 631,210 or 9,870/kg, whichever is higher.
  - Remote area: VND 704,060 or 9,870/kg, whichever is higher.
  - Rebill: VND 235,000.
  - Paper commercial invoice: VND 235,000. Use paperless invoices.
  - Prohibited item fee: VND 7,050,000.

**Returns**
- UPS Returns / Import Control (UPS creates the return label for a buyer abroad).
  Availability and pricing for lanes into VN are UNVERIFIED.

**Cost to integrate**
- No platform fee. Engineering: about 8–12 days [estimate].

### 3.4 Aramex
- Aramex is in VN only through a local agent ("Công ty CP Hợp Nhất Quốc Tế", listed as
  sole strategic representative of Aramex UAE; HQ in Hanoi, office in Tân Sơn Nhất,
  HCMC), not as a group subsidiary ([Trang Vàng listing](https://www.trangvangvietnam.com/tagclass/40419935/tim-cong-ty-chuyen-phat-nhanh.html),
  [yellowpages.vn](https://yellowpages.vn/pdetail/1188037278/aramex.com)) [estimate].
- Strength: GCC, Middle East and Africa last mile, which is where the integrators are
  most expensive.
- Unclear whether the VN agent can issue labels through the Aramex API, and what DDP and
  rates it offers: UNVERIFIED.
- Treat Aramex as a later, GCC-specific option. A more likely route is VN → integrator to
  hub → Aramex last mile [estimate].

### 3.5 SingPost / Quantium Solutions
- SingPost has been shrinking cross-border:
  - Cross-border e-commerce volumes fell **63% YoY in 1H FY25/26**.
  - The Quantium Solutions sale to Morning Global (HK/TH/MY/ID entities) was agreed in
    July 2025, and the 1H FY26 deck refers to the "cessation of the QS JV".
  - The 4PX stake was sold to Cainiao in June 2025.
  - The cross-border business was folded back into SingPost Singapore postal.
  - Sources: [SingPost 1HFY2026 deck](https://links.sgx.com/1.0.0/corporate-announcements/EKLQ22JCMG59ERJ5/866427_Results_Presentation_1HFY2026.pdf),
    [Post&Parcel](https://postandparcel.info/158951/news/singpost-simplifies-its-investment-portfolios/)
    [published].
- **No VN-origin product found.** Using it would mean line-haul VN → SG and injecting at
  SingPost: a consolidator model, with DDP UNVERIFIED.
- Not a near-term fit.

### 3.6 Asendia
- Has a Vietnamese site aimed at VN sellers ([vn.asendia.com](https://vn.asendia.com/homepage))
  [published]:
  - 200+ destinations; e-PAQ in 4 tiers, of which only e-PAQ Select (≤30 kg) is named.
  - **Offers both DDP and DAP.**
  - e-PAQ Returns with domestic return addresses and prepaid QR return labels in the
    destination country. This is the best documented cross-border returns product in
    this slice.
  - Local contact: duong.nguyen@asendia.com.
- Not on the site: rates, transit, API details (Asendia generally has a REST API; VN
  onboarding UNVERIFIED), pickup in VN, minimum volume. **Worth an RFQ for EU/UK/US
  economy DDP.**

### 3.7 Cainiao
- Footprint in VN: an automated sorting centre (2025), warehouses (Mekong Delta, MOU
  with SG Sagawa VN), and VN among 10 APAC markets with more than 20 facilities
  ([Post&Parcel](https://postandparcel.info/158794/news/e-commerce/cainiaos-new-facility-set-to-enhance-vietnams-rapidly-growing-e-commerce-sector),
  [VIR](https://vir.com.vn/cainiao-expands-warehousing-and-fulfillment-network-across-10-apac-markets-132449.html)).
- Its global "5-day / 10-day" products are China-origin.
- Alibaba's 2024 logistics service for VN sellers uses **UPS and DHL** as carriers, not a
  Cainiao line ([VietNamNet](https://vietnamnet.vn/en/alibaba-launches-online-logistics-service-to-support-vietnamese-sellers-2312782.html)).
- **VN-origin global network for third-party merchants: UNVERIFIED (likely not
  available).**

### 3.8 YunExpress
- A legal entity exists: YunExpress Logistics Vietnam Co., Ltd., in HCMC (Thu Duc and Go
  Vap addresses), 500–999 staff, and job ads for "cross-border logistics business
  development in Vietnam" ([CareerViet](https://careerviet.vn/en/nha-tuyen-dung/yunexpress-logistics-vietnam.35A98979.html),
  [TopCV](https://www.topcv.vn/viec-lam/truong-phong-kinh-doanh-thu-nhap-up-to-2000usd-tai-ho-chinh-minh/2310232.html)).
- No public VN-origin product, rates or lanes were found. **UNVERIFIED.**
- YunExpress has a REST API and DDP lines to the US and EU from China [estimate].
- Worth an RFQ, because a VN-origin YunExpress line would be the cheapest "economy DDP
  to US/EU" option if it exists.

### 3.9 4PX
- No VN-origin product was found. The only VN link is a 2020 Boxme × 4PX partnership for
  "SEA-Global" shipping ([Boxme](https://boxme.asia/author/mark/page/10)).
- 4PX is now further inside Cainiao (SingPost sold its stake to Cainiao in 2025).
- **UNVERIFIED / unlikely.**

### 3.10 Vietnam Post EMS international
- Prices: see §2b. EMS transit: EU 3–10 days, North America 6–12, Oceania 4–8 working
  days [estimate, 3rd party].
- Volumetric divisor 6,000.
- Unknown:
  - API for international shipments (VNPost has domestic APIs).
  - DDP: VNPost's UPU DDP participation is UNVERIFIED.
  - US acceptance after the de minimis change: many posts suspended US parcels, then
    returned on DDP-only terms. VNPost status UNVERIFIED.
- Low fit for an API-first MoR, but a possible fallback for heavy parcels to AU and UK.

### 3.11 E-commerce economy services from VN

| Product | Status from VN |
|---|---|
| DHL eCommerce (Parcel International Direct) | **Not available.** DHL eCommerce stopped operating in VN in 2021. DHL Express remains. |
| FedEx International Connect Plus | **Available since 2025-02-04**, promo VND rates published for 2026, account required. |
| UPS Worldwide Economy | **US only**, DDP mandatory, 5–8 days. |
| Asendia e-PAQ | Sales presence in VN, DDP/DAP, rates on RFQ. |
| Cainiao, YunExpress, 4PX, SingPost | No confirmed VN-origin product. |

---

## 4. DDP mechanics (integrators)

1. When creating the shipment we set the duty-payer to the shipper or a third party:
   - DHL: DTP value-added service plus Incoterm DDP.
   - UPS: Duty/Tax Forwarding (shipment charge for duties billed to the shipper).
   - FedEx: `dutiesPayment.paymentType = SENDER` or `THIRD_PARTY` [estimate based on
     FedEx Ship API conventions].

   We send a paperless commercial invoice with HS codes and values.
2. The carrier clears customs in the destination country as importer's broker, advances
   duties and taxes, and bills them to our account on a separate duty/tax invoice, plus:
   - the DDP admin fee (about USD 23 per shipment at DHL and UPS);
   - any disbursement or clearance extras.
3. **Reconciliation need:** the duty invoice amounts will differ from our checkout
   estimate because of FX, customs valuation rules, CIF vs FOB value and extra charges.
   The courier module must import these invoices (EDI/CSV/PDF; formats UNVERIFIED for
   all three in VN) and book the difference in the ledger.
4. **Registration:** for EU IOSS, UK VAT, AU/NZ GST and SG GST on low-value goods, we as
   MoR are the registered seller and charge the tax at checkout. We then pass the IOSS
   number or VAT ID on the shipment so the carrier does not collect again. Supported
   field names per carrier are UNVERIFIED. This interacts with the tax modules.
5. **Cost per parcel to watch:** the DDP admin fee is fixed per shipment (USD ~23), on
   top of freight and fuel (DHL 50% fuel). For a 0.5 kg apparel parcel the all-in list
   cost to the EU or US exceeds USD 120 before discount [estimate].

---

## 5. Discounts and onboarding at low volume (500 → 5k → 50k parcels/month)

- **Integrators** [estimate, no public source]:
  - At about 500 parcels/month a direct account in VN typically gets **30–50% off
    export list**.
  - At 5k+ a month, 60–75% is plausible.
  - FedEx publishes "promotional" and "special pricing" VN sheets for qualified VN payers,
    which shows that tiers are pre-defined.
- **Resellers/consolidators** in VN (forwarders holding big DHL/UPS/FedEx accounts) often
  beat a direct small account [estimate]. The trade-off:
  - labels are issued on their account;
  - API access goes through them;
  - DDP billing passes through them.

  For an MoR that must own duty reconciliation and audit trails, this adds a party.
- **Entity question:** carriers open export accounts against a local billing entity. A
  SG Pte Ltd or NL BV would either:
  - register in VN (licensing question, see AGENTS.md compliance rule);
  - use third-country / global third-party billing (FedEx publishes VN G3P pricing; DHL
    and UPS support third-party billing with surcharges); or
  - have merchants be the exporter of record while we are DDP payer, which conflicts
    with us being the seller.

  **This needs a design decision and legal advice; do not assume.**

---

## 6. Best fits for "reach the rest of the world early"

1. **One integrator as the global default: DHL Express or FedEx.**
   - DHL Express has the widest DDP coverage, a published VN DTP fee and a mature API,
     but no Express webhooks (poll).
   - FedEx has HMAC-signed webhooks, published VN promo rates and the FICP economy tier.
   - Pick by negotiated VN rates for SEA plus US/EU on the S and M parcels. Get quotes
     from all three in the same week.
2. **UPS as the second integrator.** It publishes complete VN rate and zone tables (the
   easiest to model and reconcile) and has a published DDP fee. Its Expedited service is
   a cheaper day-definite tier for M/L/XL parcels.
3. **Asendia (RFQ) for economy DDP to EU/UK/US.** It also has the best cross-border
   returns product (e-PAQ Returns, local return addresses).
4. **YunExpress VN (RFQ)** as a possible cheap DDP line if it operates VN-origin lines.
5. Later: **Aramex** for GCC last mile; **VN Post EMS** only as a fallback.

## 7. Biggest unknowns

- DHL VN per-parcel export rates, and achievable discounts at 500/month for DHL, UPS and
  FedEx (needs sales quotes).
- Whether a SG or NL entity can hold VN export accounts directly, or must use
  third-party billing (needs carrier sales and legal input).
- FedEx VN bill-duties-to-sender fee amount; whether FICP serves every IP zone country;
  FedEx tariff sheet currency.
- Duty invoice formats (EDI/CSV) for reconciliation, and how each carrier takes our IOSS
  / UK VAT / AU GST IDs.
- International returns into VN (re-import duty relief, cost); FedEx Global Returns and
  UPS Returns availability from SEA → VN.
- VN-origin products and APIs from YunExpress, Cainiao, 4PX and Asendia.
- US tariff rate applying to VN-made apparel in Nov 2026 (single-source report of a 10%
  global tariff after IEEPA tariffs were struck down).
- Webhook signing for UPS Track Alert; DHL Express push availability.

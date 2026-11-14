# 01 — SEA carriers and cross-border specialists, VN origin → SG / MY / TH / ID / PH

Researched 06-Nov-2026. Slice: regional carriers, VN couriers (first mile), VN/SEA cross-border
e-commerce forwarders. Global integrators (DHL Express, FedEx, UPS) and global aggregators are
other researchers' slices and only appear here where a VN carrier resells them.

Source labels: **[published]** carrier's own page or doc, **[calculator]** public quote tool,
**[estimate]** my inference or a third-party report. **UNVERIFIED** = could not confirm.

> Bottom line up front: **No regional carrier in this slice publishes a self-serve, API-driven,
> DDP parcel product from Vietnam to all five SEA markets.** The two big regional names that could
> have done it end to end have left VN: **Ninja Van shut down its Vietnam express business on
> 30-Sep-2025**, and **DHL eCommerce left Vietnam in 2021**. What is left is (a) J&T Vietnam's
> "J&T International" counter/portal product (no public API, rates or DDP terms), (b) 4PL /
> cross-border specialists (Janio, Boxme) that subcontract the line haul and last mile and claim
> per-country duty handling, and (c) postal/express resellers (VNPost EMS, Viettel Post,
> Nhất Tín, 247Express...) that are DDU by default. Every DDP answer and every price in this
> file needs a sales quote to confirm.

---

## 1. Lane × provider matrix (VN origin, B2C parcel)

Legend: **Y** = confirmed served from VN origin [published]; **Y?** = claimed/likely but not
confirmed from VN origin; **N** = not served / exited; **–** = not applicable; **DDP?** =
DDP status. "Last mile only" = has its own network at the destination and could be the
injection partner of a forwarder, but does not pick up in VN.

| Provider | →SG | →MY | →TH | →ID | →PH | DDP from VN | Self-serve API for VN-origin int'l | Status note |
|---|---|---|---|---|---|---|---|---|
| **Ninja Van** (VN entity) | N | N | N | N | N | – | – | VN express shut 30-Sep-2025; only freight forwarding (air ≥45 kg) remains [published] |
| Ninja Van (destination networks) | last mile | last mile | last mile | last mile | last mile | – | domestic API per country | Strong last mile in SG/MY/PH/ID/TH; usable via a forwarder that injects |
| **J&T Express VN – J&T International** | Y | Y | Y | **N (not on list)** | Y | UNVERIFIED | No public docs; "Tạo đơn quốc tế" in web portal | Destination list of 232 countries omits Indonesia [published] |
| **Flash Express** | N | N (exited MY Jan 2026) | last mile | N | last mile | – | – | No VN-origin cross-border found |
| **Shopee SPX / SLS** | marketplace only | marketplace only | marketplace only | marketplace only | marketplace only | via Shopee (SIP/SLS) | No (captive) | Not usable for our own checkout |
| **Lazada Logistics (LEX)** | marketplace only | marketplace only | marketplace only | marketplace only | marketplace only | via Lazada | No (captive) | FLex (LEX) stopped VN last mile 31-Mar-2026 |
| **Janio** (SG 4PL) | Y? | Y? | Y? | Y? | Y? | "per-country customs and duty handling" — DDP term not used | Yes, REST API + Shopify/Woo plugin | VN listed as covered country; VN as *origin* not stated |
| **Boxme** (VN-HQ) | Y? | Y? | Y? | Y? | Y? | "DDP options may be available upon agreement" | Open API v2 (markets: PH, TH, VN) | Model leans to stocking inventory in destination warehouses |
| **DHL eCommerce Asia** | N | N | N | N | N | – | – | Exited VN 2021; PID not offered from VN origin |
| **Vietnam Post – EMS / Economy / VNQuickPost** | Y | Y | Y | Y | Y | No (UPU postal = DDU) | VNPost has an "API connect" page (domestic-oriented); int'l API UNVERIFIED | Cross-border e-com product is US-focused |
| **Viettel Post international** | Y | Y | Y | Y | Y | UNVERIFIED (resells express) | No public int'l API | "Express int'l" and "carrier-designated int'l" (resold DHL/UPS/etc.) |
| **GHN** | N | N | N | N | N | – | Good domestic API | Domestic only — first mile |
| **GHTK** | N | N | N | N | N | – | Good domestic API | Domestic only — first mile |
| **Kerry Express VN (KEVN)** | Y? | Y? | Y? | Y? | Y? | UNVERIFIED | UNVERIFIED | Claims "domestic + international"; KEX TH is a separate company |
| **BEST Express VN** | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | Domestic active in 2025; int'l not found |
| **YunExpress VN** | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | YunExpress offers DDP lines into US/EU; SEA UNVERIFIED | YunExpress group API exists | HCMC office, 500–999 staff, sells "air & sea solutions to e-commerce platform clients" |
| Nhất Tín, 247Express, Nasco | Y | Y | Y | Y | Y | No (express resellers, DDU) | No | Counter-based express agents |

**DDP reality:** none of the providers above publishes a VN-origin DDP B2C product with a price per
lane. DDP from VN to SEA today is a negotiated product (Boxme "upon agreement"; Janio "per-country
duty handling"; J&T and Viettel Post unknown). Postal (EMS) is DDU by design. Because we sell DDP and
pay duties/GST via the courier, we need a partner that **bills duties to the shipper account and
gives a duty invoice per shipment** — none of these confirms that in public docs.

---

## 2. Provider details (rubric)

### 2.1 Ninja Van — exited Vietnam; still relevant as destination last mile

- **Status:** Nin Sing Logistics (Ninja Van Vietnam) stopped all B2B and B2C express delivery on
  **30-Sep-2025**; orders accepted only to 08-Sep-2025; COD reconciliation and claims closed by
  31-Oct-2025. Stated reason: restructuring toward "a new logistics model in the future".
  [published, news] https://mekongasean.vn/ninja-van-dung-dich-vu-giao-nhan-nhanh-tai-viet-nam-45764.html,
  https://en.vietstock.vn/2025/09/ninja-van-to-exit-viet-nam-delivery-scene-amid-intense-competiton-974-620157.htm,
  https://doanhnhan.baophapluat.vn/ninja-van-chinh-thuc-rut-khoi-thi-truong-giao-nhan-nhanh-viet-nam-sau-gan-mot-thap-ky-86233.html
- The VN site still says Express Logistics was discontinued and offers only "overseas shipping",
  Ninja B2B and **freight forwarding** (air consolidation min **45 kg**, LCL min 1 cbm, customs
  clearance) [published] https://www.ninjavan.co/en-vn , https://www.ninjavan.co/en-vn/services/freight-forwarding
  → Not a B2C parcel option from VN. No sign of a 2026 return (searched VN press).
- **Cross-border elsewhere:** Ninja Van runs E2E cross-border from HK/CN/KR into SG/MY/PH/VN incl.
  customs clearance; third-party booking page says only DDP accepted for VN and PH destinations
  [estimate, reseller page] https://support.spaceshipapp.com/en/ninjavan ; Ninja Van MY added 44
  outbound lanes in 2025 [published, news] https://thesun.my/business-news/ninja-van-malaysia-launches-44-new-international-delivery-lanes-extends-cross-border-reach-LA14720803
- **Integration (for use as last mile in destinations):** OAuth2 client-credentials bearer token;
  sandbox `api-sandbox.ninjavan.co/SG` (all sandbox via SG); **compulsory audit** of Create Order +
  Waybill APIs (3 test orders each), verification 1–3 weeks; webhooks are push-only (no pull
  tracking API); custom waybills allowed if they carry a QR of the tracking number. Webhook signing:
  HMAC per-country secret per an unofficial Ruby gem — header name UNVERIFIED.
  [published] https://sites.google.com/ninjavan.co/ninjaapi/api-docs , [estimate] https://www.rubydoc.info/gems/ninja_van_api
- **Takeaway:** drop as a VN-origin carrier. Keep as a likely last-mile partner *behind* a
  forwarder/4PL (ask Janio/Boxme which last mile they use per country).

### 2.2 J&T Express Vietnam — J&T International

- **Coverage [published]:** official list of **232 destinations** includes Singapore, Malaysia,
  Thailand, Philippines — **Indonesia is not on the list** (19 territories listed as unsupported;
  Indonesia is in neither list). https://jtexpress.vn/en/danh-sach-tuyen-gui-quoc-te
  Possible list error, but treat VN→ID via J&T VN as **not available** until confirmed.
- **Product [published]:** door-to-door, targeted at "shop owners on e-commerce platforms selling
  fashion items, cosmetics..."; ordered via the customer portal ("Tạo đơn quốc tế")
  https://jtexpress.vn/en/international-service , https://khachhang.jtexpress.vn/login
- **API:** no public int'l API docs found. J&T VN's domestic open platform exists (not checked in
  depth). UNVERIFIED whether int'l orders can be created by API.
- **DDP / who clears / duties billed:** not stated anywhere public. UNVERIFIED.
- **Price:** the public price-list tool is domestic-only; no int'l rates published. UNVERIFIED.
- **Transit:** aggregator figures (not VN-origin specific): MY 3–6, PH 4–7, TH 4–9 days
  [estimate] https://www.ordertracker.com/carriers/jt-express
- **Why it matters:** J&T owns last-mile networks in MY, TH, PH, SG and ID, so a J&T-group DDP
  line VN→SEA is structurally plausible. Worth a sales call with J&T VN *and* J&T International
  (regional cross-border arm, "J&T One Packet") https://www.prnewswire.com/ae/news-releases/jt-international-launches-jt-one-packet-small-parcel-delivery-service-to-europe-and-north-america-301614740.html
- **Returns:** UNVERIFIED; J&T domestic networks in each destination could take local returns.

### 2.3 Flash Express (Thailand)

- Thai-HQ; networks in TH, PH, (Laos JV). **Closed its Malaysia service, announced 12-Jan-2026**
  [published, Thai news] https://www.thansettakij.com/technology/648775 . No VN-origin cross-border
  product found; VN presence (Flash Express Vietnam / Flash Fulfillment Vietnam) is thin and
  unconfirmed [estimate] https://sgpgrid.com/company/flash-express-vietnam-ID0000
- **Use:** TH / PH last mile only, via a forwarder. Not a VN-origin option.

### 2.4 Shopee SPX / Lazada LEX — not usable outside their marketplaces for cross-border

- Shopee cross-border runs through **SLS** (Shopee Logistics Service) and the SIP programme; the
  seller drops at a Shopee hub and Shopee forwards; chargeable weight = max(actual, volumetric).
  Only for Shopee orders. [estimate] https://ginee.com/my/insights/shopee-ship-from-oversea/ ,
  https://kr-asia.com/cross-border-opportunities-and-pitfalls-in-southeast-asia-with-shopeee
- SPX VN has a "Non-Shopee" programme for **domestic** social sellers; no evidence of non-Shopee
  cross-border. [estimate, job post] https://www.monster.com.vn/job/senior-customer-experience-spx-express-spx-express-ho-chi-minh-41560100 ,
  https://spx.vn/en/about-us.html
- Lazada Logistics (ex-LEX/LEL) sells multichannel fulfilment to brands from its own stock pool,
  no public third-party shipping API found [published, news] https://www.freemalaysiatoday.com/category/highlight/2021/08/25/lazada-rebrands-logistics-arm-to-reflect-its-one-stop-offerings .
  **FLex Speed (LEX) ended VN last-mile delivery by 31-Mar-2026** [published, news 05-Mar-2026]
  https://www.theasset.com/index.php/article/55988/alibaba-linked-logistics-firm-lex-to-stop-deliveries-in-vietnam
- **Verdict:** N for our own-checkout MoR. Relevant only if a merchant *also* sells on Shopee/Lazada
  (out of our scope).

### 2.5 Janio (Singapore 4PL) — top candidate to evaluate

- **Model [published]:** now a self-described **4PL** — "we don't own the trucks and warehouses,
  we orchestrate them", 500+ vetted carriers; covers SG, MY, ID, TH, VN, PH, HK, TW, CN (15+
  countries). https://www.janio.asia/faq
- **Coverage:** VN listed as a covered country; marketplace page lists cross-border fulfilment into
  the six SEA markets with "per-country customs and duty handling", cross-border COD,
  door-to-door, **"marketplace-compliant returns management"**. VN as **origin** is not stated —
  UNVERIFIED. https://www.janio.asia/lanes/marketplace-sellers
- **Integration [published]:** REST API (1,000 req/min), Shopify/WooCommerce plugins, CSV, portal;
  "most customers are shipping within 2 weeks"; enterprise ERP 4–6 weeks. Old API docs host
  (apidocs.janio.asia) is now broken (TLS mismatch / 404) — current API docs, sandbox and webhook
  signing UNVERIFIED. https://www.janio.asia/faq
- **Cost [published]:** Platform Access **from US$0.50/shipment** (platform fee, on top of
  freight), month-to-month, "ideal for 500+ orders/month", no minimum; Managed Services from
  US$2,500/month + usage (12-month); Enterprise "typically $10K+/month" (ideal 50K+/month); no setup
  fee for standard integrations. https://www.janio.asia/pricing
  → Our ~500/month start fits Platform Access; at 50k/month the published tiers push toward
  Enterprise.
- **Entity:** SG HQ — contracting with an SG entity is natural; NL BV UNVERIFIED but likely fine.
- **Per-parcel freight:** not published ("free landed-cost and transit benchmark" on request).
  UNVERIFIED.
- **Risk:** as a 4PL, quality depends on the subcontracted carrier per lane; claims are handled by
  Janio on the customer's behalf [published FAQ]; duty invoicing format UNVERIFIED.

### 2.6 Boxme (Vietnam-HQ cross-border fulfilment) — second candidate

- **Model:** VN-founded (Boxme Global) cross-border e-commerce fulfilment: international shipping,
  customs clearance, warehousing, last mile; historically aimed at helping VN sellers expand to MY
  and others; partner of 4PX; uses J&T in PH. [estimate, older Boxme posts]
  https://thesun.my/business/boxme-to-ramp-up-operations-in-malaysia-AE2006673 ,
  https://blog.boxme.asia/?p=7130 , https://boxme.asia/?p=4661
- **Footprint conflict:** current site: FCs in VN, TH, PH ("6 Fulfillment Centers"); older pages
  add MY, ID. Open API v2 lists **markets PH, TH, VN** (MY/SG/ID API availability UNVERIFIED).
  [published] https://boxme.asia , https://developers.boxme.asia
- **DDP [published]:** "Delivered Duty Paid (DDP) options may be available upon agreement."
  International transit **5–15 business days** depending on country and carrier. Loss: compensation
  per carrier policy; damage in transit = carrier liability; tickets answered in 24 business hours.
  https://boxme.asia/shipping-policies/
- **Integration:** Open API v2 (release plan 01/2024, RESTful, keeps v1). Auth, sandbox, webhooks,
  signing: UNVERIFIED (portal content not readable without login). https://developers.boxme.asia
- **Cost:** not published ("flat-rate pricing" mentioned, no figures). UNVERIFIED.
- **Why interesting:** VN-native (first mile, Vietnamese-speaking ops, VN export paperwork) **and**
  has destination warehouses in TH/PH — the only provider in this slice with a ready place for
  buyer returns to land locally. Also allows a "forward-stock" model later (bulk B2B export, B2C
  domestic delivery in destination) which changes the tax/DDP picture.

### 2.7 DHL eCommerce Asia — not available from VN

- DHL eCommerce **stopped nationwide domestic delivery in Vietnam (announced 22-Oct-2021)**; other DHL
  divisions (Express, Global Forwarding, Supply Chain) continue. [published, news]
  https://vir.com.vn/dhl-ecommerce-stops-operation-in-vietnam-88608.html
- Parcel International Direct (PID) served CN/AU-origin sellers *into* MY/TH/VN, not VN origin.
  [published, news] https://vietnamnews.vn/media-outreach/465915/dhl-ecommerce-launches-cash-on-delivery-for-cross-border-e-commerce.html
- From VN, DHL means **DHL Express** (other researcher's slice; also resold by Vietnam Post as agent
  [published] https://vietnampost.vn/vi/doanh-nghiep/chuyen-phat/chuyen-phat-quoc-te ).

### 2.8 Vietnam Post (VNPost) / EMS Vietnam

- **Products [published]:** EMS (UPU/EMS Cooperative), VNQuickPost (VNPost-branded int'l express),
  Economy, resold UPS / DHL / SF, and a "Chuyển phát TMĐT xuyên biên giới" product aimed at Amazon /
  Etsy / TikTok Shop / eBay sellers **shipping to the US**.
  https://vietnampost.vn/vi/doanh-nghiep/chuyen-phat/chuyen-phat-quoc-te
- **SEA lanes:** EMS reaches all five. **DDP: no** — postal goods are cleared by the destination
  post, buyer pays duties/GST (DDU). Some destination posts support pre-paid tax for registered
  sellers (e.g., SG GST OVR / MY LVG registration), but not as a VNPost product — UNVERIFIED.
- **API:** VNPost has a "Kết nối API" partner page (domestic orientation); int'l EMS order API
  UNVERIFIED. Rates: official EMS int'l table on ems.com.vn is JS-rendered and was not retrievable;
  third-party indicative int'l average **US$15–25/kg** (not SEA-specific) [estimate]
  https://ghn.vn/blogs/thong-tin-giao-hang/tong-hop-don-vi-van-chuyen-quoc-te
- **Use:** fallback for low-value, DDU-tolerant lanes; does not fit our DDP promise.

### 2.9 Viettel Post — international

- Offers "Chuyển phát Quốc tế nhanh" and "Chuyển phát Quốc tế chỉ định hãng" (carrier-designated:
  resold global express) [estimate] https://www.misaeshop.vn/?p=12498 ; strategy is "Go Global"
  toward Laos/Cambodia/Myanmar + China–ASEAN road logistics and a B2B wholesale platform (VIPO Mall),
  not SEA B2C DDP parcels [published, news]
  https://mekongasean.vn/dhdcd-viettel-post-xay-mang-luoi-logistics-ket-noi-asean-trung-quoc-40755.html ,
  https://theinvestor.vn/vietnam-delivery-major-viettel-post-to-launch-two-way-cross-border-e-commerce-platform-d13623.html
- Indicative int'l **US$18–30/kg** (not lane-specific) [estimate] (GHN blog above).
- Public int'l API: none found. Domestic partner API exists (UNVERIFIED details).
- **Use:** VN first mile (strong domestic API/network); not a cross-border DDP partner.

### 2.10 GHN / GHTK — domestic first mile

- **GHN:** no international product of its own (GHN's own blog sends readers to other carriers for
  int'l). [published] https://ghn.vn/blogs/thong-tin-giao-hang/tong-hop-don-vi-van-chuyen-quoc-te
  API: Token + ShopId headers; production `online-gateway.ghn.vn`, staging
  `dev-online-gateway.ghn.vn` (staging and prod are separate accounts); webhook callback types
  `create`, `switch_status`, `update_weight`, per-order ordered delivery, fixed-backoff retries.
  Webhook signing: UNVERIFIED (doc page returned 403). [published]
  https://developer.ghn.vn/en/docs/getting-started/overview
- **GHTK:** domestic only; webhook to partner callback URL, only HTTP 200 counts as success,
  retries (docs disagree: "retry once" vs "resend"). Signing UNVERIFIED. [published]
  https://api.ghtk.vn/en/docs/submit-order/webhook , https://pro-docs.ghtk.vn/5_webhook/
- **Use:** pickup from merchants (often in HCMC/Hanoi provinces) to the cross-border partner's export
  hub, if that partner does not do its own pickup. Integration effort small (~3–5 eng-days each
  [estimate]). Domestic rates from ~15.5k VND/order (GHN, own blog) [published].

### 2.11 Kerry Express Vietnam (KEVN) / KEX

- Kerry Express (Vietnam) Co. Ltd, since 2001, HCMC HQ, ~120 post offices, 4 hubs; claims
  domestic + international express [estimate] https://yellowpages.vn/lgs/1187721495/cong-ty-tnhh-kerry-express-viet-nam.html ,
  https://jobsgo.vn/tuyen-dung/cong-ty-tnhh-kerry-express-viet-nam-76925035.html
- KEX Express Thailand (renamed 2024) is a separate listed company; Kerry Logistics is majority
  owned by SF Holding since 2021 [estimate] https://carriers.aftership.com/kerry/divisions
- VN→SEA DDP product, API, prices: all UNVERIFIED. Low priority; worth one email because SF/Kerry
  Logistics has a real SEA cross-border network.

### 2.12 BEST Express Vietnam

- Active domestic operator in 2025 (on-time delivery ranking) [published, news]
  https://en.vietstock.vn/2026/01/most-postal-services-operators-record-strong-on-time-delivery-performance-974-628387.htm
- International from VN: none found. UNVERIFIED. Low priority.

### 2.13 Other VN cross-border e-commerce forwarders found

- **YunExpress Logistics Vietnam** (HCMC Thủ Đức + Hanoi; 500–999 staff) sells "air & sea logistics
  solutions to e-commerce platform clients" — group is known for DDP e-commerce lines into US/EU;
  VN→SEA lines UNVERIFIED. [estimate, job listings]
  https://careerviet.vn/en/nha-tuyen-dung/yunexpress-logistics-vietnam.35A98979.html , https://yunexpress.com
- **Nhất Tín Express** (cross-border e-com, COD, insurance, "limited int'l footprint"; ~US$18–28/kg),
  **247Express** (~US$19–32/kg), **Nasco Express** (Hanoi; ~US$22–35/kg) — counter-based express
  resellers, DDU, no public API [estimate] (GHN blog above).
- Not found: a VN-origin **TikTok Shop** cross-border logistics service for VN sellers into SEA
  (TikTok's cross-border build-out is China-origin, e.g. Pingxiang border warehouse for CN→VN/TH)
  [estimate] https://www.photonpay.com/hk/blog/article/tiktok-shop-available-countries

---

## 3. Cost — what can and cannot be priced

**No provider in this slice publishes VN-origin SEA B2C rates.** Every number below is an
[estimate] and must be replaced by quotes.

Chargeable weight for the reference parcels (max of actual and volumetric):

| Parcel | Actual | Vol ÷5000 | Vol ÷6000 | Chargeable ÷5000 | Chargeable ÷6000 |
|---|---|---|---|---|---|
| XS 20×15×3 | 0.25 | 0.18 | 0.15 | 0.25 | 0.25 |
| S 25×20×5 | 0.5 | 0.50 | 0.42 | 0.5 | 0.5 |
| M 30×20×10 | 1.0 | 1.20 | 1.00 | **1.2** | 1.0 |
| L 35×25×15 | 2.0 | 2.63 | 2.19 | **2.63 → 3.0 kg** (0.5 kg steps: 3.0) | 2.19 → 2.5 |
| XL 40×30×20 | 5.0 | 4.80 | 4.00 | 5.0 | 5.0 |

Divisor matters most for M and L (apparel in bulky boxes). Ask every partner for ÷6000 or
actual-weight billing for ≤2 kg.

Only VN-side indicative table found (GHN blog, "air" to Philippines, VND, **probably copied from an
India article — low confidence**): 1 kg 190,000; 3 kg 400,000; 5 kg 600,000 [estimate]
https://ghn.vn/blogs/thong-tin-giao-hang/gui-hang-di-philippines . At ~26,000 VND/USD that is
≈ US$7.3 (1 kg), US$15.4 (3 kg), US$23 (5 kg) — **duties/VAT not included, DDU**.

Rough planning ranges for an e-commerce DDP-capable consolidated line VN→SEA, before duties/GST,
**[estimate, my inference from regional e-commerce line pricing; UNVERIFIED]**:

| Parcel | SG | MY (West) | TH | PH (Metro Manila) | ID (Jabodetabek) |
|---|---|---|---|---|---|
| XS / S (≤0.5 kg) | US$4–7 | US$4–7 | US$4–7 | US$5–9 | US$5–9 |
| M (1–1.2 kg) | US$6–10 | US$6–10 | US$6–10 | US$8–12 | US$8–13 |
| L (2.2–3 kg) | US$10–16 | US$10–16 | US$10–16 | US$13–20 | US$14–22 |
| XL (5 kg) | US$18–28 | US$18–28 | US$18–28 | US$22–35 | US$24–38 |

Add: Janio platform fee from US$0.50/shipment [published]; DDP handling fee per parcel (typically
a few % of duty or a flat fee — UNVERIFIED); remote-area surcharges (East MY, PH provinces, ID outer
islands). Express resellers (Viettel Post, EMS, Nhất Tín) quote US$15–30/kg [estimate], i.e.
2–3× the consolidated-line range for small parcels, and DDU.

**Integration cost [estimate]:** per courier module 8–15 eng-days for a partner with a REST API +
webhooks + label PDF (rate quote, create + label, tracking webhook, duty invoice import, returns);
+5–10 days for a partner without real API docs (CSV/portal + manual reconciliation). GHN/GHTK
first-mile module 3–5 days each.

---

## 4. Speed (transit days)

| Lane | Published / reported | Source type |
|---|---|---|
| Any SEA via Boxme int'l | 5–15 business days | [published] boxme.asia/shipping-policies |
| VN→PH generic | air express 2–4 d, sea economy 7–14 d | [estimate] GHN blog |
| J&T to MY / PH / TH (origin not VN-specific) | 3–6 / 4–7 / 4–9 d | [estimate] ordertracker |
| Ninja Van HK→SG / PH Manila / rest of PH (for comparison of a working E2E line) | 3–4 / 5–7 / 7–11 working d | [estimate] spaceshipapp |
| Expected for a consolidated air DDP line from HCMC/HAN [my estimate] | SG 3–5, MY-West 4–6, TH 4–7, PH-Manila 5–8, ID-Jakarta 5–9 (outer islands +3–7) | [estimate] |

No VN-origin SLA was published by any provider. UNVERIFIED across the board.

---

## 5. Operations

**Tracking:** Destination last-mile carriers (Ninja Van, J&T, Flash) give scan-level events with
push webhooks; 4PLs (Janio, Boxme) normalise multi-leg tracking ("multi-country tracking" —
Janio [published]). Granularity of the international leg (export cleared / departed / arrived /
import cleared) per provider: UNVERIFIED. Postal EMS: UPU event codes, coarse.

**Claims:** Boxme: loss compensated per carrier policy; damage in warehouse = Boxme, in transit =
carrier [published]. Janio files carrier claims for customers [published]. Others UNVERIFIED.

**Restricted goods:** Apparel/accessories are low-risk. Watch: leather (CITES species), items with
batteries (smartwatch accessories), perfume/aerosols, counterfeit-brand risk (ID and PH customs seize
branded goods). J&T VN has a "Refused goods" list (domestic) [published] https://jtexpress.vn/en/tracking?type=prohibited-goods ;
Boxme requires prior approval for hazardous/special goods [published].

**Destination tax context relevant to DDP (other researchers have the authoritative tax slice)
[estimate]:** ID de minimis for duty ≈ US$3, so almost every parcel pays duty + VAT
(https://blog.easyparcel.com/my/ship-from-malaysia-to-indonesia/); PH de minimis PHP 10,000
(https://www.transglobalexpress.co.uk/information/countries/philippines/); SG and MY collect GST /
LVG sales tax from registered overseas vendors rather than at the border for low-value goods;
TH charges VAT on all imports since 2024. This means for SG/MY the "DDP" problem is mostly our own
tax registration, while for ID/PH/TH the courier-paid duty/VAT path matters most.

### 5.1 Returns from SEA buyers back to VN

No provider in this slice offers a productised **B2C cross-border return to Vietnam**. Practical
options, ranked:

1. **Local return hub in each destination, then batch** (recommended model). Buyer drops/collects
   via the destination's domestic carrier (J&T / Ninja Van / Flash / SPX drop-off networks) to a
   local address; items are graded; sellable items are re-sold locally (if forward-stocking) or
   consolidated monthly and re-exported to VN as a B2B shipment; unsellable items are destroyed
   locally with a certificate. Providers with destination warehouses: **Boxme (TH, PH; MY/ID
   UNVERIFIED)** and **Janio** ("returns management", locations UNVERIFIED). [estimate]
2. **Individual return label back to VN** via express (DHL/FedEx/UPS, Viettel Post designated
   carrier). Expensive (likely US$20–40+ per parcel [estimate]); VN re-import declaration needed;
   only for high-value items.
3. **Return-less refund** (refund and let buyer keep/donate) below a value threshold — often the
   cheapest for low-value apparel. Business rule, not a logistics product.

Re-importing returned goods into VN: VN customs treats goods re-imported after export separately
from new imports (possible exemption from import duty for returned exports) — exact procedure and
paperwork for B2C returns UNVERIFIED; needs a VN customs broker. Our ledger must book: refund to
buyer, refundability of import duty/VAT already paid in the destination (rarely recoverable for
low-value parcels — UNVERIFIED per country), return freight, and write-off.

---

## 6. Top candidates in this slice

1. **Janio** — only provider with published self-serve pricing (US$0.50/shipment platform fee,
   no minimum, "ideal for 500+ orders/month"), REST API + plugins, 2-week onboarding, SG entity,
   all five destinations, per-country duty handling and returns management. Must confirm VN as an
   origin, DDP with duties billed to shipper, and API docs/webhooks.
2. **Boxme** — VN-native first mile and export, destination warehouses (TH/PH at least) for
   returns and future forward-stocking, DDP "upon agreement", Open API v2. Must confirm MY/SG/ID
   coverage, DDP terms, API depth (rate quote, labels, webhooks), prices.
3. **J&T Express (VN + J&T International)** — owns last mile in all five destinations; VN
   portal already ships to SG/MY/TH/PH. Must confirm DDP, API access for int'l orders, Indonesia,
   duty invoicing, rates. Highest scale potential at 50k/month if a J&T-group DDP line exists.

Drop for VN origin: Ninja Van (exited VN), DHL eCommerce (exited VN), Flash (no VN origin; left MY),
SPX/LEX (captive), GHN/GHTK (domestic only — keep as first mile), VNPost EMS/Viettel Post/express
resellers (DDU, no API — fallback only).

## 7. Biggest unknowns (questions for sales calls)

1. Does anyone offer **true DDP from VN origin** with duties/VAT **billed to our account** and a
   **per-shipment duty invoice** we can import for reconciliation? (All three candidates: unknown.)
2. **Who is importer of record** at destination — the 4PL's local entity, the courier, or us
   (SG/NL entity)? Affects whether we need local tax registrations (SG GST OVR, MY LVG) anyway.
3. J&T VN: is Indonesia really unavailable? Is there an int'l order API?
4. Janio: is VN an *origin* lane today, and which carriers do the line haul and last mile?
5. Boxme: current destination warehouses and API coverage for MY/SG/ID.
6. Real per-parcel rates for the 5 reference parcels, volumetric divisor, remote-area surcharges.
7. Webhook signing and sandbox availability for all three (unconfirmed for every candidate).
8. VN export side: are B2C low-value exports cleared under a simplified e-commerce declaration by
   the partner (and does the partner give us export proof for VN VAT 0% treatment, if relevant)?
9. Returns: which partner can give a local return address in each of the five countries?

# VIO ePacket (YunExpress network, VN origin)

Provider id `yunexpress_vn`. VIO Shipping, Da Nang (offices in Hanoi
and HCMC), sells ePacket lines on the YunExpress network: YunExpress
freighters, customs at its Hong Kong hub, last mile by the destination
post (US: USPS). Contact: admin@vioshipping.com, WhatsApp/Zalo +84 943
978 891. Questions q08 (answered), q46 (follow-up).

| File | What | Status |
|---|---|---|
| `pages/*.html` | vioepacket.com/en route pages, `epacket-extra` and `routes`, downloaded 07-Nov-2026; rates effective 01-Nov-2026, headed "Business rates" | loaded: main routes (standard line) as `yunexpress_vn_2026_list`, the clothing tables as `yunexpress_vn_clothing_2026_list`, US-Saver as `yunexpress_vn_us_saver_2026_list`, Extra groups as `yunexpress_vn_extra_2026_list`, 0.1 to 5 kg; transit, surcharges and US-Saver product limits |
| `2026-11-26_epacket_standard_clothing.pdf` | VIO's price list ePacket Standard (p. 1), Clothing (p. 2) and terms (p. 3), VND excl. VAT, effective 01-Nov-2026; attached to VIO's answer to q08 | received 26-Nov-2026; Standard 0.1 to 5 kg loaded as `yunexpress_vn_2026_retail`, Clothing as `yunexpress_vn_clothing_2026_retail`; BR, CL, AE, SA transit and the HK, HR and RO fees loaded |
| `2026-11-26_epacket_extra.pdf` | VIO's price list ePacket Extra (pp. 1-2), country list with price column and maximum weight (pp. 3-4) and terms (p. 5), VND excl. VAT, effective 01-Nov-2026 | received 26-Nov-2026; loaded as `yunexpress_vn_extra_2026_retail` with the website card's country mapping |

### Two tiers

VIO's email names a retail table and a Business table (Business from 70
orders a month). Both PDFs are 6-21% above the website's prices at every
loaded weight and zone, and the website heads its tables "Business
rates", so the PDFs are read as retail and the website cards carry
`volume_per_month` 70. q46 asks VIO to confirm. Both tiers are loaded at
every published break (0.1 kg steps to 2 kg, then 0.5 kg steps to 5 kg),
so light parcels such as the 0.25 kg XS scenario bill at 0.3 kg.

### What the PDFs add to the website

- Standard adds Brazil, Chile, UAE and Saudi Arabia; one US column, last
  mile all USPS (no US-Saver, no Alaska, Hawaii, PR, APO/FPO or Amazon
  warehouses, no proof of delivery).
- Clothing (also on the website): same prices as Standard to US, GB,
  DE/FR/ES, IT, AU, CA, but charged on actual weight; clothing and ordinary
  textiles only, poly bags only, one parcel per waybill. Loaded as its own
  service `yunexpress_vn_clothing` with no volumetric divisor, the main
  routes' transit and fees (EU handling, UK remote area, IOSS, pickup),
  and every other category prohibited.
- Declared-value limits per country (US US$250, GB under GBP 135, EU
  under EUR 150, CA US$99 delivered DDP with 18% of the declared value
  above CAD 20, MX 19% tax on the declared value, SA 15% VAT). The CA and
  MX charges are not loaded: whether they are duties paid for us or fees
  is asked in q46.
- Compensation: Standard max US$30 a parcel, Extra VND 1,150,000 or the
  declared value if lower; never for damage or delay. The email adds that
  a lost parcel's freight is refunded in full.
- No returns to Vietnam; failed deliveries are re-delivered for a fee
  within 14 days (country exceptions), then destroyed.

Not loaded: Extra destinations without an ISO code
(Kosovo, Canary Islands, Azores, ...; the PDF also lists Uruguay twice,
as `UT` in group 1 and `UY` in the rest of the world, the website card's
`UY` in group 1 is kept). Main-route rates include fuel; neither the
website nor the PDF says so for Extra. Duties are paid by the recipient
except EU VAT through an IOSS number, and the DDP lanes the email names
(US, UK, CA) are not yet in `services.csv` (`ddp=ioss_only`) until q46
says how duties are billed.

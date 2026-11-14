# J&T Express (VN origin, international)

Provider id `jt_intl`. VN entity: Công ty TNHH MTV Chuyển phát nhanh
Thuận Phong. Contact: contact@jet-logistics.com (from
https://www.jtexpress.com/en/contactUs), question q06.

| File | What | Status |
|---|---|---|
| `664196dba695b677401074.pdf` | international express rate card JTF/JTG/JTL2024, export from VN, effective 2024-05-10, VND (16 pp.: Vietnamese pp. 1-8, English pp. 9-16); surcharges p. 7, zones p. 8 | saved 2026-11-07; non-document rates 0.5 to 30 kg, zones and surcharges loaded as `jt_intl_vn_2024_list` |
| `69cb6fda7f557356752523.pdf` | international rates VN to Malaysia and China, effective 2026-04-01 (table is an image) | saved 2026-11-07; read by eye: identical to the 2024 card for MY and CN, so the 2024 card is taken as current |
| `69843f381c871660781152.pdf` | domestic VN standard express price list, from 2026-02-05 | saved 2026-11-07; not loaded (domestic) |
| `2026-11-14_quote_shipping_rate.xlsx` | J&T Express Vietnam's reply to q06: price sheet in VND, no date, validity or service name; sheet 2 lists remote US ZIP codes (stored as numbers, leading zeros lost) | saved 14-Nov-2026; 0.5 to 20 kg loaded as `jt_intl_vn_2026_quote` (service `jt_intl_quote`) and the Singapore 2-3 days column as `jt_intl_vn_2026_quote_sg_fast` |

Not in the 2024 files: fuel surcharge (updated monthly), transit times,
DDP. The 2024 card excludes import duties and taxes, so the service is
duties unpaid unless J&T offers DDP (asked in q06).

### The 2026 quote (14-Nov-2026)

- Prices include VAT and fuel; remote-area fees extra. Duties unpaid: the
  recipient pays import duties and taxes. Volumetric weight L×W×H/5000.
- Only these lanes: three EU country groups plus Cyprus, Greece and Malta
  (no Ireland), GB, US (California separate), SG at two speeds (3-4 and
  2-3 days), TH, MY/PH/ID at one price, JP, KR, AU zones 1-4 by postcode.
- Not loaded: US California (a little above the rest of the US), AU zones
  2-4 (AU maps to zone 1, the low end), and above 20 kg, where a per-kg
  rate applies (by case for TH, MY/PH/ID and JP).
- Against the 2024 card at 0.5 kg the quote is about the 2024 freight plus
  45-50% (US, TH, MY), so fuel and VAT on the old card were roughly that
  much. SG is far cheaper (VND 258,000 against 428,800 before fuel); the
  EU groups far dearer (DE VND 1,117,000 against 648,600 before fuel).
- Oddities asked in q45: GB is named in two columns at different prices
  (the column headed UK is loaded); JP and US are cheaper at 5 kg than at
  4.5 kg; JP is flat from 2 to 4.5 kg and KR from 0.5 to 3 kg.
- Not answered: validity, volume discount, DDP, API and webhooks, pickup,
  returns, transit except SG (q45).

Goods rules (read in Chrome 2026-11-10; the site works in a browser, the
earlier certificate error was only for automated reads), loaded into
`service_restrictions.csv`:

- refused-goods list: https://jtexpress.vn/vi/tracking?type=prohibited-goods
  (16 groups; batteries are refused under item 2)
- general terms: https://jtexpress.vn/vi/dieu-khoan-chung (§3.1b
  international packing, §4 refusal, §7 exemptions)
- compensation policy: https://jtexpress.vn/vi/chinh-sach (§III.B
  international: US$100 undeclared, VND 30,000,000 declared)

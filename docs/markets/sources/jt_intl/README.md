# J&T Express (VN origin, international)

Provider id `jt_intl`. VN entity: Công ty TNHH MTV Chuyển phát nhanh
Thuận Phong. Contact: contact@jet-logistics.com (from
https://www.jtexpress.com/en/contactUs), question q06.

| File | What | Status |
|---|---|---|
| `664196dba695b677401074.pdf` | international express rate card JTF/JTG/JTL2024, export from VN, effective 2024-05-10, VND (16 pp.: Vietnamese pp. 1-8, English pp. 9-16); surcharges p. 7, zones p. 8 | saved 2026-11-07; non-document rates 0.5 to 30 kg, zones and surcharges loaded as `jt_intl_vn_2024_list` |
| `69cb6fda7f557356752523.pdf` | international rates VN to Malaysia and China, effective 2026-04-01 (table is an image) | saved 2026-11-07; read by eye: identical to the 2024 card for MY and CN, so the 2024 card is taken as current |
| `69843f381c871660781152.pdf` | domestic VN standard express price list, from 2026-02-05 | saved 2026-11-07; not loaded (domestic) |

Not in the files: fuel surcharge (updated monthly), transit times, DDP.
The 2024 card excludes import duties and taxes, so the service is duties
unpaid unless J&T offers DDP (asked in q06).

Goods rules (read in Chrome 2026-11-10; the site works in a browser, the
earlier certificate error was only for automated reads), loaded into
`service_restrictions.csv`:

- refused-goods list: https://jtexpress.vn/vi/tracking?type=prohibited-goods
  (16 groups; batteries are refused under item 2)
- general terms: https://jtexpress.vn/vi/dieu-khoan-chung (§3.1b
  international packing, §4 refusal, §7 exemptions)
- compensation policy: https://jtexpress.vn/vi/chinh-sach (§III.B
  international: US$100 undeclared, VND 30,000,000 declared)

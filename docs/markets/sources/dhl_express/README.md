# DHL Express (VN origin)

| File | What | Status |
|---|---|---|
| `service_and_rate_guide_vn_en_2026.pdf` | DHL Express Service & Rate Guide 2026: Vietnam (29 pp.): zones p. 23-24, export rates p. 25-26 (VND), surcharges p. 21, customs services p. 22, optional services p. 9-10 | saved 07-Nov-2026; Express Worldwide zones and non-document rates (0.5 to 30 kg) loaded as `dhl_ew_vn_2026_list`; surcharges checked against it |
| fuel (web page, weekly) | DHL VN surcharges page | in `surcharges.csv`; update before a comparison |
| demand surcharge (web page) | https://mydhl.express.dhl/vn/en/ship/surcharges/demand-surcharge.html (updated 31-Aug-2026): 01-Oct-2026 to 05-Feb-2027, VND per kg, origin row Rest of Asia | read 07-Nov-2026; loaded as `demand_surcharge_*` rows; fuel applies to it. The same page's fuel list (Residential Address, Remote Area, Elevated Risk, Oversize, Demand, ...) set `fuel_applies` |
| goods rules (web pages) | what-can-i-ship/prohibited-items.html, lithium-batteries.html, legal/terms-and-conditions.html (with the VN annex) | read 07-Nov-2026 into `service_restrictions.csv` |

Not in the guide: demand surcharge amounts (on the web page above),
remote-area postcodes, per-lane transit times
(dct.dhl.com), account discounts.
China is listed as zone "2/5" without saying which part is zone 2; zone
5 is used and the zone 2 prices are not loaded.

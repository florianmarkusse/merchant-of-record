# FedEx (VN origin)

Rate files need a FedEx account registered for Tariff Pricing
(https://www.fedex.com/en-vn/shipping/rates/customized-rates/export.html).

| File | What | Status |
|---|---|---|
| `fedex-rates-cm-en-vn-2026.pdf` | 2026 export tariff in USD: IPE, IP, IPF, IE, IEF (from 2026-01-05) | saved 2026-11-07; IP (p. 2) and IE (p. 4) loaded as `fedex_ip_vn_2026_list` and `fedex_ie_vn_2026_list`, 0.5 to 20 kg |
| `fedex-rates-zi-en-vn-2026.pdf` | zone index per service (IPE, IP, IE); footnote 1 = service not yet available | saved 2026-11-07; IP and IE zones loaded; countries under footnote 1 left out |
| `fedex-rates-sur-en-vn-2026.pdf` | surcharges and other information (IPE, IP, IE), version VN_20260629 | saved 2026-11-07; residential, inbound processing, address correction, ODA tiers, additional handling, third-party billing and volumetric divisor loaded |
| fuel (web page, weekly) | https://www.fedex.com/en-vn/shipping/surcharges.html | 52.25% for 2026-11-09 to 2026-11-15, copied 2026-11-07; update weekly or before a comparison |
| `fedex-rates-exp-en-vn-2026.pdf` | 2026 promo sheet (IP, VND) | wanted; today's `fedex_ip_vn_2026_promo` is from search extracts |
| `fedex-rates-ficp-exp-en-vn-2026.pdf` | 2026 FICP export tariff | wanted |
| `fedex-rates-ficp-cm-en-vn-2026.pdf` | FICP zone index | wanted |
| `fedex-rates-g3p-en-vn-2026.pdf`, `-g3pip-`, `-g3pipe-`, `-g3pie-`, `-g3pif-`, `-g3pbox-` | Global Third-Party regions, zones and IP/IPE/IE/IEF/box rates, VND, from 2026-01-05 | saved 2026-11-07; **not VN exports**: they price shipments between two non-VN countries paid by a VN account. Not loaded; relevant once we add other SEA origins |
| `fedex-apac-service-guide-en-vn.pdf` | APAC service guide | wanted |
| `fedex-ds-2026-nov2-638-en-vn.pdf` | demand surcharge notice from 2026-11-02 (export from VN per kg by region, Priority vs Economy, min VND 6,400) | read in Chrome 2026-11-07 (automated fetch is blocked), not saved; loaded as `demand_surcharge_*` rows for IP, FICP and IE. Later notices replace it |

The list cards are `verified`. The promo and FICP cards are still
`unverified` (read from search extracts); question q29. Fuel is a weekly percentage from the fuel page, not in any PDF.

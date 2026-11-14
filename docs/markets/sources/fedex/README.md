# FedEx (VN origin)

Rate files need a FedEx account registered for Tariff Pricing
(https://www.fedex.com/en-vn/shipping/rates/customized-rates/export.html).

| File | What | Status |
|---|---|---|
| `fedex-rates-cm-en-vn-2026.pdf` | 2026 export tariff in USD: IPE, IP, IPF, IE, IEF (from 05-Jan-2026) | saved 07-Nov-2026; IP (p. 2) and IE (p. 4) loaded as `fedex_ip_vn_2026_list` and `fedex_ie_vn_2026_list`, 0.5 to 20 kg |
| `fedex-rates-zi-en-vn-2026.pdf` | zone index per service (IPE, IP, IE); footnote 1 = service not yet available | saved 07-Nov-2026; IP and IE zones loaded; countries under footnote 1 left out |
| `fedex-rates-sur-en-vn-2026.pdf` | surcharges and other information (IPE, IP, IE), version VN_20260629 | saved 07-Nov-2026; residential, inbound processing, address correction, ODA tiers, additional handling, third-party billing and volumetric divisor loaded |
| fuel (web page, weekly) | https://www.fedex.com/en-vn/shipping/surcharges.html | 52.25% for 09-Nov-2026 to 15-Nov-2026, copied 07-Nov-2026; update weekly or before a comparison |
| `fedex-rates-exp-en-vn-2026.pdf` | 2026 promo sheets in VND for VN account holders: IPE, IP, IPF, IE, IEF | saved by hand 10-Nov-2026; IP (p. 2) and IE (p. 4) loaded as `fedex_ip_vn_2026_promo` and `fedex_ie_vn_2026_promo`, 0.5 to 20 kg |
| `fedex-rates-ficp-exp-en-vn-2026.pdf` | 2026 FICP promo sheet in VND (no Envelope or Pak rates) | saved by hand 10-Nov-2026; loaded as `fedex_ficp_vn_2026_promo`, 0.5 to 20 kg; zones assumed equal to IP |
| `fedex-rates-box-en-vn-2026.pdf` | 10 kg and 25 kg Box flat rates (IPE, IP, ImportOne) | saved by hand 10-Nov-2026; not loaded (we do not plan to use FedEx boxes) |
| `fedex-rates-ficp-cm-en-vn-2026.pdf` | FICP zone index | wanted |
| `fedex-rates-g3p-en-vn-2026.pdf`, `-g3pip-`, `-g3pipe-`, `-g3pie-`, `-g3pif-`, `-g3pbox-` | Global Third-Party regions, zones and IP/IPE/IE/IEF/box rates, VND, from 05-Jan-2026 | saved 07-Nov-2026; **not VN exports**: they price shipments between two non-VN countries paid by a VN account. Not loaded; relevant once we add other SEA origins |
| `fedex-apac-service-guide-en-vn.pdf` | APAC service guide | wanted |
| `fedex-ds-2026-nov2-638-en-vn.pdf` | demand surcharge notice from 02-Nov-2026 (export from VN per kg by region, Priority vs Economy, min VND 6,400) | read in Chrome 07-Nov-2026 (automated fetch is blocked), not saved; loaded as `demand_surcharge_*` rows for IP, FICP and IE. Later notices replace it |

All FedEx cards are `verified` (q29 answered 10-Nov-2026). The promo
cards are the rates a VN account holder in good standing pays before any
negotiated discount; the list cards are the higher export tariff. Fuel is a weekly percentage from the fuel page, not in any PDF.

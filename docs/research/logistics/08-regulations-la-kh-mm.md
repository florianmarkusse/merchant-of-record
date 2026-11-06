# 08 — Laos, Cambodia, Myanmar as buyer markets (VN origin)

Researched 2026-11-06, a short pass (5 searches, no official customs page
read directly). Same labels as the other slices: [published] authority or
carrier page, [3rd-party] guide or calculator, UNVERIFIED = could not
confirm. **Every threshold below conflicts between sources and needs a
local broker or the courier to confirm.**

## TL;DR

1. All three are ASEAN, so ATIGA 0% duty applies to goods of real VN
   origin (simplified declaration up to FOB US$200, as in `05`). The
   import VAT or commercial tax applies anyway.
2. None of them has a seller-collection regime for foreign sellers of
   goods. Tax is paid at the border, so it is `BorderPaid` through the
   integrator's duty-paid service. We don't register.
3. De minimis is unclear in all three: Laos possibly none, Cambodia
   possibly US$50, Myanmar possibly US$50. With the price ranges of the
   candidate merchants (US$80–2,000), assume tax is due on every parcel.
4. Myanmar adds sanctions screening and card-acceptance risk. Treat it as
   integrator-only, after a sanctions review.
5. These are small markets for premium goods. They belong in the
   integrator tier (express, duty-paid), not in a tier we build out
   per country.

## Laos (LA)

- **VAT:** 10% again since March 2024, after two years at 7% [published,
  Tilleke & Gibbins; PwC tax summary]. Charged on import.
- **Duty:** 0–40% by HS code. ASEAN origin lowers it [PwC].
- **Value basis:** CIF, so shipping counts [3rd-party, Easyship].
- **De minimis:** sources conflict. Easyship lists a US$0 threshold.
  VATitStream gives LAK 1,500,000 (about US$75) and on the same page says
  "no de minimis" UNVERIFIED.
- **Couriers:** DHL, FedEx and UPS serve Vientiane. Land transport from
  VN is common for freight; no B2C cross-border parcel operator from VN
  found.

## Cambodia (KH)

- **VAT:** 10% on CIF + duty + special tax [3rd-party, several].
- **Duty bands:** 0%, 7%, 15%, 35% [3rd-party].
- **De minimis:** sources conflict: about US$50 [3rd-party], or US$200 for
  personal shipments [3rd-party]. One guide says most commercial
  e-commerce parcels pay VAT whatever the value. DHL publishes a de
  minimis guide for KH (not read) UNVERIFIED.
- **Couriers:** DHL, FedEx and UPS. KH is a short land route from southern
  VN, and VN domestic carriers (Viettel Post) have direct operations in KH
  (see `03`), possibly a cheap lane later UNVERIFIED.

## Myanmar (MM)

- **Commercial tax on import:** 5% on CIF + duty. Specific Goods Tax on
  some categories. Union Tax Law 2025 covers the fiscal year 2025-04-01 to
  2026-03-31 [published, Lincoln Myanmar and VDB Loi booklets].
- **Duty:** 0–40% by HS code.
- **De minimis:** a local adviser's deck (Oct 2025) says parcels under
  US$50 are exempt from duty and commercial tax, and that courier parcels
  are split on the manifest at US$500 (separate procedure above it).
  Another guide says there is no threshold UNVERIFIED.
- **Couriers:** DHL and FedEx still accept shipments to Yangon, with
  restrictions and longer lead times [3rd-party].
- **Sanctions:** the US, EU and UK sanction named persons, banks and
  state companies, not the whole country (general knowledge, not
  re-checked). For us that means sanctions screening of buyers and
  addresses, and possible trouble with PSPs and card acquiring for MM
  buyers. **Adviser** before opening.

## What this means for the design

- Market modules `la`, `kh`, `mm`: `TaxCollection = BorderPaid`. Duty comes
  from the duty-rate vendor, or ATIGA 0% when the SKU has real VN origin.
  Rate tables are effective-dated.
- Route integrator only; no order cap needed, since there is no
  seller-side scheme to stay under.
- MM gated behind `status = closed("sanctions review pending")` until an
  adviser has looked.

## Sources

- https://www.tilleke.com/insights/laos-reinstates-10-vat-rate/
- https://taxsummaries.pwc.com/lao-pdr/corporate/other-taxes
- https://www.easyship.com/countries/shipping-to-lao
- https://www.vatitstream.com/compliance/laos
- https://www.dhl.com/discover/en-kh/logistics-advice/import-export-advice/de-minimis-thresholds-guide
- https://www.manhattansez.com/en/cambodia-import-duties-customs-clearance/
- https://parcelpath.com/carrier-services/shipping-to-cambodia/
- https://taxdo.com/resources/countries/ap/cambodia
- https://www.lincolnmyanmar.com/wp-content/uploads/2025/10/Export-Import-Presentation-2.10.2025.pdf
- https://www.vdb-loi.com/wp-content/uploads/2025/11/Myanmar-Tax-Booklet_2025-2026.pdf
- https://www.parcel-guide.eu/countries/myanmar/
- https://taxdo.com/resources/countries/ap/myanmar

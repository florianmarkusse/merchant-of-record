# Plan: logistics, next steps

Status: written 2026-11-07, to pick up later. The data is in
`docs/markets/` (see `plan-markets-data.md`); run
`uv run scripts/markets.py build` and open `docs/markets.html` for the
current comparison.

## Where we are (2026-11-07)

Rates loaded, all list prices (no account discount):

| Provider | Service(s) | Source | Status |
|---|---|---|---|
| DHL Express | Express Worldwide | 2026 VN rate guide (PDF) | verified |
| UPS | Worldwide Express Saver, Expedited | 2026 VN rate guide (PDF) | verified |
| UPS | Worldwide Economy (US only) | 2025 guide | verified, old |
| FedEx | International Priority, International Economy | 2026 VN export tariff (PDF) | verified |
| FedEx | IP promo, FICP | search extracts | unverified |
| J&T Express VN | international express | 2024 card, MY/CN confirmed by April 2026 sheet | verified |
| VIO ePacket (YunExpress network) | main routes, US-Saver, Extra | vioepacket.com, from 2026-11-01 | verified |
| Vietnam Post EMS | EMS | third-party read of the calculator, DE 1 kg re-checked | secondary |

Fuel is stored as this week's percentage (DHL 50%, FedEx 52.25%, UPS
52.75% for 2026-11-09 to 2026-11-15); VIO includes fuel; J&T's is
monthly and unknown.

Indicative 0.5 kg list prices (US$, before fuel): VIO 7-16, J&T 16-26,
DHL 54-73, UPS 62-95, FedEx IP 89-121. For light parcels the economy
lines win by far; the open question for them is duty-paid delivery.

Quote requests sent on 2026-11-07 (`questions.csv`, status `sent`):

| Question | Provider | Channel |
|---|---|---|
| q01 | DHL Express VN | email |
| q04 | Janio | website benchmark form (LaaS) |
| q05 | Boxme | boxme.asia contact form |
| q06 | J&T International | contact@jet-logistics.com |
| q07 | Asendia VN | duong.nguyen@asendia.com |
| q08 | VIO ePacket | WhatsApp / admin@vioshipping.com |

FedEx (q02) and UPS (q03) are held until we have business accounts.

## Next steps

### 1. Decide which company holds the carrier accounts

Blocks the account step. A DHL, FedEx or UPS business account in
Vietnam normally needs a registered company with a Vietnamese tax code.
The holder also decides whose EU IOSS and UK VAT numbers go on the
shipments, and whether FedEx's 2.5% third-party billing surcharge
applies (when a non-VN entity pays). Options: a VN entity, a SG entity
paying through third-party billing, or none yet. This is a company
decision, not only a logistics one; record it in AGENTS.md or a plan
when made.

### 2. Open business accounts at DHL, FedEx and UPS

Done by the logistics lead (Claude cannot create accounts). An account
gives the full rate files, online quotes and usually a small standard
discount; the real discount still comes from a sales rep, so use the
account as the door to sales and send q02 and q03 from it.

Request quotes in one comparable shape, so they load as `quote` rate
cards:

- Parcels: 0.25, 0.5, 1, 2 and 5 kg, non-documents.
- Destinations: SG, MY, TH, PH, ID, JP, KR, AU, US, DE, GB.
- Volumes: 500, 5,000 and 50,000 parcels a month.
- Ask for: discount on freight, the duty-paid fee and its minimum,
  fuel basis, and the zone of each destination.

Save every quote in `docs/markets/sources/<provider>/`.

### 3. Build an all-in cost comparison (Claude, no waiting needed)

Today's comparison is freight only. Add per country and weight: fuel,
the duty-paid fee (e.g. DHL 2% of duties, minimum VND 600,000; UPS VND
600,425 per shipment), residential and US/EU processing surcharges, and
VIO's EUR 5 per HS code. This shows how large a big-three discount has
to be before it matters against the economy lines.

### 4. Check product rules per carrier (Claude)

Fill `service_restrictions.csv` for the six categories: lithium
batteries in keyboards (keycaps), fragile ceramics (packing and
liability), wood and lacquerware (US-Saver already refuses them; check
fumigation and other carriers), leather.

### 5. Follow up on the requests in flight

About a week after sending (around 2026-11-14), follow up by WhatsApp,
Zalo or phone where there is no reply. Contacts are in `questions.csv`
and `providers.csv`. When a reply arrives: save it in
`docs/markets/sources/<provider>/`, set the question to `answered`, and
load the rates and answers. The answers that matter most are duty-paid
delivery (who pays duties, under whose tax numbers), API and webhooks,
pickup in Ho Chi Minh City, and returns.

### 6. Keep weekly values current before any comparison

Fuel for DHL, FedEx and UPS (their surcharge pages, readable in
Chrome), and the demand surcharges for DHL and FedEx (not loaded yet).

## Not now

- YunExpress directly: VIO is its VN reseller and publishes rates.
- Vietnam Post: about US$65 per kg to Germany, duties unpaid, no API;
  kept only as a public reference price.
- DHL Global Forwarding and DHL Supply Chain: only relevant for
  consolidation into a destination hub or stock held abroad.

# Plan: logistics, next steps

Status: written 07-Nov-2026, to pick up later. The data is in
`docs/markets/` (see `plan-markets-data.md`); run
`uv run scripts/markets.py build` and open `docs/markets.html` for the
current comparison.

## Where we are (07-Nov-2026)

Rates loaded, all list prices (no account discount):

| Provider | Service(s) | Source | Status |
|---|---|---|---|
| DHL Express | Express Worldwide | 2026 VN rate guide (PDF) | verified |
| UPS | Worldwide Express Saver, Expedited | 2026 VN rate guide (PDF) | verified |
| UPS | Worldwide Economy (US only) | 2025 guide | verified, old |
| FedEx | International Priority, International Economy | 2026 VN export tariff (PDF) | verified |
| FedEx | IP, IE and FICP promo (VN account rates) | 2026 promo PDFs, saved 10-Nov-2026 | verified; FICP zones assumed equal to IP |
| J&T Express VN | international express | 2024 card, MY/CN confirmed by April 2026 sheet | verified |
| J&T Express VN | international express, quoted lanes (SG at two speeds) | quote in reply to q06, saved 14-Nov-2026 | verified; includes fuel and VAT; no validity date |
| VIO ePacket (YunExpress network) | main routes, US-Saver, Extra | vioepacket.com, from 01-Nov-2026 | verified |
| Vietnam Post EMS | EMS | third-party read of the calculator, DE 1 kg re-checked | secondary |

Fuel is stored as this week's percentage (DHL 50%, FedEx 52.25%, UPS
52.75% for 09-Nov-2026 to 15-Nov-2026); VIO includes fuel; J&T's is
monthly and unknown.

J&T's 14-Nov-2026 quote includes fuel and VAT. At 0.5 kg it costs
US$ 10 to SG, 17-25 to KR, AU, TH and MY/PH/ID, 27-36 to GB, JP and US, and
42-54 to the EU: cheap to SG, about the 2024 card plus 45-50% elsewhere in
Asia and the US, and dear to the EU. Still duties unpaid; follow-up q45
asks validity, DDP, API, transit and the sheet's oddities
(`sources/jt_intl/README.md`).

Indicative 0.5 kg list prices (US$, before fuel): VIO 7-16, J&T 16-26,
DHL 54-73, UPS 62-95, FedEx IP 89-121. For light parcels the economy
lines win by far; the open question for them is duty-paid delivery.

Quote requests sent on 07-Nov-2026 (`questions.csv`, status `sent`):

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

### 3. Build an all-in cost comparison (done 07-Nov-2026)

`markets.html#all-in` and the `All-in` sheet: per reference parcel
(`scenarios.csv`: 0.25, 0.5, 1, 2, 5 kg at US$25, 40, 60, 100, 200),
destination and rate card, the list price of freight + fuel + per-parcel
fees + the duty-paid fee where `border_charges.csv` says duty or tax is
due. Each cell's tooltip lists its parts. A second table per parcel gives
the freight discount DHL, FedEx IP and UPS Saver list prices need to match
the cheapest economy line that is published, complete and duty-paid.

What it shows (list prices, fuel of 09-Nov-2026):

- Where an economy line works (SG, JP, AU, GB via VIO), the big three
  need a freight discount of about 84-99%. No sales discount gets there;
  the big three are a premium or fallback option, not the default.
- To the EU the duty-paid fee decides: since 01-Jul-2026 every EU parcel
  pays the EUR 3 flat duty, so DHL's VND 600,000 minimum (about US$23) or
  UPS's VND 600,425 always applies. Up to 1 kg that fee alone is more
  than VIO's whole price, so no freight discount is enough; at 2 kg the
  need is 92-99%. The minimum is what to negotiate (q01), more than the
  freight discount. The same fee applies on every US parcel.
- No usable economy line yet to the US, MY, TH, PH, ID or KR: VIO's US
  lines and Extra are not duty-paid (DAP), and J&T's fuel and duty-paid
  terms are unknown. To the US today only the big three (and UPS
  Worldwide Economy at about US$62 for 0.5 kg) are duty-paid. These lanes
  wait on q04 to q08.

Not modelled yet: volumetric weight (actual weight is assumed), remote
areas, returns, discounts (no quotes yet).

### 4. Check product rules per carrier (partly done 07-Nov-2026)

`service_restrictions.csv` now has DHL (VN pages), FedEx and UPS (from the
VN rate PDFs; their web pages block automated reads) and VIO. Findings:

- Wired keyboards and keycaps are fine everywhere. Boards with lithium
  batteries need a pre-approved DHL account, cannot go FedEx International
  Economy, and are checked case by case at VIO.
- Apparel in poly mailers pays an additional handling fee at FedEx
  (VND 679,700) and UPS (VND 423,940): ship in cardboard with them.
- Ceramics: liability is low (DHL 9 SDR/kg in the VN annex, FedEx about
  US$20/kg or US$100, VIO Extra at most VND 1,150,000); no one states a
  fragile exclusion. US-Saver refuses food-contact ceramics (FDA).
- Wood and lacquerware: VIO wants to check them first; DHL, FedEx and UPS
  say nothing. Destination rules (AU) apply either way.

J&T and Asendia were read in Chrome on 10-Nov-2026 (J&T refused-goods
list, terms and compensation policy; Asendia Singapore GTC May 2025,
which Asendia VN links as its terms):

- J&T refuses batteries outright (refused list item 2): no boards with
  lithium batteries. International compensation is capped at US$100
  undeclared or VND 30,000,000 declared.
- Asendia GTC §7.2 treats glass, porcelain and other fragile goods as
  valuables: only by written agreement, otherwise at our own risk with no
  liability at all. Ceramics need that agreement before e-PAQ is usable
  for them.
- Neither says anything about wood, bamboo or lacquerware.

Open: wood and lacquerware everywhere, VIO battery and compensation
terms, J&T Section II batteries, and Asendia's contracting entity,
fragile-goods agreement and packing specifications. q35 now asks only
these (draft, to send with the 14-Nov-2026 follow-ups); FedEx and UPS are
in their drafts (q02, q03).

### 5. Follow up on the requests in flight

About a week after sending (around 14-Nov-2026), follow up by WhatsApp,
Zalo or phone where there is no reply. Contacts are in `questions.csv`
and `providers.csv`. When a reply arrives: save it in
`docs/markets/sources/<provider>/`, set the question to `answered`, and
load the rates and answers. The answers that matter most are duty-paid
delivery (who pays duties, under whose tax numbers), API and webhooks,
pickup in Ho Chi Minh City, and returns.

### 6. Keep weekly values current before any comparison

Fuel for DHL, FedEx and UPS (their surcharge pages, readable in
Chrome), the demand surcharges (loaded 07-Nov-2026: DHL from 01-Oct-2026 to
05-Feb-2027, FedEx from 02-Nov-2026; both can change on notice) and the ECB
display rates in `scripts/markets.py` (06-Nov-2026).

## Not now

- YunExpress directly: VIO is its VN reseller and publishes rates.
- Vietnam Post: about US$65 per kg to Germany, duties unpaid, no API;
  kept only as a public reference price.
- DHL Global Forwarding and DHL Supply Chain: only relevant for
  consolidation into a destination hub or stock held abroad.

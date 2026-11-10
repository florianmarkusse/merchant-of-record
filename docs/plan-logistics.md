# Plan: first logistics integration

Status: design proposal, second draft (2026-11-06), waiting for
confirmation. It replaces the first draft from the same day, which capped
order values to stay under every country's duty-free limit. That draft
did not fit the candidate merchants: they sell at US$80–2,000 and ship
worldwide, and most of them already use DHL. No code is written until
this is confirmed. Revised 2026-11-08: §4 now decides indirect tax and
duty separately (see there), also waiting for confirmation.

Inputs: `docs/research-logistics.md`, `docs/research-product-compliance.md`
(including the new `07-keycaps-ceramics-lacquer.md`),
`docs/research/logistics/08-regulations-la-kh-mm.md`,
`docs/plan-psp-integration.md` §6.5 and `docs/plan-merchant-surfaces.md`
§3–5.

## 1. How it works: one order from start to finish

The first carrier is **DHL or FedEx, decided on quotes** (§2, decided
2026-11-06). DHL is used in this walkthrough; FedEx works the same way.

**The idea in one sentence:** we have one DHL Express account. Every
parcel goes out on it with "duties and taxes paid by shipper", and our
checkout charges the buyer everything up front. DHL clears customs, pays
whatever is due at the border, and sends us one invoice a month.

Example: a buyer in Singapore orders CNES shoes for US$350.

| Step | Who | What happens |
|---|---|---|
| 1. Checkout created | merchant's shop → our API | The line has: price, customs description, HS code, origin (VN), weight and box size |
| 2. Address entered | buyer → us | Is Singapore open (§3), and are shoes allowed there (product gate)? |
| 3. Price shown | us → DHL, duty source | We ask DHL for the rate on our contract (VN → SG, 1.5 kg). We work out the tax path (§4): US$350 is above S$400 per item, so SG customs charges 9% GST at the border. The duty source says 0% duty. The buyer sees **one all-in price**: shoes + shipping + GST estimate |
| 4. Payment | buyer → PSP | The money lands in our account |
| 5. Label booked | our worker → DHL API | Shipment on **our** DHL account. The shipper address is the merchant's workshop in Saigon. Duties are billed to us. DHL sends back the label PDF, the commercial invoice and a tracking number. We book a pickup at the workshop |
| 6. Packing | merchant | Prints the label from our dashboard, packs, hands the box to the DHL driver |
| 7. Export | DHL | DHL declares the export to VN customs as the merchant's customs agent, and flies the box to Singapore |
| 8. Import | DHL | SG customs charges 9% GST. DHL pays it and delivers. The buyer pays nothing at the door |
| 9. Tracking | us ← DHL | We poll DHL's tracking API (DHL Express has no tracking webhooks). The events move our shipment to `delivered` |
| 10. Payout | us → merchant | After delivery plus the payout delay, the merchant's balance is released: US$350 minus our fee |
| 11. Invoice | DHL → us | Monthly invoice per waybill: freight, the GST paid, the duty-paid fee. We match each line to its shipment. A difference from our estimate goes to the merchant's balance (decided 2026-11-04) |

**The same order to the UK at £95 (keycaps)** differs in one step. Below
£135, UK law makes the foreign seller charge VAT at checkout. In step 3 we
add 20% UK VAT as our own tax line and print our UK VAT number on the
waybill. UK customs then charges nothing. We file the UK VAT return
quarterly. This is the only kind of country that needs a registration
before the first sale.

**A cheap order to Australia (US$80)** needs nothing. Below A$1,000 there
is no duty or GST at the border, and below A$75k a year we don't have to
register. Step 3 adds no tax line.

So for every order and country, the indirect tax (VAT, GST) takes exactly
one of three paths, and the import duty one of two: not due, or paid at
the border. The two are decided separately, because a cheap order can
have its VAT collected by us and still owe duty at the border (EU €3 flat
duty, §4). That is the whole tax logic of v1 (§4).

## 2. What we build in v1

1. **Country table.** One data row per destination: open or closed (and
   why), duty-free limit and how it is measured, any seller scheme with
   its limit and our turnover threshold, the fields the waybill needs.
   Effective-dated, versioned, and stored on every quote.
2. **Tax path rule.** One function that reads the row and the order and
   returns a pair: the indirect-tax path (`NotDue`, `SellerCollected` or
   `BorderPaid`) and the duty path (`NotDue` or `BorderPaid`) (§4).
3. **Duty source.** A vendor API (Zonos in estimate mode at about US$2 per
   order, or Easyship Taxes & Duties at about US$0.10 per call) behind an
   interface, with a mock. Used only when either path is `BorderPaid`.
4. **One integrator module: DHL or FedEx, decided on quotes** (decided
   2026-11-06). Rates, shipment with label and commercial invoice
   (duties billed to us), pickup, tracking (DHL by polling, FedEx by
   signed webhooks), return label, invoice import. Plus the mock
   courier, which stays the default in dev and tests.
   - **Express and a cheaper service where the carrier has one** (decided
     2026-11-06: express is not always what the buyer prefers). DHL VN
     sells only Express Worldwide. FedEx sells International Priority and
     the cheaper International Connect Plus (FICP) and International
     Economy. So FedEx gives two choices per lane in one contract, which
     favours it if its prices hold up (§4).
   - Checkout shows every service the routing table has for the lane,
     e.g. "Express, 1–3 days" and "Standard, 3–6 days".
5. **Shipment and return state machines**, the ledger accounts, and the
   merchant dashboard pieces: print label, book pickup, see tracking,
   handle return requests.
6. **Product gate.** `product_category` per line, checked against the
   destination. A blocked line is a typed error at checkout.

Not in v1 (§9): a cheaper SEA economy line, seller-scheme registrations
other than the UK, return hubs, orchestrators, EU and US (each waits for
specific answers, §3).

## 3. Countries in v1

**Open from day one** (border-paid through DHL, or nothing due; no
registration):

| Region | Countries | Notes |
|---|---|---|
| SEA | SG, MY, PH, TH, LA, KH | TH and LA have no duty-free limit, so every parcel is `BorderPaid`. ATIGA 0% duty for goods truly made in VN. ID closed (below) |
| East Asia | HK, TW, KR, JP | KR needs the buyer's customs code (PCCC) on the waybill. JP knitwear and leather are dutiable at any value. JP seller registration only from 2028-04-01 |
| Oceania | AU, NZ | nothing due below A$1,000 / NZ$1,000 until our turnover thresholds |
| Europe (non-EU) | CH, NO | border VAT through DHL. NO's VOEC scheme becomes mandatory from NOK 50k a year (confirmed 2026-11-10) |
| Americas | CA | duty and tax from CAD 20, border-paid |

**Open with one condition each:**

| Country | Condition | Why |
|---|---|---|
| UK | UK VAT registration before launch | orders up to £135 must have VAT charged by the seller |
| EU | an EU product-safety representative (a service, vendor-quoted €150–500 a year) and packaging registrations (DE before the first parcel; textile EPR in FR, NL) | The tax side works without IOSS: DHL pays import VAT at the border. The €3-per-item duty applies up to €150 |
| AE | UAE VAT registration (no threshold for non-residents selling to consumers, per advisers; FTA guide not read) | 5% duty and 5% VAT; whoever customs lists as importer owes the import VAT (FTA e-commerce guide) |
| US | adviser on importer of record under DHL duty-paid and on CPSC eFiling data; re-price after the Section 301 decision on 2026-11-29 | duty of about 30–45% on VN apparel; cotton needs origin proof (UFLPA) |

**Closed, with the reason shown at checkout:** ID (minimum price and
representative rules: adviser), CN, IN, BR, MX, ZA (local importer or
partner needed), CL (seller registration needed, later), MM (sanctions
review), NG, and every comprehensively sanctioned country.

## 4. The tax path rule

Indirect tax and import duty are decided separately (decided 2026-11-08:
the earlier single `TaxPath` assumed every order takes exactly one path,
but seller schemes cover only VAT/GST, never duty. A cheap order can have
its VAT collected by us and still owe duty at the border, e.g. an EU order
under IOSS that pays the €3 flat duty, so one order needs both answers).

```python
type IndirectTaxPath = NotDue | SellerCollected | BorderPaid
type DutyPath = NotDue | BorderPaid

@dataclass(frozen=True)
class TaxPaths:
    indirect_tax: IndirectTaxPath  # VAT, GST, SST, JCT
    duty: DutyPath

def tax_paths(row: CountryRow, order: OrderValue, us: Registrations) -> TaxPaths:
    # Indirect tax:
    # 1. A seller scheme covers this order and we are (or must be) registered
    #    → SellerCollected: we charge it at checkout and print our number on
    #    the waybill. Once registered this is mandatory for every order in
    #    scope; there is no per-order choice.
    # 2. Else, the order is under the border VAT/GST-free limit → NotDue.
    # 3. Else → BorderPaid: import VAT/GST charged by customs.
    # Duty, independently:
    # 1. Under the duty-free limit (which can differ from the VAT/GST limit),
    #    or a 0% rate for this HS code and origin → NotDue.
    # 2. Else → BorderPaid. There is no seller scheme for duty.
    # If either is BorderPaid: the duty source estimates what customs will
    # charge (import VAT is usually on value + duty), DHL pays at the border,
    # we charge the estimate at checkout, and the duty-paid fee applies.
```

The four combinations that occur:

| Indirect tax | Duty | Example |
|---|---|---|
| `NotDue` | `NotDue` | AU order under A$1,000, below our A$75k |
| `SellerCollected` | `NotDue` | UK order under £135 |
| `SellerCollected` | `BorderPaid` | EU order under €150 once we use IOSS (€3 flat duty) |
| `BorderPaid` | `BorderPaid` or `NotDue` | TH at any value; SG over S$400 (0% duty on apparel) |

The duty-paid fee has a minimum (below), so in the third row the seller
scheme does not save the fee: it only makes the VAT part exact. It costs
no extra tax either (the rate is the same whoever collects it), only the
filing work. This is why IOSS saves little while the EU flat duty lasts
(2026-07-01 to 2028-07-01, then full duty).

IOSS can even cost duty (found 2026-11-08, from TAXUD guidance of
2026-06-08 in `docs/research/logistics/07-regulations-rest-of-world.md`):
goods with EVFTA preferential origin escape the €3 only if VAT is **not**
collected through IOSS and the parcel goes on a full H1 declaration. So
for VN-made goods with VN or EU fabric under €150, IOSS turns 0% duty into
€3 per tariff line. Whether goods with a 0% MFN rate (no preference
needed) also escape the €3 is UNVERIFIED. The choice between IOSS and
border VAT in the EU is therefore per product origin, not only per order
value, and stays open until the entity decision.

**Which services an order may use** follows from its `TaxPaths` (decided
2026-11-08: customs charges whoever declares the parcel at the
destination and holds it until paid, so on a duties-unpaid (DAP) line the
buyer is billed at the door, and from VN we cannot pay each country's
customs ourselves; a duty-paid service is exactly that arrangement. But
when nothing is due at the border, there is nothing to collect, and a DAP
line behaves like a duty-paid one):

- Both paths `NotDue` or `SellerCollected` (nothing due at the border):
  any service on the lane, duty-paid or DAP, as long as it passes our
  scheme number (UK VAT, IOSS, OVR, LVG, ARN) in its customs data. A line
  that cannot carry the number counts as unusable for these orders, or
  customs taxes the parcel again.
- Either path `BorderPaid`: duty-paid services only.

So the routing table filters services per order, not only per lane. For
example, a UK order under £135 (VAT collected by us, no duty) can go on a
DAP economy line such as VIO's, while a UK order over £135 on the same
lane cannot. Paying the buyer back after a door charge is ruled out: a
bill at the door leads to refused parcels.

| Country | Seller scheme (limit) | We register when | Border duty-free limit |
|---|---|---|---|
| UK | VAT, up to £135 | first sale | — (the scheme covers it) |
| SG | GST, up to S$400 per item | S$1m global and S$100k to SG | S$400 per item |
| MY | LVG, up to RM 500 | RM 500k in 12 months | RM 500 |
| AU | GST, up to A$1,000 | A$75k a year | A$1,000 |
| NZ | GST, up to NZ$1,000 | NZ$60k a year | NZ$1,000 |
| NO | VOEC, under NOK 3,000 per item | NOK 50k (confirmed 2026-11-10) | — |
| CH | — | CHF 100k (then VAT registration with a representative) | VAT under CHF 5 |
| others in §3 | none | — | per row |

Our turnover per country is summed across all merchants from the ledger.
A daily job emits `registration_threshold_approaching` at 80%, so we
register before rule 1 starts to apply.

**Always "duties paid by shipper"**, even when nothing should be due. A
buyer must never get a bill at the door. DHL VN's duty-paid fee is
currently about US$23 per shipment (VND 600,000 or 2% of duties,
whichever is higher), which is heavy on an US$80 order.

Found 2026-11-06 while checking whether US$23 is competitive:

- **It is DHL's normal fee, not a Vietnam penalty.** Other origins pay the
  same: US 2% with a US$17 minimum, Ireland 2% with an €18 minimum.
- **Under DAP the buyer pays a similar fee at the door:** Germany 2% with
  a €12.50 minimum, the Netherlands €18, and US$17.50–20 in the US. So
  duty-paid shipping moves the fee from the buyer to us; it does not add
  one.
- **It is a percentage of the duty and tax DHL pays, with a minimum.**
  Confirmed in the DHL Express Service & Rate Guide 2026: Vietnam (p. 10,
  21): "Duty Tax Paid: 2% of fiscal charges with minimum of 600,000 VND",
  and "charges apply whenever duties, taxes or other regulatory fees
  occur". So it only hits orders where the indirect tax or the duty is
  `BorderPaid`, not orders where both are `NotDue` or `SellerCollected`. The same page says duties can be billed "to the
  shipper or a third party", which is what we need (our account, the
  merchant as shipper).
- Registering for a seller scheme (UK, later SG, AU, the EU's IOSS)
  removes the border charge and with it the fee, for the cheap orders
  where the fee hurts most, but only where those orders owe no duty. In
  the EU the €3 flat duty keeps the fee on every parcel until 2028-07-01.

Negotiating the minimum is the first question for DHL.

**DHL VN 2026 list prices** (Express Worldwide parcels, from the guide
above; VND converted at about 26,000 per US$; before fuel, VAT and any
discount; DHL VN publishes no economy product):

| Billed weight | Zone 1: SG, MY, TH, HK | 3: PH, ID, TW | 4: JP | 5: KR | 6: AU, NZ, IN, LA, KH, MM | 7: US, CA | 8: UK, CH, most of the EU | 9: NO, AE, SA, some EU (HU, HR, SK) |
|---|---|---|---|---|---|---|---|---|
| 0.5 kg | $55 | $64 | $64 | $73 | $73 | $74 | $74 | $89 |
| 1 kg | $61 | $75 | $75 | $85 | $85 | $86 | $90 | $110 |
| 2 kg | $75 | $90 | $96 | $108 | $114 | $117 | $122 | $151 |
| 3 kg | $89 | $105 | $118 | $132 | $138 | $147 | $153 | $192 |

Other charges from the guide: duty-paid 2% of duties and tax, minimum
VND 600,000 (US$23); residential address option VND 150,000 (US$6);
remote area VND 15,000 per kg, minimum VND 735,000 (US$28); address
correction VND 350,000; restricted destination and elevated risk VND
918,000 each; fuel as a monthly percentage (index, not in the guide;
about 50% in November 2026 per `research-logistics.md`). Volumetric
weight is L×W×H cm / 5,000 per piece, rounded up to 0.5 kg. The guide
says rates exclude VAT.

So a 1 kg parcel to Singapore is about US$61 list + about US$30 fuel,
before a contract discount (estimated 30–50% at 500 parcels a month).
Express is the only DHL product from VN, which is why a cheaper lane
(§9) matters for cheap orders.

**Cheaper integrator services from VN** (1 kg list prices before fuel
and discount; FedEx figures are search extracts of its 2026 VN promo
sheets "for qualified customers", which FedEx blocks from automated
download, so UNVERIFIED; UPS from its 2025 VN guide):

| To | DHL Express Worldwide | FedEx FICP | UPS Worldwide Expedited |
|---|---|---|---|
| SG | $61 | ~$48 | $51 |
| US | $86 | ~$71 | $75 |
| DE, UK | $90 | ~$67 | $78 |
| AU | $85 | ~$64 | $71 |

The cheaper tier saves about 20–25% and keeps the integrator's duty-paid
billing, tracking and API. FICP lane pairs are confirmed per contract.
UPS Worldwide Economy exists from VN to the US only.

**Real economy lines** (postal-style, slower, much cheaper) exist from VN
through **Asendia** (e-PAQ, 200+ destinations, duty-paid, economy for some
destinations, best returns product) and **YunExpress Vietnam** (run by VIO
Shipping: an ePacket-style line under 2 kg, duty-paid EU parcels with an
IOSS number, lanes to US, UK, AU, JP, KR; 7–12 days advertised). Neither
publishes rates. They work most easily where we collect the tax
ourselves (EU IOSS, UK VAT, AU GST), so they arrive together with those
registrations (§9).

## 5. Returns and refunds

**One returns policy floor for every country** (proposed): a 14-day
withdrawal right from delivery, and a full refund for defects. That meets
the strictest law we sell into (EU, UK, NO, TW, KR), so no country needs
its own return rules. The merchant can be more generous, never less.

| Reason | Refund | Return transport |
|---|---|---|
| Withdrawal within 14 days | everything paid, duties and tax included | DHL return label on our account to the merchant in VN, cost deducted from the merchant's payout (decided 2026-11-04). Or the merchant's policy says "keep it" (refund without return) |
| Defective, not as described, not delivered | everything paid | as above, never charged to the buyer |
| Change of mind after 14 days | per the merchant's policy | per the merchant's policy |

- Refund amounts are computed from the reason, not typed in.
- Tax we collected ourselves (UK VAT, later SG, AU and the others) is
  reversed with a credit note.
- Duty and import tax DHL paid at the border are usually lost on a
  return. They are booked to the merchant's balance per the contract.
  Claiming them back (EU and UK reliefs, US drawback) is not in v1.
- **Back in VN:** a returned item is re-imported. It is free of import duty
  if the papers point to the original export declaration (Decree 134/2016
  Art. 33), so we store DHL's export declaration number on every
  shipment. Whether VN VAT applies on the re-import is UNVERIFIED
  (adviser).
- For cheap items, a DHL return costs more than the item. "Refund without
  return" is the merchant's choice per country (surfaces plan §5).

## 6. The VN side

- **Shipper:** the merchant's address on the waybill, for pickup and
  export. The DHL account and the duty bill are ours. **Adviser and
  DHL:** can our SG or NL entity hold the account for exports from VN, and
  whose names go in the shipper, exporter and seller fields?
- **Export declaration:** DHL files it as customs agent. DHL VN says the
  exporter must be registered with VN tax and customs, so the merchant
  needs a VN tax code. Many artisan workshops are household businesses
  (hộ kinh doanh): **adviser** on whether they can export this way.
- **Merchant's 0% export VAT:** the merchant keeps our contract, our
  payouts and DHL's export declaration numbers. Our merchant statement
  lists all three per period.

## 7. Modules, data, messages, ledger

```
couriers/port.py      records and the CourierModule protocol
couriers/mock.py      default in dev and tests
couriers/dhl.py or couriers/fedex.py   first real courier, whichever wins the quotes
couriers/routing.py   the one place that picks a courier and service per lane
duties/port.py, duties/mock.py, duties/<vendor>.py
markets/rows.py       the country table (§3, §4), effective-dated
markets/tax_path.py   the rule in §4
shipments.py, returns.py
```

Courier port operations: `quote`, `book` (label, commercial invoice,
duties billed to us), `book_pickup`, `cancel`, `decode_event` (a webhook
or one polled page, the same inbox messages either way), `import_invoice`,
`book_return`, `capabilities`. Every result is a union of records: ok, a
typed refusal, or `OutcomeUnknown` after a timeout.

Tables: `shipment` (with the declared customs snapshot and DHL's export
declaration number), `shipment_event` (append-only, unique on the
carrier's event id), `courier_request` (idempotency key, unique),
`courier_inbox`, `courier_invoice_line`, `return_request`, `return_event`.
SKU and line data as in the surfaces plan, plus box size,
`product_category`, `food_contact` and `wood_species`.

Shipment states: `created → label_requested → labelled → picked_up →
in_transit → delivered`, with `label_failed`, `cancelled`,
`held_at_customs`, `returning_to_sender`, `returned_to_sender` and `lost`.
Return states: `requested → approved → label_issued → in_transit →
received → refunded`, with `rejected` and `refunded_without_return`.

Messages (past tense, versioned, ordered per shipment or return):
`shipment_created`, `shipment_label_booked`, `shipment_label_failed`,
`shipment_picked_up`, `shipment_delivered`, `shipment_held_at_customs`,
`shipment_lost`, `courier_invoice_imported`, `return_requested`,
`return_approved`, `return_received`, `refund_requested`,
`registration_threshold_approaching`.

Ledger accounts (shown for DHL; one set per courier): `shipping_payable:dhl` and `duties_payable:dhl` (credited
at payment, debited when the invoice is imported);
`tax_payable:{country}` for the tax we collect; `merchant_payable` takes
duty differences, return labels and refunds per contract;
`courier_variance:dhl` for freight surcharges not in the quote, until we
decide who pays them. Every DHL amount comes in with an exact FX rate and
a named rounding mode, and any remainder is booked.

## 8. Registrations, per country

What we must register for to sell there legally, as found 2026-11-06.
Compiled from the research files plus a short check; several are
UNVERIFIED and all need an adviser before launch. Payment licences are in
`plan-psp-integration.md` §8, not here.

**Once, for us:**

| What | When | Notes |
|---|---|---|
| Our company (SG or NL BV) | before anything | decides who holds the DHL account, the PSP contracts and every registration below |
| EU GDPR representative (Art. 27) | before selling to the EU, only with an SG entity | a NL BV is established in the EU and needs none. Paid services exist |
| UK GDPR representative | before selling to the UK | needed with either entity |
| Thai personal-data representative | before selling to TH | foreign controllers targeting TH: adviser (not checked) |

**Per country, for the v1 list (§3):**

| Country | Before the first sale | Later, at a threshold |
|---|---|---|
| SG | none | GST under OVR at S$1m global and S$100k to SG |
| MY | none | sales tax (LVG) at RM 500k in 12 months |
| PH | **DTI E-Commerce Bureau**: the Internet Transactions Act covers foreign sellers with "minimum contacts", and its rules ask every online merchant for its registration details. Whether the portal is live is unclear | — |
| TH, LA, KH | none for goods tax | TH: watch the seller-registration bill |
| HK | none | — |
| TW, KR | none for goods (their foreign-seller VAT is for digital services) | — |
| JP | none | JCT registration and a tax agent by 2028-04-01 |
| AU | none | GST at A$75k a year |
| NZ | none | GST at NZ$60k a year |
| NO | none (voluntary VOEC possible) | VOEC at NOK 50k a year of items under NOK 3,000 |
| CH | none | VAT with a Swiss tax representative at CHF 100k |
| CA | none (GST is paid at the border on imports) | — |
| **UK** | **VAT registration**, because orders up to £135 need seller-charged VAT | — |
| **AE** | **VAT registration**: non-residents selling to consumers have no threshold (adviser sources) | — |
| **EU** | product-safety responsible person (GPSR); packaging EPR in DE (before the first parcel) and in each country as sales start; textile EPR in FR and NL | IOSS (optional; removes border VAT on orders up to €150); EUDR operator for wooden goods from 2026-12-30 |
| **US** | none to sell. As importer of record (if chosen): CBP importer number (Form 5106) and a bond, unless DHL's duty-paid covers it; CPSC eFiling data per product | state sales tax per state at economic nexus (often US$100k a year) |

**On the VN side:** the merchant needs a VN tax code and must be
registered as an exporter (DHL VN guide). Whether our foreign entity
needs anything in VN is the open VN adviser question in §6.

## 8b. Before launch

| What | Who | Blocks |
|---|---|---|
| Entity: SG or NL BV | us | the DHL account, registrations |
| DHL and FedEx quotes in the same week (UPS as a third): rates at 500 and 5k parcels a month for express **and** each cheaper service (FedEx FICP and International Economy, UPS Expedited), FICP lane pairs, the duty-paid fee and its minimum, billing a foreign entity for VN exports, invoice format (EDI or CSV), API access | us | everything |
| Asendia VN and YunExpress VN quotes: economy duty-paid rates to EU, UK, US, AU; API; returns | us | the economy lines, not launch |
| Duty-source contract (Zonos estimate mode or Easyship) | us | `BorderPaid` countries |
| UK VAT registration | us | UK |
| EU product-safety representative, DE packaging registration | us | EU |
| Merchant contract: warranties, indemnity, deductions, reserve, arbitration | us + counsel | first merchant |
| VN adviser: shipper and exporter fields, household businesses, re-import VAT | adviser | launch |
| Product liability insurance | broker | launch |
| Lab and counsel for ceramics, lacquer and EUDR (`07-keycaps-ceramics-lacquer.md`) | lab, counsel | those categories, not launch |

## 9. Later, in this order

1. **A cheaper SEA lane** (Janio, Boxme, J&T, or the VN aggregators ShipX
   and EFEX) once SEA volume justifies it. It is one more courier module
   and one more routing row. Customers see a cheaper "economy" option
   next to DHL.
2. **Economy lines to the EU, UK, US and AU** (Asendia or YunExpress
   Vietnam) once those lanes have volume. They come together with the
   registrations that make them work (UK VAT, EU IOSS, AU GST).
3. **Seller registrations** as each turnover threshold comes near (SG,
   AU, MY, NZ, NO, CH). These change the country rows, not the code.
4. **A second integrator** (whichever of DHL and FedEx was not first,
   or UPS) if its quotes beat the first one on some lanes.
5. **Return hubs** in the busiest countries, and duty reclaims.
6. The closed countries, one by one, as advisers answer.

## 10. Project rules this design applies

- Closed sets (`TaxPath`, states, reasons), matched with `assert_never`.
- Errors as values from every courier and duty-source call.
- Outbox for every outbound call; an inbox keyed on the carrier's event id.
- One module per courier and per duty source, one place that picks by
  name, and a mock as the default.
- Effective-dated country rules, with the version stored on the quote and
  the shipment.
- An append-only ledger: every charge, variance and refund is its own
  entry.
- Money as minor units with currency, exact FX rates, named rounding.
- Compliance named, not assumed (§6, §8).

## 11. Questions for you

1. Is this v1 country list right? Most important: register for UK VAT at
   launch, and start the EU representative and registrations now?
2. One 14-day withdrawal floor for every country (§5)?
3. Shipping price to the buyer: our DHL contract rate passed through, with
   a margin, or set by the merchant (who may subsidise it)?
4. Freight surcharges not in the quote: our cost or the merchant's?
5. Which two or three merchants from the list do we talk to first? Their
   buyer countries and order sizes decide whether the cheaper SEA lane
   (§9.1) is worth doing early.

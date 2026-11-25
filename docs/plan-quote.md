# Markets, the tax path, prices, the quote and the checkout (slice 4)

Status: built (17-Nov to 19-Nov-2026). Applies `plan-architecture.md` §8
with the tax path rule of `plan-logistics.md` §4: market data in the
database, duty rates and carrier prices from switchable sources,
`build_quote`, merchants and the API, and the buyer's checkout up to a
quote it can pay (the pay step is `plan-payments.md` §4).

## 1. Steps

| Step | What | Ends with |
|---|---|---|
| 4a | Markets in the database: countries and their rules, our registrations, FX rates, loaded from `docs/markets/` | `uv run python -m mor.markets.load` fills effective-dated rows; a country can be asked "open? on what rules?" |
| 4b | The tax path rule, the seller-collected tax module (UK first), duty rates with switchable sources and a fake duty vendor | `tax_paths()` and duty rates for any (HS code, origin, destination) |
| 4c | Shipping prices (own rate tables, and a carrier rate call through a fake carrier), fee rules, and `build_quote` | the pure quote, fuzzed: parts sum to the total, every division rounded by a named mode, every remainder booked |
| 4d | Merchants, API keys, checkout sessions and estimates over the API; the quote flow with waiting; the buyer's address and quote pages | a merchant creates a session, a buyer enters an address and sees an all-in price, on the test setup |

## 2. Markets in the database (4a)

- `docs/markets/` stays the research source, edited by hand (plan-markets-
  data). Quoting reads typed tables in PostgreSQL, filled by a loader:
  `python -m mor.markets.load` reads the CSVs, and for each value that
  differs from the row in force writes a new effective-dated row. Rows are
  never updated; every row records the git commit it was loaded from, so a
  quote points at exactly what it used.
- Tables, one per thing, real columns, enums for closed sets:
  - `market_country`: iso2, market status (`open`, `conditional`,
    `closed`, `not_assessed`), the reason shown at checkout when closed.
  - `border_rule`: per country (× category), per charge (`duty`,
    `import_tax`): collected always, never, or above a limit (amount,
    currency, basis: goods, goods plus freight, per item).
  - `seller_scheme`: per country, the scheme's limit and basis and our
    turnover threshold.
  - `tax_rate`: per country (× category), the rate as an exact fraction,
    its base (goods; goods + freight; goods + freight + duty).
  - `our_registration`: per country, scheme and number, effective from.
    Empty until we register.
  - `fx_rate`: base, quote currency, exact rate, source, as-of moment.
- Only the structured files feed quoting: `countries`, `border_charges`,
  `landed_rates`, `categories`, and the rate files in 4c. `facts.csv`
  stays prose for people.
- **Versioned loads** (decided 17-Nov-2026). A `load` that finds any
  difference writes a complete new set of rows, tagged with the load's id,
  the git commit and who loaded it; loading identical files writes nothing
  (a hash of the normalised content decides). Inside a set each row keeps
  its own `effective_from`, so a future-dated change takes effect by
  itself. A quote stores the load id it used; once a newer load exists,
  the page quotes again. Before writing, `load` prints what changed per
  table against the previous set. Chosen over per-row history, where every
  table needs an identity that survives CSV edits and every rename, split
  or deletion becomes a special case. After slice 4 the admin page saves
  through the same function, as a new load with the person as actor.
- `effective_from` is a date, compared with the quote moment's date in
  UTC for now (decided 17-Nov-2026). A law starts at local midnight, so on
  the switch day a buyer far east of UTC may get the old rule for some
  hours; per-country (and per-region: AU, US) time zones come later.
- `landed_rates.csv` is what quotes use for tax and duty, with each row's
  own band (always, up to or above a limit) and collector (border or
  seller), as plan-markets-data decided on 14-Nov-2026;
  `border_charges.csv` stays a research view. Only countries with market
  status `open` quote; `conditional` ones (the UK, waiting on our VAT
  registration) stay closed with their reason until marked `open`.
- **Categories are a table, not an enum** (decided 17-Nov-2026): they
  grow with the data (a row in `categories.csv` now, the admin page
  later), rate and restriction rows reference them by foreign key, and no
  code branches on a particular category. Sets that change only with the
  code stay enums: market status, charge, band, collector, research
  status. `load` refuses a row naming an unknown category.
- Only `verified` and `secondary` values are used (§6).
- **Code** (`src/mor/markets/`): `source` parses the files into typed
  records or errors naming file, line and column; `store` writes and
  reads loads under a transaction-level lock; `changes` writes the report
  (only the fields that changed, as people read them);
  `rules.country_rules` answers open or closed (not listed, its market
  status with the reason, or a rate weaker than secondary) and lists the
  rates in force per charge, most specific first. A newer row with the
  same place, category, charge, collector and band replaces the older
  from its date, whether its rate or its limit changed: that is how a law
  change is entered. `EU` and other groups are told apart from countries
  by the countries file, not by their shape.
- **No import tax row means closed, not tax-free** (decided 18-Nov-2026,
  following §6: fail closed). An open country needs at least one
  `import_tax` row for the category; a country that truly charges none
  gets an explicit 0% row with its source. Missing
  duty rows are not the same: duty comes per HS code from the duty source
  (4b), and a missing rate there starts a lookup.
- The staff page (§5b) shows this for step 4a: `/staff/markets`. A country whose
  quoting values include anything weaker is closed at checkout with the
  reason "rules not confirmed yet".
- **A rule change is a non-event** (decided 17-Nov-2026: governments
  change limits and rates at short notice):
  - one edit and one command: `load` first prints what would change
    (added, superseded, unchanged per table) and writes only when told;
  - a change announced ahead is entered now with its future
    `effective_from` and takes effect by itself on that date;
  - a quote whose rule row is superseded is not paid on: the page quotes
    afresh (plan-architecture §8, "Waiting");
  - a property test checks that loading the same files twice writes
    nothing, and that any edit to a value writes exactly one new row.

## 3. The tax path and duty (4b)

Decided 18-Nov-2026: the rule, the rates per HS code and the staff form
(4b-1, `src/mor/tax/`), and the duty vendor flow (4b-2,
`src/mor/duties/`).

- `tax_paths(rules, order, registrations, fx, on)`, pure. VAT/GST and duty
  are decided independently, so any combination occurs (VAT collected by
  us and duty at the border, and so on):
  - import tax: the country's import tax rows, most specific first; the
    first whose band holds the order's value decides. The value is
    converted into the limit's currency with the load's exact FX rate and
    compared exactly, unrounded. A seller band with our registration on
    that date is `SellerCollected`; a seller band without one is `NotDue`
    (below the border limit nothing is due until we register; where the
    law requires registering before selling, the country stays
    `conditional`); a border band is `BorderPaid`; no band holding is
    `NotDue`.
  - duty: `NotDue` or `BorderPaid` from the duty rows' bands the same way.
    Other border charges (additional duty, fees, other taxes) are listed
    where their band holds.
  - a limit measured on goods plus freight with no freight known yet is
    `NeedsFreight`, not a guess.
- **Rates per HS code** (`docs/markets/hs_rates.csv`, seeded sparsely; the
  staff page fills it after slice 4): per destination (or group), HS code
  prefix, charge (duty, additional duty, import tax, other tax) and origin
  (or any). The longest matching prefix wins; within it an origin row
  before an any-origin row. VAT falls back to the category row's rate
  when no HS row matches; duty never falls back: no rate refuses the
  quote with "duty rate needed".
- **Trade agreements, structured** (decided 18-Nov-2026: not free text).
  A preferential row names its agreement (ATIGA, EVFTA, RCEP: data) and a
  set of conditions from a closed list: `origin_statement`,
  `certificate_of_origin`, `exporter_registration`, `direct_shipment`,
  `not_under_ioss`. It applies only if every condition is met; otherwise
  the standard rate applies and the quote says which condition was
  missing. Which conditions a product or merchant meets is recorded with
  the merchant records (4d); until then none is met, so no preference
  applies.
- **Seller-collected tax per country** needs no code of its own yet
  (decided 18-Nov-2026): limits, bases and rates are data. A jurisdiction
  module (`tax/gb.py`) is written when we approach our first registration,
  for what data cannot say (how the UK measures its £135, how it rounds
  VAT), from verified sources. The general rule is tested with a
  registration made up inside the test; real registrations go into the
  registrations table and apply from their date, with no code change.
- Duty and VAT rates per (destination, origin, HS code) come from our
  own rows, a vendor, or both, by a mode per country (4b-2 below).
- **VND** (decided 18-Nov-2026): the State Bank of Vietnam's daily
  reference rate replaces the research estimate, as the ECB rates do for
  other currencies (25,600 per US$ on 10-Nov-2026, as reported by
  VietnamPlus: `secondary`, so usable).
- The staff page gains a form: country, category, goods value and
  currency, freight, HS code, origin and date; it shows both paths, the
  row that decided each and why, and the rates found or missing.

**Code**: `tax.paths.tax_paths` decides both paths; `tax.line_rates.line_rate`
the rate per HS code with agreements and conditions; `tax.explain` puts a
decision into words for the staff page. Exchange rates are found through
the shortest chain of confirmed rates (VND to SGD runs VND-USD-EUR-SGD),
exact. The tests check why a path was chosen, not only which: a limit
outside its own `up_to` band once passed a test that checked only "not
due".

### 4b-2: duty and VAT rates from vendors (decided 18-Nov-2026)

**What a real vendor gives** (Zonos docs, read 18-Nov-2026: the
import-duty page, the calculate page, the landed cost object and the
pricing page). Zonos calculates a landed cost for a cart; it is not a rate
lookup. Per item it returns `duties[]` and `taxes[]` with an `amount` and
a `formula` as text (`"32%"`, or a specific duty), the HS code it used
(possibly corrected: `hs_code_source` = `hs_correction`), and free-text
`note`s. It applies a preferential rate on its own when the origin
qualifies, without saying which agreement. It is priced per order (US$2
plus 10% of the duties and taxes), and the pages say nothing about
idempotency keys or about storing its answers for later orders.

- **The port asks for one item's rates**: `ask_rates(account,
  destination, origin, hs_code) -> VendorRates | UnknownCode |
  Unavailable | OutcomeUnknown | UndecodableBody`. `VendorRates` holds the
  HS code the vendor used and, per charge (duty, import tax), the formula
  as the vendor wrote it, our exact rate when the formula is a percentage,
  and its note. A Zonos module would send a one-item cart.
- **Duty and VAT from the same answer** (VAT is in the same Zonos call, so
  it costs nothing extra).
- **Answers are always reused** for later orders of the same combination
  (decided 18-Nov-2026). If a vendor's terms forbid it, this becomes a
  setting on its account; nothing else changes.
- **Several vendors**: each is a module behind the port, picked by one
  registry with an exhaustive `match`. Adding one is a module, a
  `duty_provider` value and an account row.
- **Open before Zonos goes live**: whether it can be asked for the
  standard rate only, and whether its terms allow reuse. Until then an
  answer carrying a note (a preference the vendor may have applied) is
  shown to staff and not used. It can be used automatically once the
  vendor names the agreement it applied, so our conditions (§3) can be
  checked against it, or once the merchant records (4d) say the
  conditions are met.

**Who decides the rate: a mode per country** (`duty_sources.csv`:
country, mode, vendor). Trust in our own tables is earned country by
country, measured against the vendor:

| Mode | Decides | Our own rows |
|---|---|---|
| `vendor` | the vendor's answer | compared with every answer; a difference is a `duty_rate_disagreed` fact |
| `own_first` | our row when we have one; the vendor fills the gaps | the truth where present |
| `own_only` | our rows; none refuses the quote, as in 4b-1 | the only source |

A country not listed is `own_only`. To start, every open country is
listed as `vendor` (vendor `zonos`). A country moves to `own_first` when
the agreement count on the staff page shows its rows hold up, judged on
the counted matches and differences. A scheduled spot check
(`duty_rate_check_due`, asking the vendor again for a sample of stored
combinations) keeps checking countries we trust; it needs the scheduler
the quote flow brings, so it comes with 4d. The mode covers the rates
asked per HS code: duty and the import tax (VAT/GST). The band rows
(who collects, which threshold) stay ours.

The vendor is named (`zonos`), not its endpoint: each environment's
database holds a `duty_vendor_account` per name. In prod it points at the
real API; in dev and the test setup the same name points at the fake,
and the registry refuses a fake provider in prod. So the CSV is the same
everywhere.

**Tables** (0010):

- `duty_vendor_account` (id, vendor_name unique, provider: `duty_provider`
  enum, `fake` for now; base_url, timeout_ms).
- `duty_lookup` (id, vendor_name, destination, origin, hs_code, state
  `requested | answered | unknown_code | needs_person`, calls, detail,
  timestamps), `UNIQUE (vendor_name, destination, origin, hs_code)`: the
  "ask once" guard. A switch of vendor asks the new vendor afresh.
- `duty_answer` (lookup_id, charge, formula, rate or NULL when the
  formula is not a percentage, note, hs_code_used, and what our own table
  said at the time: load_id, own rate and line, or none), append-only by
  trigger, `UNIQUE (lookup_id, charge)`.
- `duty_source (load_id, iso2, mode, vendor_name)`, a vendor named
  unless the mode is `own_only`.

**Messages** (aggregate `duty_lookup`, version 1):

- `duty_rate_needed`: to the effect consumer `duty_rate_ask`.
  `prepare` locks the lookup, does nothing unless it is `requested`, and
  counts the call (committed before it is made, so every billed call is
  visible). `call` asks the vendor. `complete` stores the answer and our
  own table's rate beside it, under the lock, and appends:
- `duty_rate_found`, `duty_rate_unknown_code`, or `duty_rate_needs_person`
  (an undecodable answer); plus `duty_rate_disagreed` per charge where our
  own row says something else. The quote flow resumes a waiting quote on
  them (§5).
- Unavailable and OutcomeUnknown are retried with the worker's backoff.
  A lookup is a read, so asking again changes nothing at the vendor and
  needs no duplicate guard; the cost is one more counted call.

**Who asks**: `request_duty_rate`, which inserts the lookup with
`ON CONFLICT DO NOTHING` and appends `duty_rate_needed` only when it
inserted: from the quote flow, when a checkout needs a combination not
yet asked, and from the staff page's order check (outside prod: an "Ask
the vendor" POST with the same-origin check).

**The fake vendor** at `/_fake/duty/`, in the `fake_vendor` schema: rates
per (destination, HS prefix), a default for any other code, a call log
(tests count billed calls), and faults scripted for the next call:
unavailable (from the test network), a 5xx, a timeout after answering,
unknown code, an undecodable body, a specific-duty formula, and a
preference note.

**The staff page**: the order check shows each rate's source (our row or
the vendor's answer, with the comparison), the lookup's state, and the
button; a table of agreement per country (answers compared, matched,
differed, our table had none), and a list of each answer (country,
origin, HS code, charge, what the vendor said, our row and its line),
differences first.

**Fuzzer** (a Hypothesis state machine): request a combination (also one
already asked), run a delivery, the worker catches up, a crash after
`prepare` with or without the call, each fault, a refused connection, a
redelivered fact. After every action: one `duty_rate_needed` per lookup;
every answered lookup has exactly one answer per charge; the vendor was
called no more often than we counted; and every counted call beyond the
first per lookup has a cause (a fault, a refused connection or a crash);
a lookup ends unanswered only after a fault that ends it. After draining
with faults off: no lookup is `requested`.

Every open and conditional country is listed as `vendor` with `zonos`;
the seed gives each vendor the market data names an account at the fake.
The fuzzer starts with one combination asked, keeps at most two faults
and two refusals waiting, and bounds its checks by what the vendor and
the network actually took, so a fault scripted but never used cannot
hide a bug. `prepare`'s state check is a second guard no test can reach:
`complete` and finishing the delivery commit together, so `prepare` never
meets an answered lookup.

## 4. Prices and the quote (4c)

- Rate cards, zones, rates, surcharges and fuel loaded from
  `docs/markets/` into tables, effective-dated. `own_table` shipping price
  = zone × billed weight (volumetric per the service's divisor and step)
  + fuel + the surcharges that apply. The `carrier_api` source calls
  `quote_rate` on a fake carrier; the carrier port grows the rest of its
  operations in slice 6.
- Fee rules as plan-architecture §8: `psp_fee` (estimate per buyer country
  and currency), `fx`, `mor_margin`; per merchant overriding the default,
  append-only, exact fractions, named rounding.
- `build_quote(...) -> Quote | QuoteRefusal`, pure. Refusals a closed
  set: country closed (reason), line restricted, no service for the lane,
  buyer field missing, parcel over limits. Fuzzed: for any lines and any
  country, the parts sum to the total in minor units, the merchant gets
  exactly its price, and no float or `Decimal` appears.

### 4c: decided 18-Nov-2026

4c is built in two steps: **4c-1** our own tables, the fee rules,
`build_quote` and the full breakdown on the staff page; **4c-2** the
`carrier_api` source, a fake carrier's `quote_rate` asked by a worker and
stored with its expiry, like the duty lookups.

1. **One option per routed service, each with its full total**: freight
   changes the duty, the tax and the duty-paid fee, so totals differ by
   more than shipping (plan-logistics §2 already shows every service on
   the lane). Cheapest first; the buyer picks.
2. **What is offered** is `shipping_routes.csv`: a rate card per country
   or group. Until a carrier contract exists the routes are DHL Express
   Worldwide and FedEx International Priority from their published cards
   and FedEx International Connect Plus from its promotional card, all
   `placeholder`, so prod refuses to quote on them (§6).
3. **An unconfirmed cost leaves the option out** (§6): a service,
   card, zone, weight break or charged surcharge that is not verified or
   secondary, a price given as a range, or a category the service does
   not confirm it carries. The reason is shown on the staff page; with no
   option left the refusal is `no_service_for_lane`. Today FedEx drops out
   whenever something is paid at the border (its bill-duties fee awaits an
   answer) and Connect Plus everywhere (its zones are estimates).
4. **Surcharges on the buyer**: those that always apply, and the
   duty-paid fee when something is paid at the border. Those only on an
   event (remote area, oversize), optional ones and passed-through duties
   are not charged; a person handles them at invoice import (slice 6).
5. **Fuel past its week is still used and flagged** on the staff page:
   the difference is small and ours. A weekly update, or `carrier_api`,
   fixes it properly.
6. **Fees are a percentage of the merchant's price** (plus an optional
   fixed amount), never of the buyer's total, which would be circular:
   the fee raises the goods value, which raises the tax. The defaults are
   in `fees.csv` (`psp_fee` 3.5% and `mor_margin` 5% as placeholders,
   `fx` 0%, §6); fee overrides per merchant come with the admin pages
   that enter them.
7. **Currency and rounding**: the buyer pays in the country's currency.
   The merchant's price is converted rounding up (the merchant is never
   short), shipping parts round up (we never under-collect the carrier),
   fees, duties and taxes round half to even. Each part keeps its exact
   value, its rounded amount and its mode; the ledger books the rounded
   amounts and the remainder stays on the part (`plan-payments.md` §5).

**Code**: `mor.quote.shipping`, `mor.quote.fees`, `mor.quote.build`. One
parcel per order for now (packed weight and box). Freight from our tables
matches `scripts/markets.py`'s all-in freight exactly for its reference
parcels. Hypothesis properties, on synthetic data and on the research
files: parts sum to each total, every part is its exact value rounded by
its named mode, the merchant is never short, duties and taxes are never
negative, the duty-paid fee only with something at the border, cheapest
first. Not built yet: a product gate per country and category (no data
for it beyond the services' restrictions) and several parcels per order.

### 4c-2: live carrier prices (decided 19-Nov-2026)

**What a real carrier gives** (read 19-Nov-2026). DHL Express's rates call
(`/mydhlapi/rates`) and FedEx's (`rate/v1/rates/quotes`) take origin,
destination, a parcel's weight and box and the service. They return a
total with its parts: freight, fuel and each surcharge that applies.
FedEx's docs say every quote is an estimate that may differ from the
bill. Neither documents how long a price stays valid, and fuel changes
weekly. A rate call costs nothing per call on a carrier account, but we
count calls anyway, as for duty vendors.

**The port** (`src/mor/carriers/`, the carrier port of plan-architecture
§7, which grows its other operations in slice 6): `quote_rate(account,
PriceQuestion) -> CarrierPrice | NotServed | Unavailable | OutcomeUnknown
| UndecodableBody`. `PriceQuestion` is the service code, origin,
destination and the exact parcel (grams and millimetres). We do not send
the goods value: we price duty and tax ourselves. `CarrierPrice` is the
total and its parts (kind `freight | fuel | surcharge`, name, amount), all
in one currency, and a `valid_until` when the carrier gives one. Decoding
checks that the parts add up to the total. When they don't, the answer is
`UndecodableBody`. `NotServed` is the carrier saying it does not carry
this lane or parcel. One registry picks the module by `carrier_provider`
with an exhaustive `match`, and refuses an account kind the environment
does not allow (`mor.provider_accounts`).

**Which price a route uses**: `price_source` gains `carrier_api`. A route
names a service, plus a rate card only when the source is `own_table`.
One source per service and lane: a buyer never sees the same service
twice.

The account is found from the service's carrier
(`shipping_service.carrier`, e.g. `dhl_express`). Each environment's
database holds one `carrier_account` per carrier: the real API in prod,
the fake elsewhere, as with duty vendors. The CSV is the same everywhere.

**What stays ours for a live route**: whether the service, route and
category are confirmed, the service's maximum weight (checked before
asking), transit days, and the duty-paid fee from `surcharges.csv`. The
fee needs the duties, which we compute after the price, so the carrier is
never asked for it. Everything the carrier returns is charged, since it is
what the carrier says it will bill. The parts become `ShipPart`s,
converted at the load's FX rates and rounded up, as from our own tables.
Customs counts their sum as the freight.

**Tables** (0012):

- `carrier_account` (id, carrier unique, provider: `carrier_provider`
  enum, `fake` for now; base_url, timeout_ms, price_valid_for_s,
  price_expiry). Slice 6 adds credentials and the billing currency.
- `shipping_price_lookup` (id, carrier, service, origin, destination,
  weight_g, length_mm, width_mm, height_mm, state `requested | answered |
  not_served | needs_person`, calls, detail, finished_at,
  carrier_valid_until, timestamps).
- `shipping_price_part` (lookup_id, line_no, kind, name, amount_minor,
  currency), append-only. The total and the carrier's currency are on the
  lookup. So is our own card's price for the same service and lane at the
  time, when one is in force (load_id and amount, or none), so a person
  can see how far the two differ.

**The guard: one open request at a time, not one for good.** A price
depends on the parcel and expires, so it cannot be "asked once" like a
duty rate. A partial unique index on (carrier, service, origin,
destination, the parcel) `WHERE state = 'requested'` allows one
outstanding request per combination. `request_shipping_price` returns the
newest answered lookup still valid at `now`, or else inserts a
`requested` row with `ON CONFLICT DO NOTHING`. It appends
`shipping_price_needed` only when it inserted, and otherwise returns the
open lookup. Two buyers after an expiry therefore cause one call. A race
in which a request inserts just after another was answered costs one
extra read, and nothing else.

**How long a price is used is a setting per account, not stored on the
answer** (decided 19-Nov-2026: reusing a price the carrier calls expired
may be fine for some carriers, so it must be a switch, not impossible).
The lookup stores what happened: `finished_at` and the carrier's
`carrier_valid_until` (or none). `carrier_account` holds the rule:
`price_valid_for_s` (24 hours by default) and `price_expiry`, a
`price_expiry` enum:

- `carrier_first`: usable until the earlier of the carrier's own validity
  and `finished_at + price_valid_for_s` (the default).
- `own_period`: usable until `finished_at + price_valid_for_s`, whatever
  the carrier said.

A lookup that ends `not_served` or `needs_person` is held for the same
period, or every request in the meantime would ask again. One pure
function, `usable_until(lookup, account)`, decides, applied where a
lookup is read (`read_price`); `build_quote` compares it with
`QuoteRequest.now`. Switching an account's
rule therefore applies at once to prices already stored, and no stored row
changes. This is time as a promise: it is compared at `now` and never
takes over a request. A price used after the carrier's own validity can
differ from the bill. That difference is ours, and invoice import shows it
like any other (slice 6).

**Messages** (aggregate `shipping_price_lookup`, version 1):
`shipping_price_needed` goes to the effect consumer `shipping_price_ask`.
Its `prepare`, `call` and `complete` steps work as for duty lookups: lock,
act only on `requested`, count the call before it is made, and store the
answer under the lock. `complete` then appends `shipping_price_found`,
`shipping_price_not_served` or `shipping_price_needs_person`.
`Unavailable` and `OutcomeUnknown` are retried with the worker's backoff.
The quote flow resumes a waiting quote on them (§5).

**`build_quote`** stays pure. It gains the stored prices,
`dict[PriceQuestion, PriceLookup]`, and `QuoteRequest` gains `now`
(a timestamp, for expiry) beside `on`. For a live route:

- a valid answer becomes an `Offer`;
- a missing or expired one adds to `InputsMissing` (`price_asks`, or
  `price_waiting` for a request already open);
- `not_served` or `needs_person` leaves the option out, with
  `NotOffered` reasons `carrier_not_served` and `carrier_needs_person`.

`InputsMissing` gathers what every option lacks, duty rates and carrier
prices alike, instead of stopping at the first. A carrier's price is
converted part by part and rounded up, as from our own tables. A
duty-paid fee with fuel on it uses our fuel row for the service; without
a usable one the option is left out (`fuel_unknown`).

An option is keyed by its route (the card, or `live:<service>`), not by
its card alone.

**The fake carrier** at `/_fake/carrier/`, in `fake_vendor`: per
(service, destination) a base price, a price per kg of billed weight and a
fuel percentage; a validity it states (or none); a call log; and faults
scripted for the next call: a 5xx, a timeout after answering, not served,
an undecodable body, parts that don't sum to the total, an answer in
another currency, and a validity already past. The seed gives each
carrier named on a live route an account at the fake.

**The staff page**: a live option shows "carrier price, asked
01-Jan-2027 14:35 UTC, valid until …" and our card's price beside it when
one exists. Without a usable price it shows a greyed row ("waiting for
the carrier", "not served") with an "Ask the carrier" POST (outside prod,
same-origin check), like "Ask the vendor", or says that the carrier has
no account in this environment. Our card's price for comparison comes
from the first card of the service that can price the parcel, whether
routed or not, with no goods value. The answers page has a table of live
prices against our cards, differences first.

**Tests and fuzzer**:

- Pure: a live answer's parts, once converted and rounded, sum to the
  option's shipping. A price is never used past `usable_until`; after it,
  `build_quote` returns `InputsMissing`.
- A Hypothesis state machine like the duty one, plus a clock action that
  passes a price's `usable_until`, and a switch of the account's
  `price_expiry`. After every action:
  - at most one `requested` lookup per combination;
  - one `shipping_price_needed` per lookup;
  - every answered lookup's parts sum to its total;
  - the carrier was called no more often than we counted;
  - an extra call always has a cause.
- After draining with faults off, nothing is `requested`. Planted bugs to
  catch: a second request while one is open, using an expired answer, a
  call counted after it is made, and parts that don't sum accepted.

**Decided 19-Nov-2026**: DHL Express Worldwide goes to OCEANIA through
`carrier_api`, marked placeholder, so the test setup and the staff page
exercise it (no research country there quotes yet: AU's rules are not
confirmed, NZ has no import tax recorded); every other lane stays on our
cards. A live price is stored and shown beside our card's price, with no
`shipping_price_disagreed` fact until something consumes it.

## 5. Session, API and pages (4d)

Built in two steps: **4d-1** merchants, API keys and the API with
idempotency (sessions created and read, estimates priced); **4d-2** the
stored quote, its waiting flow, the buyer pages and the staff view of
sessions and quotes. The quote flow is `plan-architecture.md` §8: when
every input is stored the handler quotes in its transaction; otherwise
`quote_requested`, workers fetch what is missing, and the page waits
until `quote_made` or `quote_refused`. No handler calls a vendor.

### 4d-1: merchants, API keys, sessions and estimates (decided 19-Nov-2026)

Applies `plan-architecture.md` §4 and §8.

**Merchant.** `id` (uuid7), `legal_name`, `display_name` (what buyers
see), `country` (VN for now), `daily_vendor_asks` (below), `created_at`,
and the KYB state and settings of `plan-payments.md` §6. Created with
`python -m mor.merchants create`, which prints the id; we create every
merchant ourselves until the dashboard exists.

**API keys.**
- Form: `sk_<env>_<id>_<secret>`. `<env>` is `dev`, `test` or `live`,
  from `ENVIRONMENT`. `<id>` is a short public id used to look the key up.
  `<secret>` is 32 random bytes in base32.
- Stored: `api_key(id, merchant_id, env, secret_sha256, state, created_at,
  revoked_at)`, with `state` one of `active` and `revoked`. A plain SHA-256
  suffices because the secret is random and long; a slow password hash
  only matters for secrets people choose. The comparison is constant-time.
- `python -m mor.merchants key <merchant>` creates one and prints it
  once. `... revoke <key id>` revokes it. Scopes and rotation with an
  overlap come with the dashboard.
- Requests send `Authorization: Bearer sk_…`. A key from another
  environment, an unknown key or a revoked one gets 401
  `authentication_failed`, with no detail on which.
- The actor recorded for an API change is `merchant_api_key` with the key
  id; that kind already exists in `actor_kind`.

**Idempotency** (every state-changing POST: today only
`checkout_sessions`).
- Table `api_idempotency(merchant_id, key, request_sha256, status,
  response, created_at)`, with primary key `(merchant_id, key)`. Rows are
  kept for good.
- The handler inserts the row first, in the same transaction as the work,
  then stores the response on it before committing. A concurrent request
  with the same key blocks on the unique index until the first commits.
  It then sees the conflict and replays the stored response, or returns
  409 `idempotency_key_reused` if the request differs. A crash rolls back
  the row and the work together, so no "in progress" state is ever left
  behind, and no clock is involved.
- `request_sha256` hashes the decoded request re-encoded by msgspec (fields
  in a fixed order), so whitespace and key order in the merchant's JSON
  don't matter.
- A request refused before any change (bad JSON, a missing field, an
  unknown category) stores nothing, so the merchant can fix it and send it
  again with the same key.

**Errors.** One shape, `{"error": {"type", "code", "message", "param"}}`.
- `type` is a closed set: `invalid_request`, `authentication`,
  `idempotency`, `not_found`, `quote_refused`, `pending`,
  `rate_limited`, `unavailable`. A refusal that is ours to fix (no market
  data, no fee rule, no exchange rate, no account for a vendor, a
  placeholder in prod) is `unavailable` with status 503, not a 422
  `quote_refused`, which the merchant's request caused.
- `code` is a closed set as well, documented with the API.
- msgspec's decode errors map onto `invalid_request` and name the field
  (`param`).
- Every machine-read time is RFC 3339 in UTC with the offset written, to
  the second (`2026-11-18T09:30:00Z`), through `mor.dates.format_instant`.

**Ids in the API.** The prefix plus the uuid7 in hex: `cs_…` for sessions
and `mer_…` for merchants. The database keeps the uuid. The ids sort by
time and are unguessable enough for a bearer link (uuid7 has 74 random
bits).

**`POST /v1/checkout_sessions`.**
- Body: `lines`, `parcel`, `merchant_reference?`, `return_url`,
  `cancel_url`, `after_payment?` (`plan-payments.md` §6).
- Each line: `name`, `quantity`, `unit_price {amount_minor, currency}`,
  `category`, `merchant_sku?`, and `customs {description, origin_country,
  weight_g, hs_code?}`.
- `tax_behavior` is not accepted: v1 is exclusive only.
- Lines may be priced in different currencies; the quote already converts
  each one.
- Stored: a `checkout_session` row (`state` = `open`, `expires_at` 24 h
  after creation, the parcel, the URLs, the merchant reference) and one
  `checkout_session_line` row per line, a snapshot that is never changed.
- The fact `checkout_session_created` v1 goes into the outbox, with
  `checkout_session_expiry_due` scheduled at `expires_at`.
- Response: 201 `{id, state, url, expires_at, lines, …}`; `url` is
  `/c/{id}`, the buyer's page.

**`GET /v1/checkout_sessions/{id}`** returns the same shape, for that
merchant's sessions only. Another merchant's id gets 404, as if it did
not exist.

**`POST /v1/estimates`.**
- Body: the same lines and parcel plus `country`. Response: the quote,
  without storing it:
  - the currency;
  - one option per service (service, tier, transit days, total);
  - each option's build-up: the merchant's price, each fee component,
    shipping, and duties and taxes with who collects each;
  - the services not offered, with their reasons.
- A refusal (country closed, no service for the lane, a line refused) is
  422 `quote_refused` with the reason's code.
- When an input is missing (a duty rate or a carrier price not held), the
  handler asks for it as the staff page does: outbox facts, deduplicated by
  the existing guards. It answers 503 `estimate_pending` with
  `Retry-After: 2`. The merchant repeats the call; no handler calls a
  vendor.
- Placeholder fee rules: in prod an estimate that used one is refused
  (503 `pricing_unavailable`); elsewhere it is answered and lists them.
  A service whose route is a placeholder is left out in prod, listed as
  not offered.

**Where it lives.**
- `src/mor/merchants/` holds the records, the store, the keys and the CLI;
  the public id helpers live in `mor.public_ids`, so the CLI imports
  nothing from `web/`.
- `src/mor/web/api.py` and `api_wire.py` hold the decoding at the edge,
  the errors, the idempotency and the handlers.
- The routes go in `main.py` in every environment: the API is the product,
  and each environment has its own keys.
- Seed: a "Sandbox Shop" merchant if there is none, and one key printed
  the first time.
- Tables: `merchant`, `api_key`, `api_idempotency`, `checkout_session`,
  `checkout_session_line`, `merchant_vendor_asks` (a merchant's count per
  UTC day).

**Tests and fuzzing.**
- Requests in, responses and rows out:
  - every auth failure;
  - a session created and read;
  - another merchant's session not found;
  - each refusal and each pending case;
  - placeholders refused in prod.
- Idempotency properties:
  - the same key with the same body gives an identical response and one
    session;
  - any change to the body with the same key gives 409;
  - a refused request leaves the key free.
- A real-concurrency test: two simultaneous POSTs with one key make one
  session.
- Properties for the decoder:
  - any bytes decode to a request or an `invalid_request` error, never an
    exception;
  - any estimate a valid request returns sums: each option's parts equal
    its total.
- The new property files join a target in `deploy/soak.sh`, as
  `tests/test_soak_targets.py` requires.

**Decided 19-Nov-2026:**

1. **The parcel is required** on every session and estimate: prices
   depend on the volumetric weight, and a box we guessed would misprice.
   The line's `weight_g` stays required for customs. Lines will also
   come by reference (`plan-merchant-surfaces.md` §3): the line shape
   leaves room for `{product_id, quantity}` as a later, additive v1
   change, and a product can carry its own packaging, so an order of one
   product will need no parcel.
2. **Category required; classification is ours.** Correct HS
   classification is the importer of record's legal responsibility, and
   neither DDP nor a broker moves it off them; as merchant of record that
   is our side (checked 19-Nov-2026 in trade guidance, not in law: which
   party is importer of record per country is an adviser question). So a
   merchant's HS code is a hint: each line requires a category from our
   closed list; a merchant's `hs_code` is stored as the merchant's and
   used for now; our own classification per `merchant_sku`, by a vendor
   or a person, comes later, stored once per SKU. A line with no HS code
   is priced where our rows need none; a country whose duty mode needs
   the code refuses it with 422 `classification_needed`.
3. **Estimates take no `Idempotency-Key`** (they change nothing but
   deduplicated asks) and answer `estimate_pending` with `Retry-After`
   while inputs are fetched. Two limits:
   - **Vendor asks, which cost money**: each merchant has a daily cap
     (`merchant.daily_vendor_asks`, default 100) on asks that are new.
     Asks the existing guards deduplicate are not new, so they don't
     count. The count is a row per merchant and UTC day, read and raised
     under its row lock in the estimate's transaction, and raised only
     when an ask inserted a new lookup. Over the cap the ask is not made,
     and the answer is 429 `rate_limited` with `Retry-After` set to the
     next UTC midnight. The day is a business rule compared under the
     lock, not a lease.
   - **Plain request volume**: a limit per IP address and per key in
     nginx, set in the deploy slice. In-process counters would break the
     no-clocks, no-memory rule and would not survive several processes.

Six planted bugs were each caught by a test, including a refusal after
the key's claim not rolled back: psycopg commits on a clean exit, which
would have committed a key with no response.

### 4d-2: the stored quote, its waiting flow and the buyer pages (decided 19-Nov-2026)

Applies `plan-architecture.md` §2 (stage 2), §4 and §8 ("Waiting").

**The session as a state machine.** `checkout_session` is the aggregate.
Every change takes its row lock, and facts carry the steps.

```
open ──address──▶ quoting ──inputs held──▶ quoted ──pay──▶ paying ──▶ completed
  ▲                  │  └──refused──▶ refused
  └─── a new address from any of these makes a new quote ───┘
any state but completed ──expiry due, under the lock──▶ expired
```

- `quoting`: the address is stored and a quote is wanted, but an input
  is missing. The asks are made and the waits recorded.
- `quoted` and `refused`: they point at the session's current quote or
  refusal.
- `expired`: the buyer page sends the buyer to `cancel_url`, and the API
  shows `expired`.
- `paying` and `completed` are `plan-payments.md` §4.

**The buyer's address** is a new row, `checkout_address`, one per address
given. They are never changed, and the session points at the newest.
- Fields: name, email, phone, address lines, city, region, postcode,
  country (below).
- This is personal data. It is never logged, only synthetic in tests and
  the seed, and kept only in the database. Retention and data residency
  are compliance touchpoints for an adviser, not answered here.
- A changed address makes a new quote; the old one stays as it was
  (immutable).

**The stored quote** (plan-architecture §8, "the quote stores every
input's source and version").
- Tables:
  - `quote`: session, address, `market_load_id`, currency, made at and
    `valid_until` (30 minutes, §6), placeholders, and state `made` or
    `refused`;
  - `quote_option`: key, service, tier, transit and total per option;
  - `quote_part`: kind, line, exact value as numerator and denominator,
    amount, rounding mode, collector and source per part;
  - `quote_input`: each duty lookup and carrier price lookup used, by id.
- Each table has real columns, no serialized blob, and each is
  append-only by trigger.
- A refusal is stored as the quote row with `state = refused` and its
  code and message, the same codes as the API's.
- `build_quote` stays pure. Storing it is a function from `Quote` to rows,
  and a property checks that a stored quote read back equals what was
  built.

**The flow** (no handler calls a vendor):
1. `POST /c/{session}/address`. The handler locks the session, stores the
   address and builds with the held inputs, all in one transaction:
   - a quote: store it, state `quoted`, fact `quote_made`;
   - a refusal: store it, state `refused`, fact `quote_refused`;
   - missing inputs: ask for them as estimates do, record one
     `quote_wait (quote_attempt, lookup_id)` row per lookup waited on,
     state `quoting`, fact `quote_requested`.

   It answers 303 to `/c/{session}`.
2. When a lookup finishes, its fact (`duty_rate_found`,
   `…_unknown_code`, `…_needs_person`, `shipping_price_found`,
   `…_not_served`, `…_needs_person`) goes to a new local consumer,
   `quote_inputs_arrived`. It belongs to the lookup's aggregate and locks
   no session, as "one step locks one aggregate" requires. It appends
   `quote_inputs_arrived` for each session waiting on that lookup.
3. A local consumer of that fact, `quote_build`, locks the session. If it
   is still `quoting` for the same address, it builds again: a quote, a
   refusal, or new asks and waits (a second round is possible, say a
   duty rate after a carrier price). Otherwise it does nothing, so a
   duplicate or late fact is harmless.
4. While the session is `quoting`, the page says it is working out
   shipping and duties and waits as every waiting page does
   (`plan-payments.md` §4).

`still_open` reads each lookup waited on with `FOR SHARE`: a completion
that commits between the build and the wait would otherwise announce its
outcome to no one, and the buyer would wait for ever
(`tests/checkout/test_quote_concurrency.py` fails without it).

The asks a buyer starts don't count against the merchant's daily cap,
or a buyer could use up a merchant's cap and block real checkouts. They
are bounded per session instead: at most 10 addresses per session, so a
buyer can try a few while a script can't sweep countries.

**Quote validity and session expiry.** Time only triggers; the decision
is taken under the lock.
- A quote is valid until `valid_until`. Past it the page shows the old
  price as expired, with a "Get the current price" button (a POST that
  quotes the same address again). The pay step compares
  `valid_until` under the lock.
- Expiry: creating a session also appends `checkout_session_expiry_due`
  with `deliver_at = expires_at`. Its consumer locks the session and
  expires it if it is not completed and `now >= expires_at` (fact
  `checkout_session_expired`); otherwise it does nothing.

**The buyer pages.** Server-rendered with htpy, English first, mobile
first, the merchant's display name at the top, and the one line
"Sold and invoiced by <us> on behalf of <merchant>" (its wording needs
legal input; a placeholder until then).
- `GET /c/{session}` renders by state:
  - the cart without prices, with the address form: country first, from
    the countries open for every line's category;
  - waiting, refreshing itself;
  - the quote: one row per shipping option (name, delivery days, total;
    the cheapest chosen, others a plain link `?option=`), then the
    four lines `plan-architecture.md` §8 decided (items with our fee
    inside, shipping, duties and taxes, total), the time it is valid
    until (in the `01-Jan-2027 14:35 UTC` format), and the pay step;
  - refused: the reason in buyer words, with the address form to try
    another;
  - expired: a 303 to the merchant's `cancel_url`.
- POSTs use plain forms, the same-origin check, and no cookies: the
  session link is its own bearer token for that cart only. The referrer
  policy is `same-origin`: `no-referrer` makes browsers send
  `Origin: null` on the page's own POST, which the check refuses.
- Prod refuses a quote standing on a placeholder, as estimates do.

**The API.** `GET /v1/checkout_sessions/{id}` gains:
- the new states;
- `ship_to_country`;
- `quote`: id, `valid_until`, currency, and per option its key and
  total, the same shape as an estimate's options.

The full address is not returned here; the order's endpoint carries it
for fulfilment (`plan-payments.md` §6).

**The staff page** gains **Sessions** (`/staff/markets/sessions`):
- one row per session: merchant, state, country, the quote's total, the
  waits still open;
- a session's page with its quotes, each with the same build-up the
  Quote page shows.

**New messages:** `quote_requested`, `quote_made`, `quote_refused`,
`quote_inputs_arrived`, `checkout_session_expiry_due` and
`checkout_session_expired`. Consumers: `quote_inputs_arrived` on the six
lookup outcome facts, `quote_build`, and `checkout_session_expire`.

**Tests and fuzzing.**
- Pages through their edge:
  - each state renders;
  - an address in, the rows and the page out;
  - a cross-site POST refused;
  - an expired session redirects.
- A Hypothesis state machine for the quote flow, against the test
  database and the fakes.
  - Actions:
    - the buyer posts an address, twice (a double submit), or a new one;
    - the worker drains;
    - a vendor or the carrier answers, fails, or times out after doing
      the work;
    - a crash between steps;
    - the clock passes `valid_until` or `expires_at`;
    - a refresh is asked for.
  - After every action, the session is in a state the model allows.
  - After draining with faults off:
    - no session is still `quoting`;
    - every quoted session points at a stored quote whose parts sum to
      each option's total;
    - no lookup was asked twice.
- The new fuzzer joins a soak target.

**The address form** (decided 19-Nov-2026): name, email, phone, two
address lines, city, region, postcode and country, all at once, so the
buyer enters them once. Fruggals was the model
(`~/Desktop/fruggals/src/addresses.py`): a pure parser, the phone's dial
code kept apart from its number, and a postcode checked strictly where
its format is fixed. Here:

- **Rules per country, as data.** fruggals relaxes every country but
  the Netherlands. Here a table per country says whether a postcode is
  used and its pattern, whether a region (province or state) is
  required, and the dial code. SG has 6 digits; MY, TH and ID have 5;
  PH has 4. VN's postcode is optional. A country that opens gets its
  row; one without a row cannot be chosen.
- **Free address lines.** fruggals splits "street and house number" for
  the EU, which doesn't fit Thai, Vietnamese or Indonesian addresses
  (soi, ward, kelurahan). Two free lines, plus the city or district and
  the region.
- **Email and phone are required.** DHL's shipping guide asks for the
  receiver's contact number, email and full address with postcode. The
  order page's link also goes to the email. Checked
  19-Nov-2026 in DHL's guide and its MyDHL field guidelines, not in its
  API reference: confirm when the carrier slice books labels.
- **No address book**: buyers have no accounts. One address per quote.
- **A tax id per country later.** DHL asks for a receiver tax id for
  sales into many countries (KR's customs code, for one). It becomes a
  per-country field when such a country opens.
- **No address-checking service yet.** Google's Address Validation API
  covers SG and MY, but not TH, ID, PH or VN (its coverage page, read
  19-Nov-2026). It is billed per call, and no one service covers the
  region. So 4d-2 checks the format per country only. A service can come
  later behind a port with a mode per country, as duty rates have, once
  failed deliveries show it is worth paying for.

Open:
- SG's quotes carry no GST: `registrations.csv` says we are not
  registered there yet, so the tax path charges none (correct for the
  data). Selling into SG before registering is a compliance decision, not
  a code one.
- The "Sold and invoiced by" line names `[our company]` until the
  company and the legal wording exist.

## 5b. The staff page

A staff page, `/staff/markets`, shows what each step decides, and grows
(decided 17-Nov-2026): the loads and each country's answer (open, or
closed with the reason, and the rates in force), the tax paths and the
duty decision for an order, the full price breakdown, and the sessions
and quotes made. It is a staff tool, not a sandbox one: it becomes the
admin page that edits market data, and is read-only until then. Three
pages under one menu. **Quote** (`/staff/markets`) is the order form
with a parcel, then every shipping option on one line of a table
(type, delivery, warning tags, total), then how the chosen option's price
comes to be: a bar of each group's share, then the merchant's price, our
fees, shipping (with the carrier's duty-paid fee) and duties and taxes,
each with its subtotal, and each part with what it is a share of, where
it came from and how it was rounded. The cheapest is chosen until another
is picked (`option=`, a plain link); cards that cannot carry the order
are greyed rows of the same table, with why. Below, always shown, "Duties
and taxes": a summary (what is charged at the border and what that rules
out, who sets the rates, any rate our own data disputes), one row per
charge (collected where, rate, who says so, what our own data says), and
the data rows that decided it.
**Vendor answers** (`/staff/markets/answers`) and **Market data**
(`/staff/markets/data`) hold the rest. Scripts improve it (the quote
follows the form as you type; tables filter), and it works without them.
Amounts show in USD and EUR beside the quote's own currency, converted at
the load's exchange rates for reading only. **Sessions**
(`/staff/markets/sessions`) lists sessions and their quotes (§5).

Until staff sign-in exists it is mounted only outside prod, reached on
loopback like the sandbox page. Staff sign-in (staff accounts, password
plus a second factor, sessions with a SameSite cookie, the cross-site
check on every POST, the person recorded as actor) is a slice of its own,
before the first prod deploy (decided 17-Nov-2026); the page is mounted in
prod behind it, unchanged.

## 6. Decisions of 17-Nov-2026

1. **A quote relies on `verified` and `secondary` values only.** A
   country whose quoting values include `unverified`, `estimate`,
   `to_fetch` or `awaiting_answer` is closed at checkout ("rules not
   confirmed yet"), failing closed rather than charging a buyer on a
   guess; updates must be painless (§2).

2. **Who edits market data**: the CSVs for now; then the database
   becomes the single place to edit, through an admin page that writes
   new rows, and the CSV and spreadsheet views are generated from it. The
   loader then runs once, as the import.

3. **Product category on each line**: the merchant tells us the
   category (from our closed list: product gate, tax rate, border rule)
   and the HS code (duty rate); classification stays ours (§5). Where a
   merchant cannot, staff classify the merchant's stored product by
   hand, recorded as a staff action; nothing is guessed automatically.

4. **FX**: the buyer pays in their currency, so every quote needs a
   rate. For now an `fx_rate` table filled by the loader (ECB reference
   rates where they exist, the State Bank of Vietnam's for VND), behind
   the same switchable-source shape, and the `fx` fee component at 0%
   until a margin is decided. Which source in prod is a PSP question.

5. **Placeholder numbers**: the default `mor_margin` percentage and the
   `psp_fee` estimates per country are placeholders until the PSP
   contract and pricing exist: data marked placeholder, and prod refuses
   to quote while a placeholder row is in force.

6. **A quote holds for 30 minutes**, after which the page makes a fresh
   quote and shows if the total changed.

## 7. Rules this applies

Money as integers with their currency; exact fractions for rates; named
rounding with remainders booked. Effective-dated, append-only rule rows,
and every quote stores each input's source and version. Switchable sources
per lane, picked by routing data, fakes for every vendor mode. Locks and
unique keys, not clocks; time only starts work (a refresh, an expiry
check). No handler calls a vendor. Typed records at every edge.

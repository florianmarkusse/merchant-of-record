# Slice 4: markets, the tax path, prices and the quote

Status: confirmed 17-Nov-2026 (answers in §6). Step 4a built 17-Nov-2026
(`mor.markets`, `0008_markets.sql`); see §2 "Built". Implements
plan-architecture.md §8 and §14 step 4, with the tax path rule of
plan-logistics.md §4 and the checkout records of plan-merchant-surfaces.md
§3–5. Where those differ, plan-architecture wins. Payment is slice 5: this
slice ends at a quote the buyer could pay.

## 1. Steps

Built and confirmed one at a time, each with its checks, tests and fuzzing:

| Step | What | Ends with |
|---|---|---|
| 4a | Markets in the database: countries and their rules, our registrations, FX rates, loaded from `docs/markets/` | `uv run python -m mor.markets.load` fills effective-dated rows; a country can be asked "open? on what rules?" |
| 4b | The tax path rule, the seller-collected tax module (UK first), duty rates with switchable sources and a fake duty vendor | `tax_paths()` and duty rates for any (HS code, origin, destination) |
| 4c | Shipping prices (own rate tables, and a carrier rate call through a fake courier), fee rules, and `build_quote` | the pure quote, fuzzed: parts sum to the total, every division rounded by a named mode, every remainder booked |
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
- Only `verified` and `secondary` values are used (Q1).
- **Built 17-Nov-2026** (`src/mor/markets/`): `source` parses the files
  into typed records or errors naming file, line and column; `store`
  writes and reads loads under a transaction-level lock; `changes`
  writes the report (only the fields that changed, as people read them);
  `rules.country_rules` answers open or closed (not listed, its market
  status with the reason, or a rate weaker than secondary) and lists the
  rates in force per charge, most specific first. A newer row with the
  same place, category, charge, collector and band replaces the older
  from its date, whether its rate or its limit changed: that is how a law
  change is entered. `EU` and other groups are told apart from countries
  by the countries file, not by their shape. Seven currencies were added
  (GBP, AUD, NZD, CAD, CHF, JPY, KRW) for limits and FX rates. The ECB
  rates moved from `scripts/markets.py` into `fx_rates.csv`, which both
  read; the generated page is unchanged.
- **No import tax row means closed, not tax-free** (decided 18-Nov-2026,
  following Q1: fail closed). An open country needs at least one
  `import_tax` row for the category; a country that truly charges none
  gets an explicit 0% row with its source. Seen on the staff page: CA and
  CH were "open" with no rates at all. With today's data, apparel can be
  quoted to SG and TH only; the rest are closed with the reason shown
  (rates missing, a rate not confirmed, or the market status). Missing
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

Decided 18-Nov-2026. Built in two parts: 4b-1 the rule, the rates per HS
code and the staff form (pure logic and data); 4b-2 the duty vendor flow
(fake vendor, `duty_rate_needed`, the worker that asks once, its fuzzer).

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
  own rows, a vendor, or both, by a mode per country: see 4b-2 below,
  which replaces the first idea here (a plain `own_table` or `vendor`
  switch).
- **VND** (decided 18-Nov-2026): the State Bank of Vietnam's daily
  reference rate replaces the research estimate, as the ECB rates do for
  other currencies (25,600 per US$ on 10-Nov-2026, as reported by
  VietnamPlus: `secondary`, so usable).
- The staff page gains a form: country, category, goods value and
  currency, freight, HS code, origin and date; it shows both paths, the
  row that decided each and why, and the rates found or missing.

**4b-1 built 18-Nov-2026** (`src/mor/tax/`, `mor.markets.fx`,
`0009_hs_rates.sql`, `docs/markets/hs_rates.csv`, `duty_sources.csv`):
`tax.paths.tax_paths` decides both paths; `tax.line_rates.line_rate` the
rate per HS code with agreements and conditions; `tax.explain` puts a
decision into words for the staff page's order check. Exchange rates are
found through the shortest chain of confirmed rates (VND to SGD runs
VND-USD-EUR-SGD), exact. A planted bug (the limit itself outside its
`up_to` band) slipped past a test that checked only "not due" and not
why; the tests now check the reason, and the limit is pinned as an
example. HS rates are seeded for SG apparel (0%) and TH HS 6109 (30%, and
ATIGA 0% marked unverified, so reported but not applied).

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
- `duty_source` changes shape: `mode` and `vendor_name` replace `kind`
  (the table is empty, so the migration alters it).

**Messages** (aggregate `duty_lookup`, version 1):

- `duty_rate_needed`: to the effect consumer `duty_rate_ask`.
  `prepare` locks the lookup, does nothing unless it is `requested`, and
  counts the call (committed before it is made, so every billed call is
  visible). `call` asks the vendor. `complete` stores the answer and our
  own table's rate beside it, under the lock, and appends:
- `duty_rate_found`, `duty_rate_unknown_code`, or `duty_rate_needs_person`
  (an undecodable answer); plus `duty_rate_disagreed` per charge where our
  own row says something else. No consumers yet: in 4d the quote flow
  resumes a waiting quote on them.
- Unavailable and OutcomeUnknown are retried with the worker's backoff.
  A lookup is a read, so asking again changes nothing at the vendor and
  needs no duplicate guard; the cost is one more counted call.

**Who asks**: `request_duty_rate`, which inserts the lookup with
`ON CONFLICT DO NOTHING` and appends `duty_rate_needed` only when it
inserted. For now from the staff page's order check (outside prod: an
"Ask the vendor" POST with the same-origin check); in 4d from the quote
flow, when a buyer opens a checkout for a combination not yet asked.

**The fake vendor** at `/_fake/duty/`, in the `fake_vendor` schema: rates
per (destination, HS prefix), a default for any other code, a call log
(tests count billed calls), and faults scripted for the next call:
unavailable (from the test network), a 5xx, a timeout after answering,
unknown code, an undecodable body, a specific-duty formula, and a
preference note.

**The staff page**: the order check shows each rate's source (our row or
the vendor's answer, with the comparison), the lookup's state, and the
button; a table of agreement per country (answers compared, matched,
differed, our table had none).

**Fuzzer** (a Hypothesis state machine): request a combination (also one
already asked), run a delivery, the worker catches up, a crash after
`prepare` with or without the call, each fault, a refused connection, a
redelivered fact. After every action: one `duty_rate_needed` per lookup;
every answered lookup has exactly one answer per charge; the vendor was
called no more often than we counted; and every counted call beyond the
first per lookup has a cause (a fault, a refused connection or a crash);
a lookup ends unanswered only after a fault that ends it. After draining
with faults off: no lookup is `requested`.

**4b-2 built 18-Nov-2026** (`src/mor/duties/`, `mor.fakes.duty`,
`0010_duty_vendors.sql`, fake `0005_duty.sql`, the modes in
`tax.line_rates`): every open and conditional country is listed as
`vendor` with `zonos`; the seed gives each vendor the market data names
an account at the fake. The fuzzer was checked by planting bugs: a second
request emitting `duty_rate_needed` again, a call counted only after it
was made, a lost answer ending the lookup as unknown, and a refused
connection ending it as needing a person. At first only the first was
caught at the dev budget: faults piled up unused, and the checks counted
faults scripted rather than used. The fuzzer now starts with one
combination asked, keeps at most two faults and two refusals waiting,
and bounds its checks by what the vendor and the network actually took;
all four are caught. Removing `prepare`'s state check is not caught, and
cannot be: `complete` and finishing the delivery commit together, so
`prepare` never meets an answered lookup; the check is a second guard.
Still to do (asked 18-Nov-2026): the staff page's agreement table shows
counts only; add a list of each answer (country, origin, HS code, charge,
what the vendor said, our row and its line), differences first, so a
person can see which rates differ and by how much. Done in 4c-1.

## 4. Prices and the quote (4c)

- Rate cards, zones, rates, surcharges and fuel loaded from
  `docs/markets/` into tables, effective-dated. `own_table` shipping price
  = zone × billed weight (volumetric per the service's divisor and step)
  + fuel + the surcharges that apply. The `carrier_api` source calls
  `quote_rate` on a fake courier; the courier port grows the rest of its
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
`carrier_api` source, a fake courier's `quote_rate` asked by a worker and
stored with its expiry, like the duty lookups.

1. **One option per routed service, each with its full total**: freight
   changes the duty, the tax and the duty-paid fee, so totals differ by
   more than shipping (plan-logistics §2 already shows every service on
   the lane). Cheapest first; the buyer picks.
2. **What is offered** is `shipping_routes.csv`: a rate card per country
   or group. Until a carrier contract exists the routes are DHL Express
   Worldwide and FedEx International Priority from their published cards
   and FedEx International Connect Plus from its promotional card, all
   `placeholder`, so prod refuses to quote on them (as Q5).
3. **An unconfirmed cost leaves the option out** (as Q1): a service,
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
   `fx` 0% as decided in Q4); per merchant overrides come in 4d.
7. **Currency and rounding**: the buyer pays in the country's currency.
   The merchant's price is converted rounding up (the merchant is never
   short), shipping parts round up (we never under-collect the carrier),
   fees, duties and taxes round half to even. Each part keeps its exact
   value: the difference is the remainder, booked when the order is paid
   (slice 5).

**4c-1 built 18-Nov-2026** (`mor.quote.shipping`, `mor.quote.fees`,
`mor.quote.build`, `0011_shipping.sql`): the shipping files, routes and
fee rules load with the market set. One parcel per order for now (packed
weight and box). Freight from our tables matches `scripts/markets.py`'s
all-in freight exactly for its XS, M and L scenarios into SG, TH, JP, DE
and US on both priced cards. The Hypothesis properties (parts sum to each
total, every part is its exact value rounded by its named mode, the
merchant is never short, duties and taxes are never negative, the
duty-paid fee only with something at the border, cheapest first) run on
the synthetic set and on the research files; five planted bugs (goods
rounded half to even, the duty-paid fee left out of the total, the fee
charged with nothing at the border, options unsorted, a part rounded by
another mode than it names) were all caught. Not built yet: a product
gate per country and category (no data for it beyond the services'
restrictions), several parcels per order, and prod's refusal of
placeholders, which belongs to the quote flow (4d).

### 4c-2: live carrier prices (decided 19-Nov-2026)

**What a real carrier gives** (read 19-Nov-2026). DHL Express's rates call
(`/mydhlapi/rates`) and FedEx's (`rate/v1/rates/quotes`) take origin,
destination, a parcel's weight and box and the service. They return a
total with its parts: freight, fuel and each surcharge that applies.
FedEx's docs say every quote is an estimate that may differ from the
bill. Neither documents how long a price stays valid, and fuel changes
weekly. A rate call costs nothing per call on a carrier account, but we
count calls anyway, as for duty vendors.

**The port** (`src/mor/couriers/`, the courier port of plan-architecture
§7, which grows its other operations in slice 6): `quote_rate(account,
PriceQuestion) -> CarrierPrice | NotServed | Unavailable | OutcomeUnknown
| UndecodableBody`. `PriceQuestion` is the service code, origin,
destination and the exact parcel (grams and millimetres). We do not send
the goods value: we price duty and tax ourselves. `CarrierPrice` is the
total and its parts (kind `freight | fuel | surcharge`, name, amount), all
in one currency, and a `valid_until` when the carrier gives one. Decoding
checks that the parts add up to the total. When they don't, the answer is
`UndecodableBody`. `NotServed` is the carrier saying it does not carry
this lane or parcel. One registry picks the module by `courier_provider`
with an exhaustive `match`, and refuses the fake in prod.

**Which price a route uses**: `price_source` gains `carrier_api`. A route
names a service, plus a rate card only when the source is `own_table`.
Migration 0012 adds `shipping_route.service`, fills it from the card on
existing rows, and lets `rate_card` be NULL only for `carrier_api`. The
fill needs the append-only trigger switched off for that one UPDATE. That
is safe because no prod database exists yet, and each local one gets the
same fill. One source per service and lane: a buyer never sees the same
service twice.

The account is found from the service's carrier
(`shipping_service.provider`, e.g. `dhl_express`). Each environment's
database holds one `courier_account` per carrier: the real API in prod,
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

- `courier_account` (id, carrier unique, provider: `courier_provider`
  enum, `fake` for now; base_url, timeout_ms, price_valid_for_s,
  price_expiry). Slice 6 adds credentials and the billing currency.
- `shipping_price_lookup` (id, carrier, service, origin, destination,
  weight_g, length_mm, width_mm, height_mm, state `requested | answered |
  not_served | needs_person`, calls, detail, answered_at,
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
The lookup stores what happened: `answered_at` and the carrier's
`carrier_valid_until` (or none). `courier_account` holds the rule:
`price_valid_for_s` (24 hours by default) and `price_expiry`, a
`price_expiry` enum:

- `carrier_first`: usable until the earlier of the carrier's own validity
  and `answered_at + price_valid_for_s` (the default).
- `own_period`: usable until `answered_at + price_valid_for_s`, whatever
  the carrier said.

One pure function, `usable_until(lookup, account)`, decides, and both
`request_shipping_price` and `build_quote` call it. Switching an account's
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
There are no consumers of the results yet: in 4d they resume a waiting
quote.

**`build_quote`** stays pure. It gains the stored prices,
`dict[PriceQuestion, PriceLookup]`, and `QuoteRequest` gains `now`
(a timestamp, for expiry) beside `on`. For a live route:

- a valid answer becomes an `Offer`;
- a missing or expired one adds to `InputsMissing` (`price_asks`, or
  `price_waiting` for a request already open);
- `not_served` or `needs_person` leaves the option out, with new
  `NotOffered` reasons `carrier_not_served` and `carrier_needs_person`.

An option is keyed by its route (the card, or `live:<service>`), not by
its card alone.

**The fake courier** at `/_fake/courier/`, in `fake_vendor`: per
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
same-origin check), like "Ask the vendor". The answers page gets a table
of live prices against our cards, differences first.

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

**Answered 19-Nov-2026**:

1. DHL Express Worldwide goes to OCEANIA through `carrier_api`, marked
   placeholder, so the test setup and the staff page exercise it. Every
   other lane stays on our cards.
2. A price is usable for 24 hours by default, per account, with
   `price_expiry` as above.
3. Each live price is stored and shown beside our card's price for the
   same service and lane, with no `shipping_price_disagreed` fact until
   something consumes it.

**4c-2 built 19-Nov-2026** (`src/mor/couriers/`, `mor.fakes.courier`,
`0012_carrier_prices.sql`, fake `0006_courier.sql`). As designed, with
these changes made while building:

- A lookup that ends `not_served` or `needs_person` is held for the
  account's period too (from `finished_at`, which replaced `answered_at`),
  or every request in the meantime would ask again.
- `usable_until` is worked out where a lookup is read (`read_price`), by
  the account's rule at that moment, and `build_quote` compares it with
  `QuoteRequest.now`.
- `InputsMissing` gathers what every option lacks, duty rates and carrier
  prices alike, instead of stopping at the first.
- A carrier's price is converted part by part and rounded up, as from our
  own tables. A duty-paid fee with fuel on it uses our fuel row for the
  service; without a usable one the option is left out (`fuel_unknown`).
- Our card's price for comparison comes from the first card of the service
  that can price the parcel (by code), whether routed or not, with no
  goods value: a surcharge on value counts as zero there.
- The staff page says when a live route's carrier has no account in this
  environment, instead of offering a button that cannot work.

The fuzzer was checked by planting bugs: an expired price reused, a call
counted only after it was made, parts that do not sum accepted, and a
second request while one is open. All four were caught; the last by the
partial unique index itself, which is the guard. The quote property
catches a carrier part left out of the quote. Checked end to end on a
scratch database with DHL routed live into SEA: the page asks, the worker
asks the fake, and the quote shows the live option beside our card's
price.

Open: no research country in OCEANIA quotes today (AU's rules are not
confirmed; NZ has no import tax recorded), so the live route there is not
seen on the staff page until one opens.

## 5. Session, API and pages (4d)

- `Merchant` (a minimal row now; KYB is a later slice), API keys stored
  hashed and shown once, created by a CLI until there is a dashboard.
- `POST /v1/checkout_sessions`, `POST /v1/estimates`, `GET` a session:
  JSON decoded with msgspec at the edge, `Idempotency-Key` required and
  unique per merchant, one error shape.
- Quote flow as plan-architecture §8: when every input is stored the
  handler quotes in its transaction; otherwise `quote_requested`, workers
  fetch what is missing, and the page refreshes until `quote_made` or
  `quote_refused`. No handler calls a vendor.
- Buyer pages `/c/{session}` (cart, address, quote), server-rendered,
  English first, with the merchant's name. The pay step is slice 5.

## 5b. The staff page

A staff page, `/staff/markets`, shows what each step decides, and grows
with every step of this slice (decided 17-Nov-2026): 4a the loads and each
country's answer (open, or closed with the reason, and the rates in
force); 4b a form for country, category and order value showing the tax
paths and the duty decision; 4c the full price breakdown; 4d the sessions
and quotes made. It is a staff tool, not a sandbox one: after this slice
it becomes the admin page that edits market data. It is plain forms,
read-only until then.

Reworked 18-Nov-2026 (it had grown into one long unstyled page): three
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
the data rows that decided it. (Cards per option were tried first and dropped the same
day: they compared options but did not explain one price.)
**Vendor answers** (`/staff/markets/answers`) and **Market data**
(`/staff/markets/data`) hold the rest. Scripts improve it (the quote
follows the form as you type; tables filter), and it works without them.
Amounts show in USD and EUR beside the quote's own currency, converted at
the load's exchange rates for reading only. The freight field of 4b's
check is gone: with a parcel the explanation uses the chosen option's
freight.

Until staff sign-in exists it is mounted only outside prod, reached on
loopback like the sandbox page. Staff sign-in (staff accounts, password
plus a second factor, sessions with a SameSite cookie, the cross-site
check on every POST, the person recorded as actor) is a slice of its own,
before the first prod deploy (decided 17-Nov-2026); the page is mounted in
prod behind it, unchanged.

## 6. Questions, answered 17-Nov-2026

Q1. **Which research statuses may a quote rely on?** Answer: `verified`
and `secondary`, and updates must be painless (§2). As proposed: a country whose quoting values include
`unverified`, `estimate`, `to_fetch` or `awaiting_answer` is closed at
checkout ("rules not confirmed yet"), failing closed rather than charging
a buyer on a guess. Today that would close PH (border limit awaiting an
answer) and LA and KH (sources conflict) until answered.

Q2. **Who edits market data after launch?** Answer: the CSVs for now;
after slice 4 the database becomes the single place to edit, through an
admin page that writes new rows, and the CSV and spreadsheet views are
generated from it. The loader then runs once, as the import.

Q3. **Product category on each line.** Answer: the merchant tells us both
the category (from our closed list: product gate, tax rate, border rule)
and the HS code (duty rate). Where a merchant cannot, staff classify the
merchant's stored product by hand, recorded as a staff action; nothing is
guessed automatically for now. A line missing what its quote needs is
refused with `classification_needed`; the HS code only where the duty
depends on it.

Q4. **FX.** Answer: as proposed. The buyer pays in their currency, so
every quote needs a rate; which source is a PSP question (slice 5). For
now: an `fx_rate`
table filled by hand or the loader (ECB reference rates where they exist,
as plan-markets-data already uses), behind the same switchable-source
shape, and the `fx` fee component at 0% until you decide a margin.

Q5. **Placeholder numbers.** Answer: as proposed. The default
`mor_margin` percentage and the `psp_fee` estimates per country are
placeholders until the PSP contract and pricing exist: seed them as data marked placeholder, and
refuse to open prod checkout while any placeholder row is in force.

Q6. **Quote validity.** Answer: as proposed, 30 minutes (plan-merchant-surfaces §3),
after which the page makes a fresh quote and shows if the total changed.

## 7. Rules this applies

Money as integers with their currency; exact fractions for rates; named
rounding with remainders booked. Effective-dated, append-only rule rows,
and every quote stores each input's source and version. Switchable sources
per lane, picked by routing data, fakes for every vendor mode. Locks and
unique keys, not clocks; time only starts work (a refresh, an expiry
check). No handler calls a vendor. Typed records at every edge.

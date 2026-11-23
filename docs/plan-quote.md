# Slice 4: markets, the tax path, prices and the quote

Status: built (4a 17-Nov-2026, 4b and 4c-1 18-Nov-2026, 4c-2 and 4d
19-Nov-2026); confirmed 17-Nov-2026, answers in §6. Implements
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
with an exhaustive `match`, and refuses an account kind the environment
does not allow (`mor.provider_accounts`).

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

Split 19-Nov-2026 into **4d-1** (merchants, API keys, the API with
idempotency: sessions are created and read, estimates priced) and
**4d-2** (the stored quote and its waiting flow, the buyer pages, the
staff view of sessions and quotes; ends with the slice's check).

### 4d-1: merchants, API keys, sessions and estimates (decided 19-Nov-2026)

Applies plan-merchant-surfaces §3 and §4 and plan-architecture §4 and
§8. Where they differ, the architecture wins: idempotency keys are kept
for good, not 7 days.

**Merchant.** A minimal row: `id` (uuid7), `legal_name`,
`display_name` (what buyers see), `country` (VN for now),
`daily_vendor_asks` (answer 3), `created_at`.
Created with `python -m mor.merchants create`, which prints the id.
There is no KYB state yet: until the KYB slice we create every merchant
ourselves, and prod does not open before staff sign-in anyway. (Compliance
touchpoint: before a merchant takes a live payment, KYB must say
`approved`; slice 5 checks it.)

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
  `rate_limited`.
- `code` is a closed set as well, documented with the API.
- msgspec's decode errors map onto `invalid_request` and name the field
  (`param`).

**Ids in the API.** The prefix plus the uuid7 in hex: `cs_…` for sessions
and `mer_…` for merchants. The database keeps the uuid. The ids sort by
time and are unguessable enough for a bearer link (uuid7 has 74 random
bits).

**`POST /v1/checkout_sessions`.**
- Body: `lines`, `parcel`, `merchant_reference?`, `return_url`,
  `cancel_url`.
- Each line: `name`, `quantity`, `unit_price {amount_minor, currency}`,
  `category`, `merchant_sku?`, and `customs {description, origin_country,
  weight_g, hs_code?}`.
- `tax_behavior` is not accepted: v1 is exclusive only.
- Lines may be priced in different currencies; the quote already converts
  each one.
- Stored: a `checkout_session` row (`state` = `open`, `expires_at` 24 h
  after creation, the parcel, the URLs, the merchant reference) and one
  `checkout_session_line` row per line, a snapshot that is never changed.
- The fact `checkout_session_created` v1 goes into the outbox. Nothing
  consumes it yet.
- Response: 201 `{id, state, url, expires_at, lines, …}`. `url` is
  `/c/{id}`, which answers 404 until 4d-2.
- Expiry is stored only: the step that expires a session, decided under
  its lock, comes with 4d-2.

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
- `src/mor/merchants/` holds the records, the store, the keys and the CLI.
- `src/mor/api/` holds the decoding at the edge, the errors, the
  idempotency and the handlers.
- The routes go in `main.py` in every environment: the API is the product,
  and each environment has its own keys.
- Seed: a "Sandbox Shop" merchant if there is none, and one key printed
  the first time.
- New migration: `merchant`, `api_key`, `api_idempotency`,
  `checkout_session`, `checkout_session_line`, `merchant_vendor_asks`
  (a merchant's count per UTC day); enum values for the aggregate and
  the fact.

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

**Questions** (my recommendation first):

1. **The parcel.** Should the merchant send it (weight and box) on every
   session and estimate? Recommended: yes, required. Our prices depend on
   the volumetric weight, and a box we guessed would misprice. The
   line's `weight_g` stays required for customs. The alternative is to
   sum the line weights and assume a standard box.
2. **Category per line.** Should each line name one of our categories
   (`docs/markets/categories.csv`), with an unknown one refused?
   Recommended: yes. The tax path and service restrictions are per
   category, and suggesting one from the description is a later
   feature. The HS code stays optional.
3. **Estimates.** No `Idempotency-Key` (an estimate changes nothing but
   deduplicated asks), and `estimate_pending` with `Retry-After` while
   inputs are fetched. Recommended: yes. Note that a merchant's estimates
   can make us pay a vendor for each new combination of HS code and
   country. Each combination is asked only once, but a request limit per
   merchant may be needed before prod.
4. **Fee overrides per merchant** (§4 said 4d). Recommended: later, with
   the admin pages that enter them. Until then every merchant gets the
   market's fee rows.

**Answered 19-Nov-2026:**

1. **The parcel is required**, and lines will also come by reference.
   - A product sent before, or one from a catalog we may build for the
     merchant later, can be named by its id instead of being sent in full
     (plan-merchant-surfaces §3, "inline or by reference").
   - 4d-1 takes inline lines only, and its line shape leaves room for
     `{product_id, quantity}` as a later, additive v1 change.
   - Once products exist, a product can carry its own packaging, so an
     order of one product needs no parcel.
2. **Category required; classification is ours.**
   - Correct HS classification is the importer of record's legal
     responsibility, and neither DDP nor a broker moves it off them. As
     merchant of record that is our side. Checked 19-Nov-2026 in trade
     guidance (DSV on DDP; importer-of-record guides), not in law: which
     party is importer of record per country is a compliance question for
     an adviser.
   - So a merchant's HS code is a hint, not the answer:
     - each line requires a category from our closed list;
     - a merchant's `hs_code` is accepted and stored as the merchant's,
       and used for now;
     - our own classification per `merchant_sku`, by a vendor or a
       person, comes later and is stored once per SKU, so the merchant's
       code can be checked and later replaced.
   - Until that exists, a line with no HS code is priced where our rows
     need none (a rate per category, or import tax from the band row). A
     country whose duty mode needs the code refuses it with 422
     `classification_needed`.
3. **Estimates: yes, with limits.** Two layers:
   - **What costs money: vendor asks.** Each merchant has a daily cap
     (`merchant.daily_vendor_asks`, default 100) on asks that are new.
     Asks the existing guards deduplicate are not new, so they don't
     count. The count is a row per merchant and UTC day, read and raised
     under its row lock in the estimate's transaction, and raised only
     when an ask inserted a new lookup. Over the cap the ask is not made,
     and the answer is 429 `rate_limited` with `Retry-After` set to the
     next UTC midnight. The day is a business rule compared under the
     lock, not a lease.
   - **Plain request volume:** a limit per IP address and per key in
     nginx, set in the deploy slice. In-process counters would break the
     no-clocks, no-memory rule and would not survive several processes.
4. **Fee overrides per merchant: later**, with the admin pages.

**4d-1 built 19-Nov-2026.**

Changed while building:
- **Errors on our side get their own type.** A refusal that is ours to
  fix (no market data, no fee rule, no exchange rate, no account for a
  vendor, a placeholder in prod) is type `unavailable` with status 503.
  It is not a 422 `quote_refused`, which the merchant's request caused.
- **API times are always in UTC, with the offset written.** A session
  read back from the database came out in the connection's time zone
  (+01:00 on the laptop); the test that compares a read with the create
  caught it. Every machine-read time now goes through
  `mor.dates.format_instant`: RFC 3339, UTC, to the second
  (`2026-11-18T09:30:00Z`). This is a shared convention now.
- **Merchants take no import from the web layer.** The public id helpers
  live in `mor.public_ids`, so the command line does not import from
  `web/`.

How it was checked:
- Six planted bugs were each caught by a test:
  - the idempotency check ignoring the request;
  - a refusal after the key's claim not rolled back, which would have
    committed a key with no response, because psycopg commits on a clean
    exit;
  - the estimate dropping the fee parts;
  - the daily cap ignored;
  - a session read without its merchant;
  - the decoder letting an exception out.
- A seventh plant (validation errors folded into `invalid_body`) is not a
  bug the properties can see, since msgspec's `ValidationError` is a
  `DecodeError`. The unit test that checks each error's `param` caught it.
- End to end on the sandbox over HTTP:
  - a session was created, replayed byte for byte, refused with 409 for
    another body under the same key, and read back;
  - a request without a key got 401;
  - an SG estimate answered from our tables, with each option's parts
    summing to its total;
  - a TH estimate answered `estimate_pending` until the worker had asked
    the fake duty vendor, then 200, with one new lookup counted for the
    day;
  - a DE estimate got 422 `country_closed`, with the reason.

### 4d-2: the stored quote, its waiting flow and the buyer pages (decided 19-Nov-2026)

Applies plan-architecture §2 (stage 2), §4 and §8 ("Waiting"), and
plan-merchant-surfaces §5 (the address and quote steps; the pay step is
slice 5). It ends with the slice's check: a merchant creates a session, a
buyer enters an address and sees an all-in price, on the sandbox.

**The session as a state machine.** `checkout_session` is the aggregate.
Every change takes its row lock, and facts carry the steps.

```
open ──address──▶ quoting ──inputs held──▶ quoted ──(slice 5: paying …)
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
- New enum values `quoting`, `quoted` and `refused`. `paying` and
  `completed` come with slice 5.

**The buyer's address** is a new row, `checkout_address`, one per address
given. They are never changed, and the session points at the newest.
- Fields: name, email, phone, address lines, city, region, postcode,
  country (question 1).
- This is personal data. It is never logged, only synthetic in tests and
  the seed, and kept only in the database. Retention and data residency
  are compliance touchpoints for an adviser, not answered here.
- A changed address makes a new quote; the old one stays as it was
  (immutable).

**The stored quote** (plan-architecture §8, "the quote stores every
input's source and version").
- Tables:
  - `quote`: session, address, `market_load_id`, currency, made at and
    `valid_until` (question 2), placeholders, and state `made` or
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
4. While the session is `quoting`, the page refreshes itself every 2 s
   with `<meta http-equiv="refresh">` and says it is working out
   shipping and duties. No JavaScript is needed.

The asks a buyer starts don't count against the merchant's daily cap.
They are bounded per session instead (question 3).

**Quote validity and session expiry.** Time only triggers; the decision
is taken under the lock.
- A quote is valid until `valid_until`. Past it the page shows the old
  price as expired, with a "Get the current price" button (a POST that
  quotes the same address again). The pay step (slice 5) compares
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
    four lines plan-architecture §8 decided (items with our fee inside,
    shipping, duties and taxes, total), the time it is valid until (in
    the `01-Jan-2027 14:35 UTC` format), and a disabled "Pay" saying
    payment comes next (slice 5);
  - refused: the reason in buyer words, with the address form to try
    another;
  - expired: a 303 to the merchant's `cancel_url`.
- POSTs use plain forms, the same-origin check, and no cookies: the
  session link is its own bearer token for that cart only.
- Prod refuses a quote standing on a placeholder, as estimates do.

**The API.** `GET /v1/checkout_sessions/{id}` gains:
- the new states;
- `ship_to_country`;
- `quote`: id, `valid_until`, currency, and per option its key and
  total, the same shape as an estimate's options.

The full address is not returned. The merchant needs it for fulfilment
from slice 5 on, and the order's endpoints carry it then. A v1-additive
change.

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

**Questions** (my recommendation first):

1. **Which address fields, now.** Recommended: name, email, phone, two
   address lines, city, region, postcode and country, all now. The
   buyer enters them once. The label (slice 9) and the order page's
   magic link (slice 5) need them, and the quote may use the postcode
   later (remote-area surcharges). Per-country forms (KR's customs code,
   no postcode in some countries) come when those countries open. The
   other choice is the country only now and the rest at the pay step.
   That's less to store before a sale, but it makes a second form.
2. **How long a quote holds.** Recommended: 30 minutes (the surfaces
   plan's example), then "Get the current price".
3. **Bounding what buyers make us ask.** Recommended: at most 10
   addresses per session (a buyer can still try a few, while a script
   can't sweep countries), and none of it counted against the merchant's
   daily cap, which is for its own estimates. The other choice is to
   count buyer asks too, but then a buyer could use up a merchant's cap
   and block real checkouts.

**Answered 19-Nov-2026:** the design is agreed, with 30 minutes per
quote and at most 10 addresses per session. For the address fields,
fruggals was the model (`~/Desktop/fruggals/src/addresses.py`): a pure
parser, the phone's dial code kept apart from its number, and a postcode
checked strictly where its format is fixed. What changes here:

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
  order page's link (slice 5) also goes to the email. Checked
  19-Nov-2026 in DHL's guide and its MyDHL field guidelines, not in its
  API reference: confirm when the courier slice books labels.
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

**4d-2 built 19-Nov-2026.**

Changed while building:
- **A share lock on each lookup waited on.** `still_open` reads a
  lookup's state with `FOR SHARE`. A completion that commits between the
  build and the wait would otherwise announce its outcome to no one, and
  the buyer would wait for ever. The foreign key from the wait row locks
  the lookup too, but only after the state was read. A test with real
  concurrent connections (`tests/checkout/test_quote_concurrency.py`)
  fails without the lock.
- **The referrer policy is `same-origin`.** `no-referrer` made browsers
  send `Origin: null` on the page's own POST, so the same-origin check
  refused every address. The tests, which set the header themselves, could
  not see this; the sandbox in a real browser did.
- **An unknown country gives no phone error.** It is only checked once
  the country, and so its dial code, is known.

How it was checked:
- Planted bugs, each caught:
  - no share lock;
  - a finished lookup telling nobody;
  - waits not recorded;
  - only open sessions expiring;
  - goods parts not stored;
  - one address too many allowed;
  - a late rebuild bringing back a replaced address. The fuzzer's first
    run missed this one, and the check "a session points at its newest
    address" was added.
- End to end on the sandbox in a headless browser at phone width:
  - a wrong SG postcode was shown back on its field, with the other values
    kept;
  - SG quoted at once, with two options and the four lines;
  - a TH cart with an HS code not asked about yet showed the waiting page
    until the worker had the duty vendor's answer, then the price;
  - the API and the staff Sessions pages showed both.

Open:
- SG's quotes carry no GST. `registrations.csv` says we are not
  registered there yet, so the tax path charges none (correct for the
  data). Selling into SG before registering is a compliance decision, not
  a code one.
- The "Sold and invoiced by" line names `[our company]` until the
  company and the legal wording exist.
- Waiting without full reloads (a meta refresh inside `<noscript>`, a
  script in `ts/` polling a status route) was built in slice 5a, for
  quotes and QR payments alike.

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

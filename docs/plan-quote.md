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
  read; the generated page is unchanged. A country whose
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

- `tax_paths(country_rules, order_value, registrations) -> TaxPaths`,
  pure, exactly as plan-logistics §4: indirect tax `NotDue`,
  `SellerCollected` or `BorderPaid`; duty `NotDue` or `BorderPaid`.
  Limits compared in the limit's own currency through the quote's FX rate,
  rounding named.
- Seller-collected tax: one module per jurisdiction (`tax/gb.py` first),
  holding the rule (what is in the base, per consignment or per item),
  never the rate, which is data. Until a registration row exists, no order
  takes `SellerCollected`, so the UK stays as `conditional` in §3 of the
  logistics plan says.
- Duty rates per (HS code, origin, destination), effective-dated,
  append-only, each naming its source and version. Source per destination
  is routing data: `own_table` (loaded, or entered with its legal
  reference) or a duty vendor through `duties/registry.py`. A missing
  combination emits `duty_rate_needed`; the vendor is asked once, not per
  quote. A fake duty vendor (under `/_fake/`) offers every mode, as the
  echo fake does.

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

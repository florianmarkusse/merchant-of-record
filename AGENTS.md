# AGENTS.md

Shared conventions for all projects live in `~/Desktop/conventions/AGENTS.md`
(loaded in every session). This file holds what is particular to merchant-of-record,
and wins where the two differ.

## Project Rules

- Purpose: a merchant of record (MoR) for companies in Southeast Asia
  selling to buyers in other SEA countries, and later the rest of the
  world. As MoR we are the seller in the buyer's eyes: we take the payment,
  issue the invoice in our name, owe and file the indirect tax, and handle
  refunds and chargebacks. We then pay the merchant out, minus tax and fees.
  Early stage: expect the project's shape to change, and keep this file
  current as it does.
- Python with pyright strict (decided 05-Nov-2026; why not C or Rust:
  `docs/plan-architecture.md` §3).
- Where to start: `docs/README.md` says what is built, what comes next
  and which document holds what. `docs/plan-architecture.md` is the design
  for the whole flow. A plan describes the design as it is now: a change
  rewrites the plan it changes and updates the decisions it overturns;
  there are no amendment pages, and what comes next lives only in the
  README.
- The design's decisions and their reasons are in the plans. Those that
  shape everyday code (§ is `docs/plan-architecture.md`):
  - The merchant receives exactly the price it names. Named fee
    components (estimated PSP fee, FX, our margin), shipping, tax and
    duty are added on top; the buyer sees them inside the item price.
    Merchant prices are tax-exclusive (§8).
  - After a vendor call with no answer, the operation's duplicate guard
    decides (`vendor_constraint`, `compensable`, `none`), never a lookup
    alone and never a clock (§5).
  - The duplicate guard for anything a vendor sends is the vendor's own
    id (event id, notice id, file name), never a hash of the bytes
    (`docs/plan-messaging.md` §7).
  - Every provider account is `fake`, `sandbox` or `live`, and the
    registry refuses a kind the environment does not allow. The fakes
    are fake vendors over HTTP inside the dev web server, under `/_fake/`
    (§6).
  - We pay every carrier bill; a person attributes each difference to us
    or to the merchant by cause (§9).
- Local databases `mor_dev` and `mor_test` on the PostgreSQL 18 cluster,
  reached over peer auth as the developer's own role
  (`postgresql:///mor_dev`), no dedicated role locally (decided
  16-Nov-2026). Ids are uuid7, made in code (`uuid.uuid7()`), so they
  sort by time and code knows an id before inserting.

### Chosen Stack

The shared Storage rules apply as written; what follows is particular to
this project.

- Web: server-rendered HTML built by typed functions, with no template
  language and no string formatting for markup. Avoid JavaScript where
  possible: our own pages work without it, and a script only improves a
  form whose work the server also does when the form is sent. Payment
  provider components (card fields, wallets) need JavaScript and are
  allowed in checkout; the server still takes the outcome from the
  provider, never from the browser (decided 04-Nov-2026: a strict no-JS rule
  is not possible with PSP card fields). Merchant-facing JSON APIs and
  webhooks come next to this, not instead of it.
- Libraries (decided 05-Nov-2026):
  - Web: Starlette with plain handlers; the request is an argument, never
    hidden global state. HTML: htpy (typed functions, escaped by default).
  - Database: psycopg 3 (async) with hand-written SQL, rows read into
    frozen dataclasses; migrations run by a small runner of our own.
    Every database enum loads as its StrEnum through `mor.db.enums`
    (decided 21-Nov-2026), so `class_row` reads a row straight into its
    domain record; `tests/db/test_enums.py` keeps that table complete.
    Code writes a state as its member, passed as a parameter, not as a
    quoted label in SQL; a filter in a `WHERE` may keep the label.
    `numeric` is only for whole numbers that can outgrow `bigint`, with a
    CHECK that its scale is 0, read as an `int` (`mor.db.numeric`), never
    as `Decimal`.
  - Parsing at the edges: msgspec. Every input (API body, webhook, form,
    settlement file) is decoded into a typed, frozen record at the edge
    before anything else sees it, because Python does not check types at
    runtime.
  - Outgoing HTTP: httpx2, the maintained fork of httpx, which is what
    the conventions' "httpx" means here (decided 17-Nov-2026; why:
    `docs/plan-messaging.md` §11). Crypto: `cryptography` and joserfc (HMAC,
    JWT/JWE). Invoices as PDF: WeasyPrint, from the same HTML functions.
  - Money: `int` minor units with a currency enum; exact rates as
    `fractions.Fraction`. Never `float` or `Decimal` for amounts.
- Frontend (decided 18-Nov-2026): the staff pages' stylesheet is
  `static/css/staff.css`; scripts are strict TypeScript in `ts/`, compiled
  by `tsc` into git-ignored `static/js/` (no bundler, no framework, as in
  the other projects). Run `npm install` once; the Stop hook runs `npx
  tsc` from then on. Every page works without them: a script only
  improves a form or table the server already handles.
- The app sends the Content-Security-Policy itself, in every environment,
  from `src/mor/web/csp.py` (decided 25-Nov-2026, overriding the shared
  convention that nginx sets it in prod): dev, the browser smoke suite and
  prod get the same header, so it cannot drift. No inline scripts or
  `style` attributes; a PSP's origins join the policy through the PSP
  registry (`docs/plan-payments.md` §4, card fields).
- Checks: the shared hooks run them, vulture (from `[tool.vulture]`) and
  `npx tsc` (from `tsconfig.json`) included; vulture's reviewed false
  positives are in `vulture_whitelist.py`. A rule ruff cannot say becomes an
  ast-grep rule: in the conventions' `ast-grep/` when it holds for every
  project (the hook runs those everywhere), otherwise here
  (`ast-grep/rules/`, examples in `ast-grep/rule-tests/`; `uv run ast-grep
  scan` shows its cases). A failing check says why.
  `typings/` holds stubs for the untyped parts we call (WeasyPrint, for
  the invoice PDF; it also needs Pango from the system). `scripts/` holds
  PEP 723 scripts with their own dependencies (`uv run scripts/markets.py
  check`); they are outside the package's pyright run.

### Commands

```
uv run python -m mor.db.migrate          # apply migrations/ to DATABASE_URL (default mor_dev)
uv run python -m mor.fakes.migrate       # apply migrations_fake/ (schema fake_vendor); refused in prod
uv run python -m mor.messaging.worker    # the worker: runs due deliveries; outside prod watchfiles restarts it on a change under src/
uv run python -m mor.probe               # start a probe, exit 0 once the worker finished it
uv run python -m mor.web.serve           # the web app on SELF_URL (default 127.0.0.1:8741), fakes outside prod; uvicorn reloads it on a change under src/
uv run python -m mor.seed                # test setup: echo accounts per provider and mode, duty vendor and carrier accounts at the fake, a fake PSP account per wiring with a default route, the Sandbox Shop merchant and its key (server must run)
uv run python -m mor.markets.load        # show what docs/markets/ would change; --write loads it as a new set
uv run python -m mor.merchants           # create/list merchants; `key mer_…` prints a new API key once; `revoke <key id>`; `kyb mer_… approved` (or pending, suspended)
deploy/soak.sh                           # the nightly soak, now; `install` sets up its timer, `test-alert` checks Telegram
npx tsc                                  # compile ts/ into static/js/ (after `npm install` once)
uv run pytest                            # drops and recreates mor_test, then runs the suite
HYPOTHESIS_PROFILE=soak uv run pytest    # the long property and fuzz run
uv run pytest tests_browser              # the browser smoke suite: its own mor_smoke, server and worker on 8742
uv run python -m tests.messaging.fact_schemas  # record a new fact version's shape (never rewrites an existing one)
```

- The test setup (`docs/plan-test-setup.md`) runs on this laptop for now:
  `ENVIRONMENT=test DATABASE_URL=postgresql:///mor_sandbox` for the server,
  worker, seed and probe, on `127.0.0.1:8741`. Never point it at `mor_test`,
  which every pytest run drops.
- Tests: `tests/conftest.py` recreates `mor_test` once per session with
  the real migrations, and empties every table before each test that asks
  for `database_url`. Tests commit for real, so commit-time triggers (the
  ledger balance check) run as in prod. The runner's own tests use a
  throwaway `mor_test_migrations`.
- A new property test or fuzzer joins a target in `deploy/soak.sh` in the
  same change, with a budget from a timed run; `tests/test_soak_targets.py`
  fails until it does.
- A state machine fuzzer lists its milestones and records its reach
  (`tests/flow/reach.py`); the soak fails a milestone reached by fewer than
  1 run in 200. Depth comes from structure (machines split by stage, a deep
  start, per-run fault plans, preconditioned rules), not from
  `hypothesis.target`, which was measured and dropped
  (`docs/plan-architecture.md` §10).
- Async tests: pytest functions are synchronous and run their async body
  with `asyncio.run`, one event loop per call; no pytest async plugin.
- The ledger refuses `UPDATE` and `DELETE` by trigger but not `TRUNCATE`
  (the suite truncates between tests). The prod database role must not be
  granted `TRUNCATE` on ledger tables: to be done in the deploy slice.

## Code Style

- Records are frozen dataclasses or msgspec structs, not `TypedDict`;
  closed sets are `StrEnum` or `Literal`; results are unions of records
  matched with `match`. A payment that can be `captured` or `refunded`
  is a closed set of states, not booleans. A closed set that is a value
  we switch on is a `StrEnum` (or a `Literal` when it never reaches the
  database), never a bare `str`.
- Checks enforce these, and each says why when it fails: a `match` over
  a closed set lists every case with no `assert_never` fallback (`case
  _:` only where it does real work); a record of 4 or more fields is
  `kw_only` and built with keywords; a query of two or more columns is
  read with `class_row`, never as a tuple, and a single column with
  `one_value`; a read that must find a row uses `mor.db.fetch`, not
  `fetchone()` and an assert; a function that changes a row it read
  takes the row's `Locked…` type, made only by its `lock_*` function; no
  import aliases; no local named after a domain name, module or builtin.
- Every id and code has its own `NewType` in `mor.ids` (`MerchantId`,
  `PspAccountId`, `CountryCode`, `CarrierCode`, `JournalKey`), so pyright
  refuses one where another belongs (decided 24-Nov-2026). A uuid id is
  named after its table. A value gets its type where it is made
  (`OrderId(uuid.uuid7())`), read (the record's field) or parsed (a path
  parameter, `mor.public_ids`, a market data file, a form once
  normalized), and passes through unchanged elsewhere. A bare `uuid.UUID`
  is only for generic messaging code (a message, an aggregate of any
  kind); the `typed-ids` ast-grep rule refuses it elsewhere, except in the
  echo flow until it is retired. A new string id or code gets its type
  there too: no check can find a bare `str`. The fakes stay untyped, as
  the vendors they stand for.
- The company that carries a parcel is a carrier, never a courier, in
  code, schema and plans (decided 24-Nov-2026). "Courier" stays only in
  market data and research, where it is the customs term (a courier
  regime, courier consignments).
- Name a value by its role (`seller`, `paid_invoice`), not with a `_row`
  or `_record` suffix. Where two modules clash (the many `flow` modules
  in `mor.messaging.consumers`), import by dotted path (`import
  mor.payments.flow`).
- `assert` is for what other code guarantees (a foreign key, a CHECK, a
  row the caller locked), to narrow a type; anything a request or a
  vendor can cause is a returned value, such as a declined card, a
  missing tax rule or a PSP timeout. A check that a state cannot occur
  is a `match` with one case per state and a comment on why the
  impossible ones are impossible.
- Timestamps: local time only at the edge, for display and for
  tax-point rules that name a local date. Inside the process every moment
  is aware UTC (decided 20-Nov-2026): a vendor's timestamp is decoded as
  `mor.dates.AwareDatetime` (one without an offset is refused) and
  converted with `to_utc`, a CSV cell with `parse_instant`; every database
  connection sets `TimeZone=UTC` (`mor.db.connect.SESSION_OPTIONS`), so a
  `timestamptz` comes back in UTC whatever the server's zone. Never patch
  a read with `.astimezone(UTC)`.

## Money and Ledger

- An amount is an integer in its currency's minor unit, ALWAYS stored and
  passed together with its ISO 4217 currency code (`amount_minor bigint`,
  `currency char(3)`), never a bare number. Two amounts in different
  currencies are never added.
- One table in code gives each currency's minor-unit exponent. Never
  hardcode "divide by 100". SEA has both 0-decimal (VND) and 2-decimal
  (SGD, MYR, THB, PHP) currencies. IDR uses exponent 0, a deliberate
  departure from ISO 4217's 2 (decided 04-Nov-2026: sub-rupiah amounts are
  not used in practice and Adyen uses 0; exponent 2 would only add amounts
  we can never charge). Our table follows ISO 4217 everywhere else, and each
  provider module converts to and from its provider's exponents. An
  incoming provider amount that is not a whole number in our exponent (e.g.
  a fee of IDR 1,234.56 from a 2-decimal PSP) is rounded explicitly at
  import with a named mode, and the remainder is booked.
- The ledger is double-entry and append-only. Every money movement is one
  journal entry whose lines sum to zero per currency. A correction is a new
  reversing entry, never an update or a delete. Balances are derived from
  entries, never stored as the truth.
- FX is a ledger event of its own: the rate (exact, never float, e.g. an
  integer numerator and denominator), its source and its timestamp are
  stored with the entry that used it.
- Rounding is explicit. Every operation that divides (tax, fees, FX, splits)
  names its rounding mode, and where a remainder can appear it is booked
  somewhere, never dropped. Verify rounding, tax and FX against realistic
  synthetic data.

## Messaging

The shared `messaging.md` applies from the start, not as a later
conversion, and to every money flow: checkout → capture → settle → payout
is a state machine driven by messages, and messages are facts only, never
commands.

## Merchant of Record Domain Rules

- Tax per jurisdiction: indirect tax rules (SG GST, MY SST, TH VAT, VN VAT,
  ID VAT, PH VAT, and later others) live in one module per jurisdiction,
  with effective-dated rates and thresholds. Never hardcode a rate in
  feature code. The rule applied, its version and the buyer-location
  evidence are stored with each transaction, so an invoice can be
  re-explained years later.
- PII and card data: we never see or store raw card numbers: the PSP
  tokenizes them, which keeps us out of full PCI DSS scope. Do not add
  code that would bring a PAN into our systems.
- Idempotency and audit: every money-moving request, inbound or outbound,
  carries an idempotency key that is stored and enforced by a unique
  constraint, and never expires. Financial records are appended, never
  updated in place or deleted. Every state change records who or what
  made it and when.
- Compliance and licensing (KYC/KYB of merchants, payment licences per
  country, data residency) shape the design. When a feature touches them,
  say so in its design and do not assume an answer.

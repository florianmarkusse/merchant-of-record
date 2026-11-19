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
- Decided so far (03-Nov-2026): PostgreSQL, data-oriented code, server-rendered
  HTML, message-oriented design from the start (see Messaging).
- Language: Python with strict static type checking (decided 05-Nov-2026:
  the team is fast in it, its libraries cover PSP crypto, JWE, PDF and
  PostgreSQL, and pyright strict checks closed sets and exhaustive `match`.
  C was ruled out because an internet-facing payment service parses
  untrusted input all day and would need its HTTP, JSON, JWT and HTML
  layers built or vetted by hand. Rust stays the alternative if Python's
  lack of runtime type checks ever becomes the bottleneck). See Chosen
  Stack.

- Where to start (15-Nov-2026): `docs/plan-architecture.md` is the confirmed
  design for the whole flow and wins over the earlier plans where they
  differ. Its §16 says what state the project is in and what comes next.
  Slice 1 (tooling, `money`, `db` and migrations, `ledger`) is built
  (16-Nov-2026); slice 2 is planned in `docs/plan-messaging.md`, and its
  steps 2a (outbox, deliveries, inbox, worker, probe, fuzzer), 2b
  (effect consumers, duplicate guards, the fake echo vendor) and 2c
  (inbound by push, thin push, poll, file, none, and by hand; the first
  web handler) are built. The fuzzer failure seen in 2c was a real
  thin-webhook bug, now fixed (`docs/plan-messaging.md` §13). Slice 3,
  the test setup, is planned in `docs/plan-test-setup.md` (on this laptop
  until a VPS exists); its steps 1 (serve, `SELF_URL`, seed), 2 (the page at `/_fake/`)
  and 3 (the nightly soak, `deploy/soak.sh`) are built. Slice 4 (markets, tax path,
  prices, the quote) is planned in `docs/plan-quote.md` in four steps; 4a
  (markets in the database, versioned loads), 4b-1 (the tax-path rule,
  rates per HS code with trade agreements, exact FX chains) and 4b-2
  (duty and VAT rates from vendors: a mode per country, asked once per
  combination, compared with our rows), 4c-1 (shipping from our own
  rate tables, fee rules, `build_quote` with one option per service) and
  4c-2 (live carrier prices through a fake courier, held while usable by a
  rule per courier account) are built; 4d (merchants, sessions, the quote
  flow) is next.
- Decided 17-Nov-2026: the duplicate guard for anything a vendor sends is
  the vendor's own id (event id, notice id, file name), never a hash of
  the bytes; the inbox's body hash only means "these bytes are stored
  already" (`docs/plan-messaging.md` §12.2).
- Decided 16-Nov-2026, starting slice 1:
  - Local databases `mor_dev` and `mor_test` on the PostgreSQL 18 cluster,
    reached over peer auth as the developer's own role
    (`postgresql:///mor_dev`); no dedicated role locally.
  - Ids are uuid7, made in code (`uuid.uuid7()`), so they sort by time
    and code knows an id before inserting.
  - The outbox moved from slice 1 to slice 2, where it is designed once
    with `outbox_delivery`, the worker and the `message_type` enum
    (plan-architecture.md §5, §12).
- Decided 15-Nov-2026, with the reasons in `docs/plan-architecture.md`:
  - A lock or a unique constraint guards; time only triggers. No leases,
    no expiring idempotency keys of our own, no "old enough, so take it
    over". Time starts work (retry, poll, deadline due) or is a promise
    compared under the row lock (§4).
  - After a vendor call with no answer, the operation's duplicate guard
    decides (`vendor_constraint`, `compensable`, `none`), never a lookup
    alone and never a clock (§5).
  - Messages are facts only, no commands. Inbound is push, poll or file
    as capability data, on one path (§5).
  - Providers are picked by a registry with an exhaustive `match`. The
    fakes are fake vendors over HTTP, inside the dev web server under
    `/_fake/`, and offer every integration mode (§6).
  - Shipping price and duty rates come from switchable sources, our own
    tables or a provider, per lane (§8).
  - The merchant receives exactly the price it names. Named fee
    components (estimated PSP fee, FX, our margin), shipping, tax and
    duty are added on top; the buyer sees them inside the item price.
    Merchant prices are tax-exclusive (§8).
  - We pay every carrier bill; a person attributes each difference to us
    or to the merchant by cause (§9).
  - The flow is fuzzed with Hypothesis: pure properties, the whole flow
    as a state machine against the test database and the fakes, and a
    real-concurrency suite (§10).

### Chosen Stack

- Storage: PostgreSQL. Schema changes are numbered SQL files in
  `migrations/`, applied in order and never edited once applied. No
  `SELECT *`. Persistence functions take the connection or transaction as
  their first argument and never commit: the caller commits, so a state
  change and its outbox message commit together.
- Fixed value sets are PostgreSQL enum types with a matching closed type in
  code, not text columns with a CHECK.
- Web: server-rendered HTML built by typed functions, with no template
  language and no string formatting for markup. Avoid JavaScript where
  possible: our own pages work without it, and a script only improves a
  form whose work the server also does when the form is sent. Payment
  provider components (card fields, wallets) need JavaScript and are
  allowed in checkout; the server still takes the outcome from the
  provider, never from the browser (decided 04-Nov-2026: a strict no-JS rule
  is not possible with PSP card fields). Merchant-facing JSON APIs and
  webhooks come next to this, not instead of it.
- Language: Python 3.14, packages and lock file managed with `uv`
  (decided 05-Nov-2026, see Project Rules).
- Libraries (decided 05-Nov-2026):
  - Web: Starlette with plain handlers; the request is an argument, never
    hidden global state. HTML: htpy (typed functions, escaped by default).
  - Database: psycopg 3 with hand-written SQL, rows read into frozen
    dataclasses. Migrations are run by a small runner of our own.
  - Parsing at the edges: msgspec. Every input (API body, webhook, form,
    settlement file) is decoded into a typed, frozen record at the edge
    before anything else sees it, because Python does not check types at
    runtime.
  - Outgoing HTTP: httpx2 (decided 17-Nov-2026), Pydantic's maintained
    fork of httpx 0.28.1 with the same API under `import httpx2`. httpx
    itself has had no release since 06-Dec-2024 and lists no Python 3.14
    support. This reads the conventions' "httpx for outgoing HTTP" as
    this fork. Crypto: `cryptography` and joserfc (HMAC,
    JWT/JWE). Invoices as PDF: WeasyPrint, from the same HTML functions.
  - Money: `int` minor units with a currency enum; exact rates as
    `fractions.Fraction`. Never `float` or `Decimal` for amounts.
- Records are frozen dataclasses or msgspec structs, closed sets are
  `StrEnum` or `Literal`, results are unions of records matched with
  `match` and `assert_never` for exhaustiveness.
- Frontend (decided 18-Nov-2026): the staff pages' stylesheet is
  `static/css/staff.css`; scripts are strict TypeScript in `ts/`, compiled
  by `tsc` into git-ignored `static/js/` (no bundler, no framework, as in
  the other projects). Run `npm install` once, then `npx tsc` after
  changing a `.ts` file. Every page works without them: a script only
  improves a form or table the server already handles (the quote page
  re-quotes as you type; the data and answers tables get a filter).
- Checks: the shared order plus `npx tsc`; pyright in strict mode. `scripts/` holds
  PEP 723 scripts with their own dependencies (`uv run scripts/markets.py
  check`); they are outside the package's pyright run.

### Commands

```
uv run python -m mor.db.migrate          # apply migrations/ to DATABASE_URL (default mor_dev)
uv run python -m mor.fakes.migrate       # apply migrations_fake/ (schema fake_vendor); refused in prod
uv run python -m mor.messaging.worker    # the worker: runs due deliveries; outside prod watchfiles restarts it on a change under src/
uv run python -m mor.probe               # start a probe, exit 0 once the worker finished it
uv run python -m mor.web.serve           # the web app on SELF_URL (default 127.0.0.1:8741), fakes outside prod; uvicorn reloads it on a change under src/
uv run python -m mor.seed                # test setup: echo accounts per provider and mode, duty vendor accounts at the fake (server must run)
uv run python -m mor.markets.load        # show what docs/markets/ would change; --write loads it as a new set
deploy/soak.sh                           # the nightly soak, now; `install` sets up its timer, `test-alert` checks Telegram
npx tsc                                  # compile ts/ into static/js/ (after `npm install` once)
uv run pytest                            # drops and recreates mor_test, then runs the suite
HYPOTHESIS_PROFILE=soak uv run pytest    # the long property and fuzz run
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
- Async tests: pytest functions are synchronous and run their async body
  with `asyncio.run`, one event loop per call; no pytest async plugin.
- The ledger refuses `UPDATE` and `DELETE` by trigger but not `TRUNCATE`
  (the suite truncates between tests). The prod database role must not be
  granted `TRUNCATE` on ledger tables: to be done in the deploy slice.

## Code Style

- Records are frozen dataclasses or msgspec structs (see Chosen Stack),
  not `TypedDict`. A payment that can be `captured` or `refunded` is a
  closed set of states, not booleans.
- Errors as values covers declined card, tax rule missing, PSP timeout.
- Never shadow domain names (`payment`, `merchant`, `invoice`, `ledger`)
  with local variables.
- Timestamps: local time only at the edge, for display and for
  tax-point rules that name a local date.

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
  somewhere, never dropped.

## Messaging

Message-oriented from the start, as the shared Messaging section describes.
Here it applies to every money flow: checkout → capture → settle → payout
is a state machine driven by messages, and a state change and its outbox
message always commit together.

## Merchant of Record Domain Rules

- Tax per jurisdiction: indirect tax rules (SG GST, MY SST, TH VAT, VN VAT,
  ID VAT, PH VAT, and later others) live in one module per jurisdiction,
  with effective-dated rates and thresholds. Never hardcode a rate in
  feature code. The rule applied, its version and the buyer-location
  evidence are stored with each transaction, so an invoice can be
  re-explained years later.
- PII and card data: never commit, log or put into a test fixture real
  buyer or merchant data. Use synthetic fixtures. We never see or store raw
  card numbers: the PSP tokenizes them, which keeps us out of full PCI DSS
  scope. Do not add code that would bring a PAN into our systems.
- Idempotency and audit: every money-moving request, inbound or outbound,
  carries an idempotency key that is stored and enforced by a unique
  constraint. Financial records are appended, never updated in place or
  deleted. Every state change records who or what made it and when.
- PSPs behind one module each: every payment provider (Stripe, Xendit, 2C2P,
  Adyen, …) gets its own module that translates our own types to and from
  its API. Exactly one place chooses a provider by name. In dev an offline
  mock provider is the default, so tests and local runs never call out.
  Real providers need `ENVIRONMENT=prod` and their keys in `.env`.
- Compliance and licensing (KYC/KYB of merchants, payment licences per
  country, data residency) shape the design. When a feature touches them,
  say so in its design and do not assume an answer.

## Agent Workflow

- Design first also covers changed message schemas.
- Verify against realistic synthetic data, especially for rounding,
  tax and FX.

## Operational Rules

Detailed operational rules go in `.ai/rules/` as the project grows, and are
linked from here.

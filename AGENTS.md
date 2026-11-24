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
  lack of runtime type checks ever becomes the bottleneck).

- Where to start: `docs/README.md` says what is built, what comes next
  and which document holds what. `docs/plan-architecture.md` is the design
  for the whole flow. A plan describes the design as it is now: a change
  rewrites the plan it changes and updates the decisions it overturns;
  there are no amendment pages, and what comes next lives only in the
  README.
- Decided 19-Nov-2026: the test setup may route chosen merchants to a
  real vendor's sandbox next to the fakes, once that vendor's module
  exists; every provider account is `fake`, `sandbox` or `live`, and the
  registry refuses a kind the environment does not allow
  (`docs/plan-architecture.md` §6).
- Decided 17-Nov-2026: the duplicate guard for anything a vendor sends is
  the vendor's own id (event id, notice id, file name), never a hash of
  the bytes; the inbox's body hash only means "these bytes are stored
  already" (`docs/plan-messaging.md` §7).
- Decided 16-Nov-2026: local databases `mor_dev` and `mor_test` on the
  PostgreSQL 18 cluster, reached over peer auth as the developer's own
  role (`postgresql:///mor_dev`), no dedicated role locally. Ids are
  uuid7, made in code (`uuid.uuid7()`), so they sort by time and code
  knows an id before inserting.
- Decided 15-Nov-2026, with the reasons in `docs/plan-architecture.md`
  (locks and constraints guard, time only triggers, as in the shared
  conventions):
  - After a vendor call with no answer, the operation's duplicate guard
    decides (`vendor_constraint`, `compensable`, `none`), never a lookup
    alone and never a clock (§5).
  - Inbound is push, poll or file as capability data, on one path (§5).
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
    frozen dataclasses. Migrations are run by a small runner of our own.
    Database enums load as our StrEnums (decided 21-Nov-2026): `connect`
    registers each pair in `mor.db.enums`, so a flat row reads straight
    into its domain record with `class_row`, with no `::text` cast and no
    mirror row class. Every database enum has a StrEnum in that table
    (decided 23-Nov-2026: a `Literal` read as text was unchecked at
    runtime); `tests/db/test_enums.py` fails on an enum missing there or
    on values that differ. Code writes a state as its member, passed as a
    parameter, not as a quoted label in SQL; a filter in a `WHERE` may
    keep the label. `numeric` is only for whole numbers that can outgrow
    `bigint`, with a CHECK that its scale is 0; `connect` reads it as an
    `int` (`mor.db.numeric`, decided 24-Nov-2026), never as `Decimal`.
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
- Frontend (decided 18-Nov-2026): the staff pages' stylesheet is
  `static/css/staff.css`; scripts are strict TypeScript in `ts/`, compiled
  by `tsc` into git-ignored `static/js/` (no bundler, no framework, as in
  the other projects). Run `npm install` once; the Stop hook runs `npx
  tsc` from then on. Every page works without them: a script only
  improves a form or table the server already handles.
- Checks: the shared hooks run them (conventions `AGENTS.md`, Hooks);
  this project's extras, `uv run vulture` and `npx tsc`, are in
  `.claude/checks.sh`. Vulture was set up 23-Nov-2026, with its settings in
  `pyproject.toml` and reviewed false positives, each with its reason, in
  `vulture_whitelist.py`. pyright runs in strict mode. Ruff
  also bans APIs we do not use (`TID251` in `pyproject.toml`, each with
  its reason). The formatter decides every layout: no `fmt: skip` or
  `fmt: off` (`tests/test_no_format_skips.py`). What ruff cannot say is
  an ast-grep rule (`sgconfig.yml`, `ast-grep/rules/`, each with examples
  in `ast-grep/rule-tests/`), run by `tests/test_ast_grep_rules.py`;
  ast-grep is a dev dependency, so `uv run ast-grep scan` shows a rule's
  cases.
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
uv run python -m mor.seed                # test setup: echo accounts per provider and mode, duty vendor and courier accounts at the fake, a fake PSP account per wiring with a default route, the Sandbox Shop merchant and its key (server must run)
uv run python -m mor.markets.load        # show what docs/markets/ would change; --write loads it as a new set
uv run python -m mor.merchants           # create/list merchants; `key mer_…` prints a new API key once; `revoke <key id>`; `kyb mer_… approved` (or pending, suspended)
deploy/soak.sh                           # the nightly soak, now; `install` sets up its timer, `test-alert` checks Telegram
npx tsc                                  # compile ts/ into static/js/ (after `npm install` once)
uv run pytest                            # drops and recreates mor_test, then runs the suite
HYPOTHESIS_PROFILE=soak uv run pytest    # the long property and fuzz run
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
- A `match` over a closed set lists every case and has no `case _:
  assert_never(...)` fallback (decided 23-Nov-2026): pyright's
  `reportMatchNotExhaustive` refuses a missing case, and a value outside
  its type cannot get past the edges. `case _:` stays only where it does
  real work (a `WrongFact` for a fact routing never sends).
- `assert` is for what other code guarantees (a foreign key, a CHECK, a
  row the caller locked), to narrow a type; anything a request or a
  vendor can cause is a returned value. A read that must find a row uses
  `mor.db.fetch` (`one_row`, `one_value`), not `fetchone()` and an
  assert. A check that a state cannot occur is a `match` with one case
  per state and a comment on why the impossible ones are impossible.
- A record of 4 or more fields is built with keyword arguments, and a
  query of two or more columns is read with `class_row` into a row record
  named after its columns, never unpacked from a tuple, also not a
  two-column row in a `for` (decided 23-Nov-2026, the two-column case
  24-Nov-2026: pyright sees tuple rows as `Any`, and two positional ints
  can swap unnoticed). A single column is read with `one_value`. Such a
  record says `kw_only=True`, so pyright refuses a positional build;
  `tests/test_records_kw_only.py` fails on one that does not.
- A function that changes a row it read takes the row as its `Locked…`
  type (`LockedAttempt`, `LockedSession`, `LockedOrder`, or a record of
  its own such as `LockedLine`), made only by the row's `lock_*`
  function (decided 24-Nov-2026): pyright then refuses a read-then-write
  without the row lock (ast-grep `locked-row-made-by-its-lock`).
- Errors as values covers declined card, tax rule missing, PSP timeout.
- No shadowing (decided 24-Nov-2026: one name meaning two things is
  confusing): no local or parameter named after a domain name
  (`payment`, `merchant`, `invoice`, `ledger`), a package or module, or
  a builtin. Name the value by its role (`seller`, `paid_invoice`), not
  with a `_row` or `_record` suffix.
- No import aliases, project or third-party, with no exceptions
  (decided 24-Nov-2026): `from hypothesis import strategies`, then
  `strategies.integers()`, not `as st`. Where two modules or names
  clash (the many `flow` modules in `mor.messaging.consumers`), import
  the module by its dotted path (`import mor.payments.flow`, its
  functions called through that full path), as the shared conventions
  show.
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
  somewhere, never dropped.

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
- Payment providers (Stripe, Xendit, 2C2P, Adyen, …) follow the shared
  External Services rules: one module each, one registry, the fake PSP
  outside prod.
- Compliance and licensing (KYC/KYB of merchants, payment licences per
  country, data residency) shape the design. When a feature touches them,
  say so in its design and do not assume an answer.

## Agent Workflow

- Verify against realistic synthetic data, especially for rounding,
  tax and FX.

## Operational Rules

Detailed operational rules go in `.ai/rules/` as the project grows, and are
linked from here.

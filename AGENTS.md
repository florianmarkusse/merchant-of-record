# AGENTS.md

## Project Rules

- Purpose: a merchant of record (MoR) for companies in Southeast Asia
  selling to buyers in other SEA countries, and later the rest of the
  world. As MoR we are the seller in the buyer's eyes: we take the payment,
  issue the invoice in our name, owe and file the indirect tax, and handle
  refunds and chargebacks. We then pay the merchant out, minus tax and fees.
  Early stage: expect the project's shape to change, and keep this file
  current as it does.
- Reference: this file is the source of truth for project conventions.
  Detailed rules go in `.ai/rules/` and are linked from here. Plans with
  their decisions go in `docs/plan-*.md`.
- Decided so far (2026-11-03): PostgreSQL, data-oriented code, server-rendered
  HTML, message-oriented design from the start (see Messaging).
- Language: Python with strict static type checking (decided 2026-11-05:
  the team is fast in it, its libraries cover PSP crypto, JWE, PDF and
  PostgreSQL, and pyright strict checks closed sets and exhaustive `match`.
  C was ruled out because an internet-facing payment service parses
  untrusted input all day and would need its HTTP, JSON, JWT and HTML
  layers built or vetted by hand. Rust stays the alternative if Python's
  lack of runtime type checks ever becomes the bottleneck). See Chosen
  Stack.

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
  provider, never from the browser (decided 2026-11-04: a strict no-JS rule
  is not possible with PSP card fields). Merchant-facing JSON APIs and
  webhooks come next to this, not instead of it.
- Language: Python 3.14, packages and lock file managed with `uv`
  (decided 2026-11-05, see Project Rules).
- Libraries (decided 2026-11-05):
  - Web: Starlette with plain handlers; the request is an argument, never
    hidden global state. HTML: htpy (typed functions, escaped by default).
  - Database: psycopg 3 with hand-written SQL, rows read into frozen
    dataclasses. Migrations are run by a small runner of our own.
  - Parsing at the edges: msgspec. Every input (API body, webhook, form,
    settlement file) is decoded into a typed, frozen record at the edge
    before anything else sees it, because Python does not check types at
    runtime.
  - Outgoing HTTP: httpx. Crypto: `cryptography` and joserfc (HMAC,
    JWT/JWE). Invoices as PDF: WeasyPrint, from the same HTML functions.
  - Money: `int` minor units with a currency enum; exact rates as
    `fractions.Fraction`. Never `float` or `Decimal` for amounts.
- Records are frozen dataclasses or msgspec structs, closed sets are
  `StrEnum` or `Literal`, results are unions of records matched with
  `match` and `assert_never` for exhaustiveness.
- Checks, in the order they run:
  1. `uv run ruff format --check`
  2. `uv run ruff check`
  3. `uv run pyright` (strict mode)
  4. `uv run pytest`

  A change is not done until they pass clean (0 errors, 0 warnings).

### Do Not Touch

- `.env` and any file holding secrets or keys.
- Applied migrations: add a new one instead.

## Code Style & Architecture

These hold whatever language is chosen.

### Data-Oriented Design

Represent data as plain records (structs, typed dicts), closed sets of
values (enums, literal unions) and plain functions. No classes that hide
state, no service hierarchies, no inheritance, no dependency injection, no
ORM. Data flow is explicit: a function gets what it needs as arguments and
returns what it produced.

### Typing

Strict static typing wherever the language allows it. Keep return types
narrow. Make illegal states unrepresentable: a payment that can be
`captured` or `refunded` is a closed set of states, not booleans.

### Errors as Values

Return expected failures (declined card, tax rule missing, PSP timeout) as
values and handle them explicitly at the call site. Raise or panic only on
bugs and broken invariants.

### Imports & Naming

- Do not alias project imports. Modules that cover the same thing in
  different layers get different names, so no alias is needed.
- Never shadow domain names (`payment`, `merchant`, `invoice`, `ledger`)
  with local variables.

### Flatter Control Flow

Prefer explicit loops and early returns over dense comprehensions, chained
combinators or nested expressions.

### Data Types

- Timestamps are ALWAYS aware UTC (`timestamptz`). Local time only at the
  edge, for display and for tax-point rules that name a local date.
- Money: see Money and Ledger. Never a float, anywhere.

## Money and Ledger

- An amount is an integer in its currency's minor unit, ALWAYS stored and
  passed together with its ISO 4217 currency code (`amount_minor bigint`,
  `currency char(3)`), never a bare number. Two amounts in different
  currencies are never added.
- One table in code gives each currency's minor-unit exponent. Never
  hardcode "divide by 100". SEA has both 0-decimal (VND) and 2-decimal
  (SGD, MYR, THB, PHP) currencies. IDR uses exponent 0, a deliberate
  departure from ISO 4217's 2 (decided 2026-11-04: sub-rupiah amounts are
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

Message-oriented from the start, even while everything runs on one machine.

- Transactional outbox: a state change and the messages it causes are
  written in the same database transaction (an `outbox` table). Nothing is
  sent to a queue, PSP or webhook directly from a request handler. A relay
  publishes from the outbox afterwards.
- Delivery is at-least-once, so every consumer is idempotent: it records
  the message ids it has handled (an inbox table, or a unique key on the
  effect) and treats a duplicate as already done.
- Messages are facts named in the past tense (`payment_captured`,
  `refund_requested`), immutable, with an explicit schema version. A shape
  change is a new version, and consumers handle the versions still in
  flight.
- Ordering is guaranteed per aggregate (one payment, one merchant), never
  globally. Do not write code that relies on global ordering.
- Start on PostgreSQL itself (the outbox table, workers claiming with
  `FOR UPDATE SKIP LOCKED`). Change data capture (logical replication) and
  an external broker are later steps. Keep the producer and consumer
  interfaces so that this swap changes the transport only. Record it here
  when it is made.
- Long-running flows (checkout → capture → settle → payout) are explicit
  state machines driven by messages. Each step stores and commits its
  result before emitting the next message, so a crash or restart never
  pays, charges or files twice.

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

## Agent Workflow Guidelines

### Do Not Commit

Agents must not run `git commit` unless explicitly told to in that turn.
Leave verified work in the worktree for the user to review.

### Design First for Nontrivial Changes

For changes that add tables or modules, change data shapes or message
schemas, or span multiple files:

1. Write out the design in prose.
2. Reference the project rules it applies.
3. Wait for confirmation before coding.

### Record Decisions Inline

When a decision is made, write it where it applies (here or in the
relevant `docs/plan-*.md`) with its date and the reason, e.g. "(decided
2026-11-03: …)". A later reader should find why something is the way it
is next to the rule itself.

### Verify Against Realistic Data

When unsure, test a change against realistic synthetic data. Do not rely on
reasoning or assertions alone, especially for rounding, tax and FX.

## Code search and navigation

Use the following tools according to the type of task:

- Use `rg` for literal text, filenames, error messages, comments, and logs.
- Use Serena for semantic symbol navigation, references, definitions, and
  refactoring. Its tools are deferred: load them with `ToolSearch` in one call
  before the first traversal of a session. Serena is for code traversal only:
  its memory tools and onboarding are disabled, and project knowledge lives
  in this file.
- Use `ast-grep` for syntax-aware structural searches and transformations;
  prefer it over `rg` when the question depends on code structure.
- Avoid reading entire large files when targeted search or symbol-level
  inspection is sufficient.

## Operational Rules

Detailed operational rules go in `.ai/rules/` as the project grows, and are
linked from here.

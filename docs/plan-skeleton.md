# Plan: project skeleton and first slice

Status: design, waiting for confirmation (2026-11-05). No code is written
until this is confirmed.

## 1. Scope

The first slice builds the foundation everything else stands on, and
nothing that talks to the outside world yet:

1. Tooling: `pyproject.toml`, `uv.lock`, the four checks passing on an
   almost empty package.
2. `money`: currencies, exponents, the `Money` record, exact division and
   allocation with named rounding modes.
3. `db`: connecting, and a migration runner for numbered `.sql` files.
4. `ledger`: tables and functions to record a balanced journal entry and
   derive balances.
5. `outbox`: the outbox and inbox tables and the functions to append,
   claim and mark messages.

Left for the next slices: the web layer (Starlette, htpy), the mock
provider and the provider port, checkout sessions, msgspec parsing, FX
events. Dependencies are added in the slice that first uses them.

## 2. Layout

```
pyproject.toml          project, dependencies, ruff and pyright config
uv.lock
.python-version         3.14
migrations/
  0001_ledger.sql
  0002_outbox.sql
src/mor/
  config.py             Environment (StrEnum), Settings record, read from env
  money/
    currency.py         Currency (StrEnum), EXPONENT table
    amount.py           Money record, add/subtract/negate, parse_major, format_major
    rounding.py         RoundingMode, divide, allocate
  db/
    connect.py          connect(settings) -> psycopg.Connection
    migrate.py          the migration runner (also `python -m mor.db.migrate`)
  ledger/
    account.py          AccountKind (StrEnum), Account record
    entry.py            Line, JournalEntry, build_entry
    store.py            record_entry(conn, ...), balance(conn, ...)
  outbox/
    message.py          OutboxMessage, NewMessage
    store.py            append, claim_batch, mark_published
    inbox.py            mark_handled(conn, consumer, message_id) -> Handled | AlreadyHandled
  audit.py              Actor record (who or what made a change)
tests/                  mirrors src/mor/
  conftest.py           test database, per-test transaction rolled back
```

`mor` is a placeholder package name until the company has a name; renaming
it later is one mechanical change.

## 3. Tooling

- `uv init --package`, Python `>=3.14`. Runtime dependency in this slice:
  `psycopg[binary]`. Dev group: `pyright`, `ruff`, `pytest`.
- pyright: `typeCheckingMode = "strict"`, plus
  `reportUnnecessaryTypeIgnoreComment` and `reportMatchNotExhaustive`
  turned on.
- ruff: line length 88. The rule sets `E F W I UP B SIM RET PTH DTZ ANN
  PL TID S` (DTZ catches naive datetimes; TID bans relative imports; S is
  bandit). `flake8-tidy-imports` bans the `decimal` module in `src/`, so
  `Decimal` doesn't creep into money code. Floats can't be banned by a
  linter; the `Money` constructor rejects anything that isn't an `int`.
- Checks, in order (as recorded in AGENTS.md): `uv run ruff format
  --check`, `uv run ruff check`, `uv run pyright`, `uv run pytest`.

## 4. `money`

- `Currency` is a `StrEnum` of the codes we support: VND, IDR, SGD, MYR,
  THB, PHP, USD, EUR. Adding a currency is adding a member and its row in
  `EXPONENT: Final[Mapping[Currency, int]]`. A test checks every member has
  an exponent. IDR is 0, with the AGENTS.md note next to it.
- `Money` is a frozen dataclass `(amount_minor: int, currency: Currency)`.
  `add`, `subtract` and `negate` are plain functions. Adding two
  currencies is a bug in our code, not an expected failure, so it raises
  `CurrencyMismatch` (AGENTS.md: raise only on bugs).
- `parse_major("12.34", Currency.SGD) -> Money | InvalidAmount` for amounts
  arriving as decimal strings at the edges; it parses digits by hand, never
  through `float`. Too many decimals for the currency is `InvalidAmount`;
  provider modules that need to round use `divide` explicitly instead.
- `RoundingMode` (`StrEnum`): `half_even`, `half_away_from_zero`,
  `toward_zero`, `away_from_zero`, `floor`, `ceiling`.
- `divide(money, ratio: Fraction, mode) -> Divided(result: Money,
  remainder: Fraction)` multiplies by an exact ratio (a tax rate, an FX
  rate, a fee percentage) and returns what was rounded away, so the caller
  can book it.
- `allocate(total: Money, weights: Sequence[int]) -> list[Money]` splits by
  the largest-remainder method; the parts always sum exactly to the total.
  Ties go to the earliest weight, so the result is deterministic.
- Tests use synthetic amounts: VND 1,000,000 split three ways, IDR 0-decimal
  tax at 11%, SGD 9% GST half-even at the 0.5-cent boundary, negative
  amounts (refunds) in every mode, and a property test that `allocate`
  always sums to the total.

## 5. `db` and migrations

- Local development uses the PostgreSQL 18 cluster already running on this
  machine. Tests use a separate database, `mor_test`.
- Settings come from environment variables only (`DATABASE_URL`,
  `ENVIRONMENT`, default `dev`). Code reads the environment once, into a
  `Settings` record, and passes it down. No module reads the environment
  on import.
- The runner applies `migrations/NNNN_name.sql` in order, each in its own
  transaction, and records `(version, name, sha256, applied_at)` in a
  `schema_migration` table. If an applied file's checksum changes, it stops
  with an error: this enforces "never edit an applied migration".
- Persistence functions take `conn` first and never commit (AGENTS.md).
  The test fixture opens one transaction per test and rolls it back.

## 6. `ledger`

Tables (`0001_ledger.sql`):

```sql
CREATE TYPE currency AS ENUM ('VND','IDR','SGD','MYR','THB','PHP','USD','EUR');
CREATE TYPE account_kind AS ENUM (
  'psp_clearing', 'tax_payable', 'duties_payable', 'shipping_payable',
  'duty_variance', 'merchant_payable', 'reserve', 'revenue_mor_fee',
  'rounding');
CREATE TYPE actor_kind AS ENUM ('system', 'staff', 'merchant_api_key');

CREATE TABLE journal_entry (
  id               uuid PRIMARY KEY,          -- uuid7, made in code
  idempotency_key  text NOT NULL UNIQUE,
  description      text NOT NULL,
  effective_at     timestamptz NOT NULL,
  recorded_at      timestamptz NOT NULL,
  actor_kind       actor_kind NOT NULL,
  actor_id         text NOT NULL
);

CREATE TABLE journal_line (
  entry_id      uuid NOT NULL REFERENCES journal_entry(id),
  line_no       int NOT NULL,
  account_kind  account_kind NOT NULL,
  account_scope text NOT NULL DEFAULT '',   -- e.g. merchant id, country, courier
  amount_minor  bigint NOT NULL,             -- debit positive, credit negative
  currency      currency NOT NULL,
  PRIMARY KEY (entry_id, line_no)
);
```

- Balance is enforced in the database, not only in code: a deferred
  constraint trigger checks at commit that each entry's lines sum to zero
  per currency and that an entry has at least two lines.
- Append-only is enforced too: a trigger rejects `UPDATE` and `DELETE` on
  both tables. A correction is a new reversing entry (AGENTS.md: Money and
  Ledger).
- `build_entry(...) -> JournalEntry` checks the same rules in code first.
  An unbalanced entry is a bug in the code that built it, so it raises.
- `record_entry(conn, entry) -> Recorded | AlreadyRecorded`: a repeated
  idempotency key with the same content returns `AlreadyRecorded`; the
  same key with different content raises (a bug, like the API's 409).
- `balance(conn, account, currency) -> Money` sums lines; balances are
  never stored (AGENTS.md).
- `rounding` is an account kind of its own, where remainders from
  `divide` are booked when no better account applies.
- `Actor(kind, id)` is passed into every write, so each change records who
  or what made it (AGENTS.md: idempotency and audit). Time is passed in as
  `now: datetime` (aware UTC), never read inside the function, so tests
  are deterministic.

## 7. `outbox`

Tables (`0002_outbox.sql`):

```sql
CREATE TABLE outbox (
  seq             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id              uuid NOT NULL UNIQUE,
  aggregate_type  text NOT NULL,
  aggregate_id    text NOT NULL,
  message_type    text NOT NULL,      -- past tense: payment_captured
  schema_version  int NOT NULL,
  payload         jsonb NOT NULL,
  created_at      timestamptz NOT NULL,
  published_at    timestamptz
);
CREATE INDEX outbox_unpublished ON outbox (aggregate_type, aggregate_id, seq)
  WHERE published_at IS NULL;

CREATE TABLE inbox (
  consumer    text NOT NULL,
  message_id  uuid NOT NULL,
  handled_at  timestamptz NOT NULL,
  PRIMARY KEY (consumer, message_id)
);
```

- `append(conn, message)` writes in the caller's transaction, next to the
  state change (AGENTS.md: transactional outbox).
- `claim_batch(conn, limit)` takes unpublished rows with `FOR UPDATE SKIP
  LOCKED`, only the oldest unpublished row of each aggregate, so ordering
  holds per aggregate and never relies on global order.
- `mark_published(conn, ids, now)`. `published_at` is the only column
  ever updated; the outbox is delivery bookkeeping, not a financial record.
- `inbox.mark_handled` inserts with `ON CONFLICT DO NOTHING` and returns
  `Handled` or `AlreadyHandled`, which the consumer matches on.
- The relay loop and its transport come in the slice with the first
  consumer; this slice only proves the storage behaviour, including a test
  with two connections claiming at once that never get the same row.

## 8. Rules applied

- Data-oriented design: frozen dataclasses, `StrEnum`, plain functions; no
  classes with behaviour, no ORM, no dependency injection (settings and
  `now` are arguments).
- Typing: pyright strict; results as unions matched exhaustively.
- Errors as values for expected failures (`InvalidAmount`,
  `AlreadyRecorded`, `AlreadyHandled`); raising only for bugs (currency
  mismatch, unbalanced entry, idempotency key reused with other content).
- Money and Ledger: integer minor units with currency, one exponent table,
  explicit rounding with booked remainders, double-entry append-only
  enforced in the database.
- Messaging: outbox in the same transaction, idempotent consumers through
  the inbox, ordering per aggregate.
- Storage: numbered migrations protected by checksums, enum types matching
  closed types in code, no `SELECT *`, callers commit.
- PII: none in this slice; fixtures are synthetic.

## 9. Open questions

1. Database access: may I create a `mor` role and the `mor_dev` and
   `mor_test` databases on the local cluster (`sudo -u postgres createuser
   --createdb mor` or similar), or do you already have a role you use?
2. Ids: uuid7 made in code (Python 3.14 has `uuid.uuid7`), proposed above.
   It keeps ids sortable by time and lets code know an id before inserting.

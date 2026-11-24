# Rules that should be checked, not only written down

Status: survey, 23-Nov-2026. Nothing built. To be picked up in a later
session; items 1 to 3 are small and were recommended to do first, item 4
needs the existing violations fixed. The open rules in §2.4 were decided
on 24-Nov-2026 (shadowing, import aliases, two-column rows), and
`AGENTS.md` Code Style says them.

Why: a rule in prose is easy to forget, and several already are broken in
places (counts below). A check in the tools that run after every change
(`uv run ruff check`, `uv run pyright`, `uv run pytest`, vulture) keeps
a rule kept. This went through every rule in `AGENTS.md` and the shared
conventions (`~/Desktop/conventions/AGENTS.md`, `messaging.md`) and asked
for each: is it checked already, and if not, can a tool check it?

Counts are as of 23-Nov-2026 and go stale: each item names the command
that finds the cases, so run it again before starting.

## 1. Already checked: nothing to do

- Exhaustive `match` over closed sets: pyright's `reportMatchNotExhaustive`
  (`pyproject.toml`).
- Every database enum has a StrEnum with the same values:
  `tests/db/test_enums.py`.
- A new property test or fuzzer joins a soak target:
  `tests/test_soak_targets.py`.
- An applied migration is never edited: the runner stores each file's
  checksum and stops on a change (`src/mor/db/migrate.py`).
- No SQL composed at runtime: psycopg types a query as `LiteralString`
  (or `bytes`, `psycopg.sql`), and pyright strict refuses a string built
  from runtime values. Gaps: `bytes` queries and `psycopg.sql`
  composition are still accepted (neither is used in `src/` today; item
  3 bans `psycopg.sql`).
- Ledger append-only and balanced per currency, idempotency keys unique,
  a delivery never reopened: database triggers and constraints.
- Each consumer locks its row before reading: not checked yet, but
  decided and planned as a declaration the worker applies
  (`plan-messaging-rework.md` §3.3b).

## 2. To codify, most valuable first

### 2.1 "A shape change is a new version" (messaging.md)

Unchecked today: adding a field to `PaymentDeclinedV1` passes every test,
while undelivered messages of the old shape no longer decode as expected.

Check: a snapshot test. Each fact's msgspec JSON schema
(`msgspec.json.schema(record)`), per `(message_type, schema_version)`,
stored in the repo (one file, e.g. `tests/messaging/fact_schemas.json`).
The test fails when an existing version's schema differs from its
snapshot, and when a version has no snapshot (a new version adds one, in
the same change, by an explicit update command, never silently). Removing
a version is allowed only with its transitional comment's condition met
(`AGENTS.md`, Transitional code).

### 2.2 "A record of 4 or more fields is built with keyword arguments" (AGENTS.md)

134 of the 338 frozen dataclasses in `src/` have 4 or more fields (count
by an `ast` walk over `src/`). Positional builds exist; one seen:
`Candidate(...)` with four positional arguments in
`tests/messaging/test_worker.py`.

Check: `@dataclass(frozen=True, kw_only=True)` (and `kw_only=True` on
msgspec structs) on every record of 4 or more fields; pyright then
refuses a positional build. A small test walks `src/` with `ast` and
fails on a record of 4 or more fields without `kw_only`, so a new one
cannot be added without it. Adding `kw_only` will make pyright list the
positional builds to fix.

Measured 24-Nov-2026 in a throwaway worktree: `kw_only=True` on the 134
dataclasses (msgspec structs not counted) gives 90 pyright errors, all
positional builds. 89 are in tests, most in the quote tests
(`tests/quote/`, `tests/checkout/test_quote_*`, the quote and shipping
fuzzers), and 1 is in `src/mor/quote/build.py`. The messaging refactor
does not touch these, so waiting for it saves nothing. Doing this first
means the refactor writes its new records under the check. So it
belongs with the cheap guards, before the refactor
(`plan-architecture.md` §16).

### 2.3 Ruff settings not used yet (config only)

`pyproject.toml` selects `E, F, I, UP, B, DTZ005, DTZ011`.

- **All of `DTZ`** (naive datetimes; the conventions' aware-UTC rule).
  One violation today: `tests/ledger/test_entry.py:70` (`DTZ001`).
  `uv run ruff check --select DTZ src tests`.
- **`TID251` banned APIs**, each with the rule as its message:
  - `typing.TYPE_CHECKING` (conventions: no `TYPE_CHECKING` imports),
  - `typing.TypedDict`, `pydantic` (records are frozen dataclasses or
    msgspec structs),
  - `httpx` (`httpx2`, decided 17-Nov-2026),
  - `functools.partial` (no `partial`-style indirection),
  - `psycopg.sql` (no SQL composed at runtime),
  - `decimal.Decimal` (money and rates are `int` and `Fraction`), with
    one allowed use: `src/mor/checkout/quotes.py` reads `quote_part`'s
    `numeric` columns, which are `numeric` on purpose
    (`migrations/0022_convention_fixes.sql`), and turns them into a
    `Fraction` at once. Allow it by a per-file ignore with that reason.
  None of these is used today except the allowed `Decimal`.
- **All of `A`** (a local, parameter or import named after a builtin;
  part of the no-shadowing rule decided 24-Nov-2026). No violations
  today: `uv run ruff check --select A src tests`.

### 2.4 An ast-grep rule set for what ruff cannot say

A `sgconfig.yml` and `rules/` in the repo, run as one more check
(`ast-grep scan`), with the rule's reason as its message. Each needs its
current cases fixed first. Counts in `src/` unless noted.

| Rule (source) | Cases | Find them |
|---|---|---|
| No import aliases at all, project or third-party (AGENTS.md, decided 24-Nov-2026) | 23 project, 20 `strategies as st`, `src/` and `tests/` | `rg -n '^\s*(from \S+ )?import .* as ' src tests` |
| No local or parameter named after a domain name or a package or module (AGENTS.md, decided 24-Nov-2026; builtins are ruff's `A`, §2.3) | 32 domain names; module names not counted yet | `rg -n '^\s+(payment\|merchant\|invoice\|ledger)\s*(:[^=]+)?=' src` |
| A query of two or more columns is read with `class_row`, never unpacked from a tuple, also in a `for` (AGENTS.md, decided 23-Nov-2026, two columns 24-Nov-2026) | 8 | `rg -n 'for \w+, \w+.* in await cur\.fetchall\(\)\|^\s+\w+, \w+(, \w+)* = row$' src` |
| A row that must exist is read with `mor.db.fetch` (`one_row`, `one_value`), not `fetchone()` and an assert (AGENTS.md) | 4 | `rg -n -A2 'fetchone\(\)' src \| rg 'assert .* is not None'` |
| No reflection (conventions, Explicit over clever) | 1 | `rg -n 'getattr\(\|setattr\(\|importlib' src` |
| A provider is picked by name in one place, its registry (conventions, External Services) | 1 | `rg -n 'match .*\.provider\b' src \| rg -v registry.py` |
| `strftime` only in `src/mor/dates.py` (one formatting helper per audience) | 0 | `rg -n strftime src` |
| `.commit()` never in a persistence module (the caller commits) | 0 | `rg -n '\.commit\(\)' src` |
| `@asynccontextmanager` returns `AsyncGenerator[T, None]` (conventions, Tooling) | not counted | ast-grep on decorated functions |
| No `case _: assert_never(...)` fallback (AGENTS.md, decided 23-Nov-2026) | 0 | `rg -n assert_never src` |

What the cases are:

- **Aliased imports**: e.g. `from mor.payments.inbound import store_inbox
  as store_psp_inbox` and `from mor.psp import registry as psp_registry`
  (`src/mor/web/webhooks.py`), `from mor.checkout import addresses as
  fields` (`src/mor/web/buyer_html.py`), `from mor.markets.fx import rate
  as fx_rate` (`src/mor/web/staff_markets.py`), `Stale as StaleEvent`
  (`src/mor/payments/inbound.py`), and `addresses as f` in three tests.
  Most exist because two modules give the same name (the many `flow`
  modules imported into `src/mor/messaging/consumers.py`); the fix is a
  dotted import (`import mor.payments.flow`), as the conventions show.
  The 20 `from hypothesis import strategies as st` become `from
  hypothesis import strategies`: no exceptions, decided 24-Nov-2026.
- **Shadowed domain names**: mostly `merchant = await read_merchant(...)`
  and `invoice = await invoice_for_order(...)` in web handlers
  (`order_pages.py`, `staff_markets.py`, `api.py`, …) and `payment` in
  the fake PSP (12). Decided 24-Nov-2026: all of them go, since one
  name meaning two things is confusing; the new name says the value's
  role (`seller`, `paid_invoice`), not `_row` or `_record`.
- **Tuple-unpacked rows**: in real code `src/mor/checkout/sessions.py`,
  `src/mor/checkout/quotes.py`, `src/mor/psp/accounts.py`,
  `src/mor/echo/fake_client.py`; the rest in the fakes. Two-column rows
  read in a `for` are the common case; decided 24-Nov-2026 that the
  rule covers them, each with its own small row record.
- **`fetchone()` and an assert**: both inbox decoders
  (`src/mor/payments/inbound.py`, `src/mor/echo/inbound.py`),
  `src/mor/seed.py`, `src/mor/fakes/echo.py`.
- **Reflection**: `src/mor/markets/changes.py` compares two records field
  by field with `getattr`. Explicit comparisons per record, or a field
  list as data, would replace it.
- **Provider by name outside a registry**: `src/mor/echo/flow.py`
  (`send_guard(account.provider)`); echo is transitional (retired with
  the shipment slice), so this may go with it rather than be fixed.

### 2.5 Found while looking, not a rule check

- The payment fuzzer's meet-again rule could reach the
  `session_payment_ended` bug fixed on 23-Nov-2026 but did not: a second
  attempt open while the first one's end is met again is rare there. A
  milestone for it would show the soak reaching it
  (`plan-messaging-rework.md` §1).

## 3. Not worth a check

- Dates in docs (`01-Jan-2027`): no violations in Markdown prose; the
  ISO dates found are in CSV data under `docs/markets/`, where machines
  keep ISO.
- `float` for money: none in `src/`; the one `float(` formats a CSS width
  (`src/mor/web/staff_quote_html.py`).
- Divide by 100 for minor units: none; the `/ 100` cases turn percentages
  into `Fraction` rates (`src/mor/markets/source.py`,
  `src/mor/duties/port.py`).
- "Every state change records who and when", "errors as values, raise
  only on bugs", "facts in the past tense": they depend on meaning, not
  syntax; review and the fuzzers cover them.

## 4. Order of work (recommended)

1. §2.3 (config only), then §2.1 and §2.2, each with its check clean.
2. Fix the cases of each rule in §2.4 (decided 24-Nov-2026) and add
   the ast-grep rules, one rule per change, so each lands clean.
3. Add the ast-grep scan to `AGENTS.md`'s checks list, as vulture and
   `npx tsc` are.

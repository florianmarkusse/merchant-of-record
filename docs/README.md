# Where we are, where we are going

The one place for the build's status and what comes next. The plans
describe the design as it is now: when something changes, the plan it
changes is rewritten and the decisions it overturns are updated, never
amended by a newer page. Git holds the history.

Status as of 24-Nov-2026.

## Built

| Slice | What | Plan |
|---|---|---|
| 1 | Skeleton: `money`, `db` and migrations, `ledger` | (no plan of its own; `AGENTS.md`, Money and Ledger) |
| 2 | Messaging: outbox, deliveries, the worker, the consumer table, effect consumers, inbound on one path; the probe and the echo flow | `plan-messaging.md` |
| 3 | The test setup on the laptop and the nightly soak | `plan-test-setup.md` |
| 4 | Market data, the tax path, duty rates and carrier prices from switchable sources, `build_quote`, merchants and the API, the buyer's checkout up to a quote | `plan-quote.md`, `plan-markets-data.md` |
| 5a–5c-1 | Payments against the fake PSP in every mode, the order, the invoice, settlement with each payout reconciled | `plan-payments.md` |

The checks that run after every change (ruff, pyright strict, vulture,
`npx tsc`, the ast-grep rules, pytest with the fuzzers) are in
`AGENTS.md`, Checks.

## Next, in this order

1. **5c-2, the bank statement**: designed first. A fake bank and the
   import of its statements, matching each PSP payout to its credit,
   moving it from `payout_in_transit` to `bank:{entity}`, and flagging a
   payout whose credit arrived but whose lines do not add up
   (`plan-payments.md` §1, §7).
2. **5c-3**: the fake PSP's card fields and the Playwright smoke suite
   (`plan-architecture.md` §10).

Then the slices of the whole flow (`plan-architecture.md` §2), each
designed first where it adds tables or message shapes:

6. Shipments against the fake carrier in every mode: booking, tracking,
   milestones (`plan-architecture.md` §7, §9; `plan-logistics.md`). The
   echo flow is retired with it, and with it the echo ignores of the
   `typed-ids` ast-grep rule.
7. Carrier invoice import and its ledger entries.
8. Returns and refunds, chargebacks (`plan-psp-integration.md` §5).
9. Payout release and payout runs against the fake rail
   (`plan-psp-integration.md` §7).
10. The first real carrier module, once the quotes decide which
    (`plan-logistics-next.md`).

Slices of their own, placed when needed:

- **Staff sign-in** (staff accounts, password plus a second factor,
  sessions with a SameSite cookie, the person recorded as actor), before
  the first prod deploy; until then staff pages are mounted outside prod
  only.
- **Notify**: buyer emails and merchant webhooks together, with a fake
  mailbox (`plan-payments.md` §11, `plan-psp-integration.md` §6).
- **Deploy**, once a VPS exists (`plan-test-setup.md` §6): the deploy
  script (with Pango for WeasyPrint), nginx with TLS and per-IP and
  per-key request limits, the heartbeat, and a prod database role
  without `TRUNCATE` on ledger tables.
- **Merchant surfaces**: payment links and products, the rest of the API,
  the dashboard (`plan-merchant-surfaces.md`).

## Waiting until needed

- Messaging: the message's own uuid, version draining, replay
  (`plan-messaging.md` §12).
- `src/mor/markets/source.py` decoded with msgspec structs instead of
  hand-parsed files with `isinstance` checks.
- **To decide**: reflection in `src/mor/markets/changes.py`, which
  compares 17 record types field by field with `getattr` for the market
  change report. Explicit field lists per record (about 200 lines, with a
  test that each list names every field), or a documented exception.
  No ast-grep rule until decided.
- The payment fuzzer could meet an earlier attempt's end again while a
  second attempt is open, which once hid a bug; a reach milestone for it
  would show the soak reaching it.

## Decisions waiting on people

- The legal entity, SG or Dutch BV: it blocks PSP contracts, carrier
  accounts, registrations and licensing (`plan-psp-integration.md` §10).
- Carrier quotes and accounts (`plan-logistics-next.md`), the v1 country
  list and the returns floor (`plan-logistics.md` §11).
- Advisers: customs, tax, VN export, payment licences, consumer law
  (the compliance sections of each plan).

## The documents

**The design, current**:

- `plan-architecture.md`: the whole flow, locks, messaging in short,
  ports and fakes, logistics in detail, testing and fuzzing.
- `plan-messaging.md`: facts, deliveries, the worker, the consumer
  table, effect consumers, inbound.

**Built slices**, each describing what exists:

- `plan-test-setup.md`, `plan-quote.md`, `plan-markets-data.md`,
  `plan-payments.md`.

**Input for slices not built yet**:

- `plan-psp-integration.md`: choosing PSPs, stored cards, refunds and
  disputes, merchant webhooks, payouts, Shopify.
- `plan-merchant-surfaces.md`: payment links, the rest of the API, buyer
  pages, the dashboard, other channels.
- `plan-logistics.md`: countries, the tax path rule, carriers, returns,
  registrations, launch.
- `plan-logistics-next.md`: the carrier-sourcing worklist.

**Research** (dated desk research, not decisions):
`research-logistics.md` and `research-product-compliance.md` with their
sources in `research/`, and their summaries `logistics.html` and
`product-compliance.html`. `markets/` holds the market and carrier data
(`plan-markets-data.md`); `markets.html` is generated from it.

Applied migrations are never edited, so their comments cite docs by the
names they had then (`plan-messaging-rework.md`,
`plan-settlement-reconciliation.md`, `plan-skeleton.md`) and say
"courier" where the code now says "carrier" (`0028_carrier_names.sql`);
git history has those files.

An explainer page with worked examples of the design was published as a
private artifact, "MoR Flow Architecture": a reading aid, possibly
behind the design; the plans are the source.

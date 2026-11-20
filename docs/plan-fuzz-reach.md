# Fuzzing reach: deep enough, and the whole flow

Status: agreed and built 20-Nov-2026. Amends plan-architecture §10
(fuzzing) and `docs/plan-payments.md` §8. Read §4 before reaching for
`hypothesis.target`.

## 1. The problem, measured

The payment fuzzer (one Hypothesis state machine from the quote to the
invoice) was checked for what its runs reached, 20-Nov-2026, 300 examples
(351 runs):

- 59% of runs never made a payment attempt;
- 2.5% reached a paid order before a "pays and approves" action was added
  in 5b, 12% after it;
- no run ever had a declined payment, and none paid both its sessions.

Every invariant held, but over states the runs mostly never reached. A
green fuzzer said little about everything after the capture, and
settlement (5c) would add another stage behind it.

## 2. How others handle it

- **Swarm testing** (Groce et al., ISSTA 2012): each run switches a random
  subset of features off, so runs differ in kind, not only in order.
  Hypothesis does this for state machine rules already (`FeatureFlags` in
  `hypothesis.stateful`). Part of our "no attempt" share is swarm turning
  the pay rules off, which is wanted.
- **Workloads plus faults** (FoundationDB's simulation): a test is a
  workload that drives the system somewhere useful, with fault injection
  (`BUGGIFY`, each site on or off for a whole run) layered on top. The
  workload gets depth, the faults get breadth.
- **Profiles** (TigerBeetle's VOPR): separate simulator modes (safety and
  liveness; swarm and low-fault profiles), each tuned to reach its kind of
  state, instead of one mode that tries everything.
- **Targeted property-based testing** (Löscher and Sagonas, ISSTA 2017;
  `hypothesis.target`): the test reports a number and Hypothesis searches
  toward inputs that raise it. On a toy state machine it reached depth 16
  where chance gives about 5; on ours it was unstable (§4).
- **Start where it gets interesting** (snapshot fuzzing): set the system
  up in a deep state cheaply, then fuzz from there.

## 3. What we do

1. **Two machines, split at the capture.**
   - The payment machine (`tests/flow/test_payment_flow_fuzz.py`) keeps
     everything up to the capture: pricing, paying, the PSP's answers,
     faults, crashes, the clock, and what crosses into the order (a late
     webhook, expiry while paying). It still drains to the invoice and
     checks it.
   - The order machine (`tests/flow/test_order_flow_fuzz.py`) starts from
     one to three payments the buyer approved at the PSP, with everything
     after that still to run: the webhook's decoding, the capture, the
     order, the invoice. Its actions are the delivery machinery's own
     faults: a delivery run, a crash inside a local step or around an
     effect's call, a handled fact met again by its consumer past the
     inbox and committed (as a broker's redelivery could), the PSP sending
     every event again, the clock passing the session's life. Settlement,
     refunds and payouts join this machine as they are built.
   - The rules both check live in one module
     (`tests/flow/payment_checks.py`).
2. **Structure, not search, for reach.**
   - "Pays and answers": pay, the worker makes the payment, the buyer
     approves, declines, lets it expire, chooses another way, or leaves.
   - The clock's jumps past a quote's hold or a session's life wait for a
     first attempt: early, they left nothing payable for the rest of the
     run.
   - Up to two PSP faults are planned for the whole run at setup, as
     `BUGGIFY` sites are on or off per run, so a fault on the one call
     that matters is common.
   - A rule for a moment that is short (crash around a capture) has a
     precondition that the moment is now: Hypothesis draws only among
     rules whose preconditions hold.
   - Swarm testing stays on.
3. **Reach is measured, and has a floor** (`tests/flow/reach.py`). Each
   fuzzer lists its milestones (fact types in the outbox before the final
   drain, plus what its actions met) and writes a reach table to
   `.hypothesis/reach/`; the soak appends it to the target's log. With
   500 runs or more, a milestone reached by fewer than 1 run in 200 fails
   the test, so a drop in reach alerts like a bug. The floor found the
   rare capture crash on its first run.

## 4. What was tried and dropped: `target()`

Measured 20-Nov-2026, 300 examples, two seeds each (shares of runs):

| | captured | declined | cancelled | needs a person |
|---|---|---|---|---|
| one `target()` score, seed 1 | 2.5% | 60% | 0% | 60% |
| one `target()` score, seed 2 | 60% | 58% | 55% | 0.3% |
| no `target()`, seed 1 | 12% | 5.3% | 6.6% | 0.9% |
| no `target()`, seed 2 | 9.8% | 3.9% | 3.3% | 0.9% |

A single score makes each run's search climb toward whatever it found
first and starves the rest; which milestones starve depends on the seed.
One label per milestone (scored by its count) did no better, and on its
second seed Hypothesis 6.170.17's own optimiser crashed (an `IndexError`
in `hill_climb`, inside Hypothesis). Without `target()`, every milestone
is reached in every seed, and depth after the capture comes from the order
machine instead.

## 5. Results (20-Nov-2026, 500 examples)

- Payment machine, 550 runs: an attempt 56%, captured 6.9%, declined
  9.8%, expired 8.0%, cancelled 5.5%, waiting on a person 1.1%, the
  session expired 27.5%. Before: attempts 41%, captured 12% only with the
  happy-path action and 2.5% without, never a decline.
- Order machine, 565 runs: an order 70%, an invoice 68%, several orders
  49%, a crash in `order_create` 1.6% and in `invoice_issue` 2.8%, around
  a capture 3.4%, a consumer met again 27%, events sent again after an
  order 32%.
- Planted bugs: an invoice number committed apart from its invoice, which
  the old single machine missed at 1,000 examples, the order machine
  finds at 300 (a gap: numbers 2, 3), not at the 30 of `uv run pytest`.
  Expiring a session while it is being paid, a 5a plant, the payment
  machine still finds at 300.
- Found while building, both by design and now stated in the checks:
  decoding a stored webhook again emits its events again (the guard is the
  vendor's event id, plan-messaging §12.2); a crash around a capture on an
  account with no duplicate guard leaves the attempt with a person.

## 6. Rules this applies

The conventions' Fuzzing section: actions include crashes at each commit
boundary and duplicates; outcomes depend only on what Hypothesis drew; a
found failure is pinned; a new fuzzer joins a soak target with a timed
budget; check the fuzzer by putting a fixed bug back.

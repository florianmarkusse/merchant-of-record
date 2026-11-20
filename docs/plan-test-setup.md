# Slice 3: the test setup

Confirmed 17-Nov-2026. Implements plan-architecture.md §6 ("the test
setup") and §14 step 3: a running copy with `ENVIRONMENT=test`, web (with
the fakes under `/_fake/`) and worker, its own database with synthetic
data only, where the echo flow can be walked by hand in every mode and the
long fuzz runs happen.

## 1. Where it runs

- **On the developer's laptop for now** (decided 17-Nov-2026: there is no
  server yet). Web on `127.0.0.1:8741`, bound to loopback only, so
  nothing is reachable from outside and the page needs no login yet.
- Database `mor_sandbox`, never `mor_dev` (local development) or
  `mor_test` (dropped by every pytest run). The soak uses `mor_soak`.
- **Deferred until a VPS exists:** `deploy/deploy.sh` (bootstrap, deploy,
  env, status), nginx with TLS on a subdomain, a login for the page
  (nginx basic auth; merchants will need one), the Healthchecks.io
  heartbeat. A heartbeat from a laptop that is off at night would only
  raise false alarms. The shape follows fruggals' `deploy/`.

## 2. Running it for real

- `python -m mor.web.serve`: uvicorn serving `make_app`, fakes mounted
  outside prod (refused in prod, as `make_app` already does).
- Setting `SELF_URL` (e.g. `http://127.0.0.1:8741`): the fake's webhooks
  go there over real HTTP, and seeded accounts reach the fakes at
  `SELF_URL/_fake/…`, as plan-architecture §6 says ("the worker reaches
  them at the dev server's own address").
- Two processes, as in prod: the server and `python -m
  mor.messaging.worker`. On the laptop they run as systemd user units.
- Outside prod both restart when a `.py` file under `src/` changes
  (19-Nov-2026): the server through uvicorn's `--reload` (with an app
  factory, `create_app`), the worker under `watchfiles.run_process`. A
  restart is a crash the worker is built to survive. A reload does not
  migrate, load markets or seed: run those by hand after a change that
  needs them.

## 3. Seed

`python -m mor.seed`, refused in prod: one echo account per provider and
mode, the push accounts' webhooks subscribed at the fake, the report
schedule started for `file` accounts. Synthetic data only. Running it again
changes nothing: insert, and a conflict means it was done.

## 4. The page

One HTML page under `/_fake/`, built with htpy, working without
JavaScript. Per account (provider, mode) its jobs, each job's state,
vendor status and recorded events, with form buttons:

- vendor side (under `/_fake/`): release the next event, release twice,
  release with a bad signature, redeliver failed webhooks, cut a report,
  script a fault;
- our side: create a job, record an event by hand (as a staff actor).

Every POST is guarded against cross-site forgery: its `Origin` must name
the host it was sent to. There is no login or session cookie yet, so a
`SameSite` cookie would guard nothing; it comes with the login. Nothing of
it exists in prod.

## 5. Nightly soak

`deploy/soak.sh` (built 17-Nov-2026), run by a systemd user timer
(`deploy/mor-soak.timer`, 02:00, `Persistent=true`, so a night the laptop
was off runs at the next start; niced and on idle I/O, since that start
may be while it is in use):

- It tests the last commit on `main`, in a git worktree of its own under
  `~/.local/state/mor-soak/`, never the working tree, so a half-edited
  file is never what runs at night.
- Its own databases, `mor_soak` and `mor_soak_migrations`
  (`MOR_TEST_DATABASE=mor_soak`, a closed choice in `tests/helpers.py`),
  and its own `.hypothesis/`, so a pytest run by hand never drops a
  database under it.
- Targets, as plain data in the script: one per flow fuzzer, and one for
  the pure properties. Each gets an example budget (`SOAK_EXAMPLES`,
  read by the soak profile) sized from a measured run (the script's
  comment has the figures), stops at its first failure, and has a time
  limit only as a guard against a hang. The targets are read from the
  tested commit's copy of the script, not from the working tree's, which
  is what the timer runs (20-Nov-2026: targets the working tree named for
  uncommitted test files broke two targets against `main`).
- Every test file that uses Hypothesis is in a target, checked by
  `tests/test_soak_targets.py` (19-Nov-2026: the duty and shipping-price
  fuzzers and four property files had been added since the soak without
  joining it).
- A target is judged by its log: a `FAILED` line is a failure, else exit 0
  is a pass, else the run broke. Failures and broken runs send one
  Telegram message to `TELEGRAM_CHAT_ID_ALERTS` (token and chat id from
  `.env`, never committed): the commit, the failing test, its first
  assertion line and the log's path. Nothing is sent on a pass.
- `deploy/soak.sh test-alert` sends one test message; `SOAK_QUICK=1`,
  `SOAK_DRY_ALERT=1` and `SOAK_REF=<commit>` are for checking the soak
  itself.

What building it showed:

- Ending targets by a time limit was wrong twice over. A limit that cut
  pytest off lost failures it had already found (a planted money bug
  showed four `F`s and counted as a pass), and a Ctrl-C at the limit did
  not reliably stop the fuzzers. Hence example budgets.
- The fuzzers' "no advisory lock left held" checks counted locks on the
  whole PostgreSQL cluster, so a lock the sandbox's worker held in its
  own database failed the probe fuzzer by chance. They now count only the
  test's own database; `tests/test_helpers.py` pins it.

## 6. Build order

1. Serve, `SELF_URL`, seed; checked with the two processes running.
2. The page.
3. The soak timer and its Telegram alert.
4. When a VPS exists: `deploy/`, nginx, TLS, login, heartbeat.

## 7. Rules this applies

One environment setting decides fakes or real; registries refuse real
providers outside prod. Time only starts the soak. Seed and page are
idempotent; forms are answered with pages; every POST is guarded. No real
data anywhere. Server config, once there is a server, lives in the repo.

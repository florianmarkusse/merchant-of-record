# The test setup

Status: built 17-Nov-2026 (slice 3) on the laptop; the deploy waits for a
VPS (§1). A running copy with `ENVIRONMENT=test`: web (with the fakes
under `/_fake/`) and worker, its own database with synthetic data only,
where every flow can be walked by hand in every mode and the long fuzz
runs happen (`plan-architecture.md` §6).

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

`python -m mor.seed`, refused in prod, with the server running: one echo
account per provider and event mode (push accounts subscribe their
webhooks at the fake), an account at the fake duty vendor and the fake
carrier for every vendor the market data names, one fake PSP account per
wiring with a default route, and the Sandbox Shop merchant with its API
key. Synthetic data only. Running it again changes nothing: insert, and a
conflict means it was done.

## 4. The page

One HTML page under `/_fake/`, built with htpy, working without
JavaScript. Per echo account (provider, mode) its jobs, each job's state,
vendor status and recorded events, and per PSP account a settlement cut,
with form buttons:

- vendor side (under `/_fake/`): release the next event, release twice,
  release with a bad signature, redeliver failed webhooks, cut a report,
  cut a settlement with a chosen fault, script a fault;
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
  limit only as a guard against a hang. Ending a target by its time
  limit instead lost failures pytest had already found, and a Ctrl-C did
  not reliably stop the fuzzers.
- The targets are read from the tested commit's copy of the script, not
  from the working tree's, so a target for an uncommitted test file
  cannot break a run against `main`.
- Every test file that uses Hypothesis is in a target, checked by
  `tests/test_soak_targets.py`.
- A fuzzer's "no advisory lock left held" check counts only the test's
  own database (`tests/test_helpers.py` pins it): other databases on the
  cluster (the sandbox's worker) hold locks of their own.
- A target is judged by its log: a `FAILED` line is a failure, else exit 0
  is a pass, else the run broke. A failed or broken target sends a
  Telegram message to `TELEGRAM_CHAT_ID_ALERTS` as soon as it ends (token
  and chat id from `.env`, never committed): the commit, the failing
  test, its first assertion line and the log's path. Nothing is sent on a
  pass.
- A run that ends any other way (stopped, killed, the script failing)
  sends one message on its way out, naming the target it was in. The
  systemd unit has no time limit of its own; the targets' guards bound
  the run. A unit limit that went stale cut every run short before its
  alerts for five nights (21-Nov-2026 to 25-Nov-2026), so nothing waits
  for the end of the run to alert.
- `deploy/soak.sh test-alert` sends one test message; `SOAK_QUICK=1`,
  `SOAK_DRY_ALERT=1` and `SOAK_REF=<commit>` are for checking the soak
  itself.

## 6. Still to build, when a VPS exists

`deploy/`, nginx, TLS, a login for the page, the heartbeat (§1).

## 7. Rules this applies

One environment setting decides fakes or real; registries refuse real
providers outside prod. Time only starts the soak. Seed and page are
idempotent; forms are answered with pages; every POST is guarded. No real
data anywhere. Server config, once there is a server, lives in the repo.

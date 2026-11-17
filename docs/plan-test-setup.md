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

Every POST is guarded against cross-site forgery (origin check plus a
`SameSite` cookie). Nothing of it exists in prod.

## 5. Nightly soak

A systemd user timer runs `HYPOTHESIS_PROFILE=soak pytest` against
`mor_soak` with a time limit; the limit ending the run is a pass. A failure
keeps the falsifying example and sends a Telegram message (bot token and
chat id from `.env`, never committed). On the laptop the timer is
`Persistent=true`, so a night with the laptop off runs at the next start.

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

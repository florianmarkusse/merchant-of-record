-- The echo flow: a dev/test-only aggregate whose one effect consumer calls a
-- fake echo vendor, to prove prepare / call / complete and the duplicate
-- guards (docs/plan-messaging.md §10). Transitional: dropped by a later
-- migration once the fake PSP exercises the same machinery (slice 5). The
-- enum values added below then stay in the database: PostgreSQL cannot drop
-- an enum value without rebuilding the type.

ALTER TYPE aggregate_type ADD VALUE 'echo_job';
ALTER TYPE message_type ADD VALUE 'echo_job_requested';
ALTER TYPE message_type ADD VALUE 'echo_job_echoed';
ALTER TYPE message_type ADD VALUE 'echo_job_refused';
ALTER TYPE message_type ADD VALUE 'echo_job_needs_person';
ALTER TYPE consumer_name ADD VALUE 'echo_send';

CREATE TYPE echo_provider AS ENUM ('fake_keyed', 'fake_cancellable', 'fake_plain');

-- deduplicating: the vendor answered, but a resend may have left extra
-- echoes, which are being cancelled (compensable guard only).
CREATE TYPE echo_job_state AS ENUM (
  'requested', 'deduplicating', 'echoed', 'refused', 'needs_person'
);
CREATE TYPE echo_operation AS ENUM ('send', 'cancel');
CREATE TYPE vendor_request_state AS ENUM ('sent', 'succeeded', 'refused', 'not_sent');

CREATE TABLE echo_account (
  id          uuid PRIMARY KEY,
  provider    echo_provider NOT NULL,
  base_url    text NOT NULL,
  timeout_ms  int NOT NULL CHECK (timeout_ms > 0)
);

CREATE TABLE echo_job (
  id              uuid PRIMARY KEY,
  account_id      uuid NOT NULL REFERENCES echo_account (id),
  message         text NOT NULL,
  state           echo_job_state NOT NULL,
  vendor_echo_id  text,
  refusal         text,
  created_at      timestamptz NOT NULL,
  updated_at      timestamptz NOT NULL
);

-- One row per call that may do work at the vendor: the committed "we are
-- about to ask" mark. A `sent` row with no result is a call that may have
-- happened; the provider's duplicate guard decides what follows.
CREATE TABLE echo_request (
  id               uuid PRIMARY KEY,
  echo_job_id      uuid NOT NULL REFERENCES echo_job (id),
  operation        echo_operation NOT NULL,
  number           int NOT NULL CHECK (number >= 1),
  idempotency_key  text NOT NULL UNIQUE,      -- kept for good
  state            vendor_request_state NOT NULL,
  sends            int NOT NULL DEFAULT 0,    -- times marked sent; >1 may duplicate
  target_echo_id   text,                      -- the echo a cancel is for
  result_echo_id   text,                      -- the echo a send made
  refusal          text,
  created_at       timestamptz NOT NULL,
  completed_at     timestamptz,
  UNIQUE (echo_job_id, operation, number),
  CHECK ((operation = 'cancel') = (target_echo_id IS NOT NULL))
);

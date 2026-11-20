-- The fake PSP's own state (docs/plan-payments.md §2), in the fake_vendor
-- schema. Our code never reads it; tests inspect it as evidence of what the
-- PSP was asked to do.

CREATE TYPE psp_fake_capture AS ENUM ('sale', 'authorise_capture');
CREATE TYPE psp_fake_payload AS ENUM ('full', 'thin');
CREATE TYPE psp_fake_status AS ENUM
  ('requires_action', 'authorised', 'captured', 'declined', 'expired', 'cancelled');
CREATE TYPE psp_fake_event_kind AS ENUM
  ('authorised', 'captured', 'declined', 'expired', 'cancelled');
CREATE TYPE psp_fake_push AS ENUM ('not_subscribed', 'delivered', 'failed');
CREATE TYPE psp_fake_operation AS ENUM ('create', 'capture', 'cancel', 'get');
-- `decline`: the next create is declined at once, as an issuer would.
CREATE TYPE psp_fake_fault AS ENUM
  ('fail_500', 'fail_500_after_work', 'timeout_after_work', 'decline');

-- An account as set up on the PSP's dashboard: how it captures, whether it
-- keeps idempotency keys (for good), and where it pushes webhooks.
CREATE TABLE psp_fake_account (
  name             text PRIMARY KEY,
  capture          psp_fake_capture NOT NULL,
  keyed            boolean NOT NULL,
  webhook_url      text,
  webhook_secret   text,
  webhook_payload  psp_fake_payload,
  CHECK ((webhook_url IS NULL) = (webhook_secret IS NULL)),
  CHECK ((webhook_url IS NULL) = (webhook_payload IS NULL))
);

-- Every payment the PSP ever made. Amounts in the PSP's own minor units
-- (ISO 4217: IDR has 2 here, where we use 0).
CREATE TABLE psp_fake_payment (
  id               text PRIMARY KEY,
  account          text NOT NULL REFERENCES psp_fake_account (name),
  reference        text NOT NULL,
  idempotency_key  text,
  amount_minor     bigint NOT NULL,
  currency         char(3) NOT NULL,
  method           text NOT NULL,
  status           psp_fake_status NOT NULL,
  return_url       text NOT NULL,
  qr_expires_at    timestamptz,
  created_at       timestamptz NOT NULL
);
CREATE INDEX psp_fake_payment_by_reference ON psp_fake_payment (account, reference);
-- A keyed account's promise: one payment per key, for good.
CREATE UNIQUE INDEX psp_fake_payment_key ON psp_fake_payment (account, idempotency_key);

-- Capture keys a keyed account has seen, for good.
CREATE TABLE psp_fake_capture_key (
  account          text NOT NULL REFERENCES psp_fake_account (name),
  idempotency_key  text NOT NULL,
  payment_id       text NOT NULL REFERENCES psp_fake_payment (id),
  PRIMARY KEY (account, idempotency_key)
);

-- Events in release order. `push` is what happened to its webhook.
CREATE TABLE psp_fake_event (
  seq          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id           text NOT NULL UNIQUE,
  payment_id   text NOT NULL REFERENCES psp_fake_payment (id),
  kind         psp_fake_event_kind NOT NULL,
  amount_minor bigint NOT NULL,
  occurred_at  timestamptz NOT NULL,
  push         psp_fake_push NOT NULL
);

-- Money the PSP moved, one row per capture: the fuzzer's proof that
-- nothing was charged twice.
CREATE TABLE psp_fake_charge (
  seq          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  payment_id   text NOT NULL UNIQUE REFERENCES psp_fake_payment (id),
  reference    text NOT NULL,
  amount_minor bigint NOT NULL,
  currency     char(3) NOT NULL
);

-- Faults scripted for the next calls of an operation, oldest first.
CREATE TABLE psp_fake_fault_next (
  seq        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  account    text NOT NULL,
  operation  psp_fake_operation NOT NULL,
  fault      psp_fake_fault NOT NULL
);

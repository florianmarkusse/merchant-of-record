-- Settlement, step 5c-1 (docs/plan-payments.md §7.1): what the PSP says it
-- settled and paid out, each batch and line once by the PSP's own id,
-- booked against the capture, and what did not fit, for a person.

-- How settlement reaches us: a report file per batch, or a batch read page
-- by page through an API. Both are reads, so neither needs a guard.
CREATE TYPE psp_settlement_mode AS ENUM ('file', 'api');

-- Existing rows (the test setup's accounts) settle by file; every new row
-- names its mode.
ALTER TABLE psp_account
  ADD COLUMN settlement_mode psp_settlement_mode NOT NULL DEFAULT 'file',
  ADD COLUMN settlement_interval_s int NOT NULL DEFAULT 3600
    CHECK (settlement_interval_s > 0);
ALTER TABLE psp_account
  ALTER COLUMN settlement_mode DROP DEFAULT,
  ALTER COLUMN settlement_interval_s DROP DEFAULT;

-- Where an account's reading stands. The row is made when its looks start:
-- its insert is the guard against starting them twice. `last_read` is the
-- last report or batch read whole; `pending` the one being read, and for
-- an API batch `pending_page` its next page.
CREATE TABLE psp_settlement_cursor (
  account_id    uuid PRIMARY KEY REFERENCES psp_account (id),
  last_read     text,
  pending       text,
  pending_page  int CHECK (pending_page >= 1),
  started_at    timestamptz NOT NULL,
  CHECK (pending IS NOT NULL OR pending_page IS NULL)
);

-- One payout: one bank transfer, in one currency. Amounts are exact in our
-- minor units (numerator / denominator: a PSP may count finer than we do,
-- IDR at 2 decimals) and rounded half-even.
CREATE TABLE settlement_batch (
  id               uuid PRIMARY KEY,
  account_id       uuid NOT NULL REFERENCES psp_account (id),
  vendor_batch_id  text NOT NULL,
  currency         currency NOT NULL,
  net_numerator    bigint NOT NULL,
  net_denominator  bigint NOT NULL CHECK (net_denominator > 0),
  net_minor        bigint NOT NULL,
  paid_out_at      timestamptz NOT NULL,
  inbox_id         uuid NOT NULL REFERENCES psp_inbox (id),
  recorded_at      timestamptz NOT NULL,
  booked_at        timestamptz,  -- its payout entry is in the ledger
  UNIQUE (account_id, vendor_batch_id)
);

CREATE TYPE settlement_line_type AS ENUM ('capture', 'unknown');
-- `waiting`: its payment is not in our books yet (settlement got ahead of
-- the webhook); it is booked when the order is made.
CREATE TYPE settlement_line_state AS ENUM
  ('received', 'waiting', 'booked', 'exception');

CREATE TABLE settlement_line (
  id                   uuid PRIMARY KEY,
  batch_id             uuid NOT NULL REFERENCES settlement_batch (id),
  account_id           uuid NOT NULL REFERENCES psp_account (id),
  vendor_line_id       text NOT NULL,
  type                 settlement_line_type NOT NULL,
  vendor_type          text NOT NULL,  -- as the PSP wrote it
  provider_payment_id  text NOT NULL,
  our_reference        text NOT NULL,
  currency             currency NOT NULL,
  gross_numerator      bigint NOT NULL,
  gross_denominator    bigint NOT NULL CHECK (gross_denominator > 0),
  gross_minor          bigint NOT NULL,
  fee_numerator        bigint NOT NULL,
  fee_denominator      bigint NOT NULL CHECK (fee_denominator > 0),
  fee_minor            bigint NOT NULL,
  rounding             rounding_mode NOT NULL,
  attempt_id           uuid REFERENCES payment_attempt (id),
  state                settlement_line_state NOT NULL,
  recorded_at          timestamptz NOT NULL,
  updated_at           timestamptz NOT NULL,
  UNIQUE (account_id, vendor_line_id),
  CHECK (state NOT IN ('waiting', 'booked') OR attempt_id IS NOT NULL)
);
CREATE INDEX settlement_line_by_batch ON settlement_line (batch_id);
CREATE INDEX settlement_line_by_attempt ON settlement_line (attempt_id);
-- A payment is settled once. A second line for it is an exception.
CREATE UNIQUE INDEX settlement_line_one_booked_capture ON settlement_line (attempt_id)
  WHERE type = 'capture' AND state = 'booked';

CREATE TYPE settlement_exception_kind AS ENUM (
  'unknown_reference',  -- no payment of ours by that reference and id
  'not_captured',       -- the payment ended otherwise, or is with a person
  'amount_differs',     -- the gross is not what we captured
  'currency_differs',   -- the line's currency is not the payment's or batch's
  'settled_twice',      -- a second line for a payment already settled
  'unknown_type',       -- a line type we do not book (yet)
  'net_differs'         -- the batch's net is not what its lines add up to
);

-- What did not fit, for a person. A line or a batch has at most one: its
-- insert is the guard against recording it twice. Nothing was booked for a
-- line with an exception; a batch's payout is booked as the PSP reported it.
CREATE TABLE settlement_exception (
  id          uuid PRIMARY KEY,
  account_id  uuid NOT NULL REFERENCES psp_account (id),
  line_id     uuid UNIQUE REFERENCES settlement_line (id),
  batch_id    uuid UNIQUE REFERENCES settlement_batch (id),
  kind        settlement_exception_kind NOT NULL,
  detail      text NOT NULL,
  created_at  timestamptz NOT NULL,
  CHECK ((line_id IS NULL) <> (batch_id IS NULL))
);

CREATE TRIGGER settlement_exception_append_only
  BEFORE UPDATE OR DELETE ON settlement_exception
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

-- A person looked and wrote down what they found (question 10 of
-- 20-Nov-2026). It books nothing; a correcting entry is made by hand.
CREATE TABLE settlement_exception_resolved (
  exception_id  uuid PRIMARY KEY REFERENCES settlement_exception (id),
  note          text NOT NULL CHECK (btrim(note) <> ''),
  resolved_at   timestamptz NOT NULL,
  actor_kind    actor_kind NOT NULL,
  actor_id      text NOT NULL
);

CREATE TRIGGER settlement_exception_resolved_append_only
  BEFORE UPDATE OR DELETE ON settlement_exception_resolved
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

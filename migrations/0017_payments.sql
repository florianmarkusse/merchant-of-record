-- Payments against a stored quote, step 5a (docs/plan-payments.md §3-§5):
-- PSP accounts and routing, the payment attempt, its requests to the PSP,
-- what the PSP sends us, and the order a capture makes.

CREATE TYPE psp_provider AS ENUM ('fake');
-- Which environments may use an account (plan-architecture §6, decided
-- 19-Nov-2026): fake outside prod, a vendor's sandbox on the test setup,
-- live in prod only. The registry checks it on every call.
CREATE TYPE provider_account_kind AS ENUM ('fake', 'sandbox', 'live');
CREATE TYPE psp_status_mode AS ENUM ('push_fat', 'push_thin', 'poll');
CREATE TYPE psp_capture_mode AS ENUM ('sale', 'authorise_capture');
CREATE TYPE duplicate_guard AS ENUM ('vendor_constraint', 'compensable', 'none');
CREATE TYPE payment_method AS ENUM ('card', 'qr');

CREATE TABLE psp_account (
  id                uuid PRIMARY KEY,
  name              text NOT NULL UNIQUE,
  provider          psp_provider NOT NULL,
  kind              provider_account_kind NOT NULL,
  base_url          text NOT NULL,
  timeout_ms        int NOT NULL CHECK (timeout_ms > 0),
  status_mode       psp_status_mode NOT NULL,
  capture_mode      psp_capture_mode NOT NULL,
  -- Creating and capturing move money: never `compensable`
  -- (plan-architecture §5).
  create_guard      duplicate_guard NOT NULL CHECK (create_guard <> 'compensable'),
  capture_guard     duplicate_guard NOT NULL CHECK (capture_guard <> 'compensable'),
  methods           payment_method[] NOT NULL CHECK (cardinality(methods) > 0),
  poll_interval_s   int NOT NULL CHECK (poll_interval_s > 0),
  -- A made-up secret for a fake account. A real one names a key in .env
  -- (credentials_ref): secrets never sit in the database.
  webhook_secret    text NOT NULL,
  credentials_ref   text,
  created_at        timestamptz NOT NULL,
  CHECK ((kind = 'fake') = (credentials_ref IS NULL))
);

-- Which account takes a payment: the most specific row in force wins
-- (merchant and country, merchant, country, neither), then the newest.
-- Append-only: a change is a new row.
CREATE TABLE psp_route (
  id               uuid PRIMARY KEY,
  merchant_id      uuid REFERENCES merchant (id),
  buyer_country    char(2),
  psp_account_id   uuid NOT NULL REFERENCES psp_account (id),
  effective_from   timestamptz NOT NULL,
  created_at       timestamptz NOT NULL,
  actor_kind       actor_kind NOT NULL,
  actor_id         text NOT NULL
);

CREATE TRIGGER psp_route_append_only
  BEFORE UPDATE OR DELETE ON psp_route
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

CREATE TYPE payment_attempt_state AS ENUM (
  'requested',        -- asked of the PSP, no answer recorded yet
  'action_required',  -- the buyer must act: the PSP's page, or a QR
  'authorised',       -- approved, not captured yet
  'cancelling',       -- the buyer chose another way; the PSP is told
  'captured',
  'declined',
  'expired',
  'cancelled',
  'needs_person'      -- an answer lost on a `none` guard, or a mismatch
);
CREATE TYPE payment_action_kind AS ENUM ('redirect', 'qr');

CREATE TABLE payment_attempt (
  id                   uuid PRIMARY KEY,
  session_id           uuid NOT NULL REFERENCES checkout_session (id),
  quote_id             uuid NOT NULL REFERENCES quote (id),
  option_no            int NOT NULL,
  psp_account_id       uuid NOT NULL REFERENCES psp_account (id),
  route_id             uuid NOT NULL REFERENCES psp_route (id),
  method               payment_method NOT NULL,
  amount_minor         bigint NOT NULL CHECK (amount_minor > 0),
  currency             currency NOT NULL,
  state                payment_attempt_state NOT NULL,
  return_url           text NOT NULL,  -- where the PSP's page sends the buyer
  provider_payment_id  text,
  action_kind          payment_action_kind,
  action_url           text,           -- the PSP's page, or what the QR holds
  action_expires_at    timestamptz,
  ended_reason         text,           -- a decline's reason, a person's case
  created_at           timestamptz NOT NULL,
  updated_at           timestamptz NOT NULL,
  actor_kind           actor_kind NOT NULL,
  actor_id             text NOT NULL,
  FOREIGN KEY (quote_id, option_no) REFERENCES quote_option (quote_id, option_no),
  CHECK ((action_kind IS NULL) = (action_url IS NULL))
);
CREATE INDEX payment_attempt_by_session ON payment_attempt (session_id, id);

-- One open attempt per session (answer 1 of 19-Nov-2026): a buyer
-- switching methods can never be charged twice for one cart.
CREATE UNIQUE INDEX payment_attempt_one_open ON payment_attempt (session_id)
  WHERE state IN ('requested', 'action_required', 'authorised', 'cancelling');
-- One capture per session. A second one the PSP reports anyway becomes
-- `needs_person` (a refund, slice 8).
CREATE UNIQUE INDEX payment_attempt_one_captured ON payment_attempt (session_id)
  WHERE state = 'captured';

CREATE TYPE psp_operation AS ENUM ('create', 'capture', 'cancel');

-- One row per call that may do work at the PSP: the committed "we are
-- about to ask" mark, with the key kept for good. A `sent` row with no
-- result may have happened; the operation's guard decides what follows.
CREATE TABLE psp_request (
  id               uuid PRIMARY KEY,
  attempt_id       uuid NOT NULL REFERENCES payment_attempt (id),
  operation        psp_operation NOT NULL,
  number           int NOT NULL CHECK (number >= 1),
  idempotency_key  text NOT NULL UNIQUE,
  state            vendor_request_state NOT NULL,
  sends            int NOT NULL DEFAULT 0,
  result           text,
  created_at       timestamptz NOT NULL,
  completed_at     timestamptz,
  UNIQUE (attempt_id, operation, number)
);

CREATE TYPE psp_inbound_mode AS ENUM ('push_fat', 'push_thin', 'poll');

-- Raw bodies as received, before decoding; append-only. The body hash only
-- means "these bytes are stored already"; the guard against a duplicate
-- event is the PSP's event id, on payment_event.
CREATE TABLE psp_inbox (
  id           uuid PRIMARY KEY,
  account_id   uuid NOT NULL REFERENCES psp_account (id),
  mode         psp_inbound_mode NOT NULL,
  body_sha256  text NOT NULL,
  body         bytea NOT NULL,
  received_at  timestamptz NOT NULL,
  UNIQUE (account_id, body_sha256)
);

CREATE TRIGGER psp_inbox_append_only
  BEFORE UPDATE OR DELETE ON psp_inbox
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

CREATE TYPE psp_event_kind AS ENUM
  ('authorised', 'captured', 'declined', 'expired', 'cancelled');

-- Each PSP event about an attempt, once, by the PSP's own id.
CREATE TABLE payment_event (
  attempt_id       uuid NOT NULL REFERENCES payment_attempt (id),
  vendor_event_id  text NOT NULL,
  kind             psp_event_kind NOT NULL,
  amount_minor     bigint NOT NULL,
  occurred_at      timestamptz NOT NULL,
  inbox_id         uuid NOT NULL REFERENCES psp_inbox (id),
  recorded_at      timestamptz NOT NULL,
  PRIMARY KEY (attempt_id, vendor_event_id)
);

CREATE TRIGGER payment_event_append_only
  BEFORE UPDATE OR DELETE ON payment_event
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

CREATE TYPE order_state AS ENUM ('paid');

-- The order a capture makes: one per session and one per capture, by
-- these keys. `buyer_token` is the order page's link (5b).
CREATE TABLE order_ (
  id           uuid PRIMARY KEY,
  session_id   uuid NOT NULL UNIQUE REFERENCES checkout_session (id),
  attempt_id   uuid NOT NULL UNIQUE REFERENCES payment_attempt (id),
  merchant_id  uuid NOT NULL REFERENCES merchant (id),
  quote_id     uuid NOT NULL REFERENCES quote (id),
  option_no    int NOT NULL,
  address_id   uuid NOT NULL REFERENCES checkout_address (id),
  state        order_state NOT NULL,
  paid_at      timestamptz NOT NULL,
  buyer_token  text NOT NULL UNIQUE,
  created_at   timestamptz NOT NULL
);

-- The bank statement, step 5c-2 (docs/plan-payments.md §7, the bank
-- statement): our bank accounts, what they show, each entry booked once,
-- and which PSP payout each credit is.

CREATE TYPE bank_provider AS ENUM ('fake');

-- One currency balance at one provider: an IBAN, a Wise balance, an
-- Airwallex global account. `external_ref` is what the bank calls it.
CREATE TABLE bank_account (
  id                    uuid PRIMARY KEY,
  name                  text NOT NULL UNIQUE,
  provider              bank_provider NOT NULL,
  kind                  provider_account_kind NOT NULL,
  base_url              text NOT NULL,
  timeout_ms            int NOT NULL CHECK (timeout_ms > 0),
  -- A real account names a key in .env: secrets never sit in the database.
  credentials_ref       text,
  external_ref          text NOT NULL,
  currency              currency NOT NULL,
  statement_interval_s  int NOT NULL CHECK (statement_interval_s > 0),
  created_at            timestamptz NOT NULL,
  CHECK ((kind = 'fake') = (credentials_ref IS NULL))
);

-- Where a PSP account pays out each currency, as the PSP account is set up.
-- A payout is matched only within its destination.
CREATE TABLE psp_payout_destination (
  psp_account_id   uuid NOT NULL REFERENCES psp_account (id),
  currency         currency NOT NULL,
  bank_account_id  uuid NOT NULL REFERENCES bank_account (id),
  created_at       timestamptz NOT NULL,
  PRIMARY KEY (psp_account_id, currency)
);

-- Where an account's reading stands: the bank's own page key, a position
-- in its list, never a transaction id (a transaction listed twice would
-- make "after this id" ambiguous). The row is made when its looks start:
-- its insert is the guard against starting them twice.
CREATE TABLE bank_cursor (
  bank_account_id  uuid PRIMARY KEY REFERENCES bank_account (id),
  page_key         text,
  started_at       timestamptz NOT NULL
);

-- Raw pages as read, before decoding; append-only. The body hash only means
-- "these bytes are stored already"; the guard against an entry stored twice
-- is the bank's own transaction id, on bank_entry.
CREATE TABLE bank_inbox (
  id               uuid PRIMARY KEY,
  bank_account_id  uuid NOT NULL REFERENCES bank_account (id),
  body_sha256      text NOT NULL,
  body             bytea NOT NULL,
  received_at      timestamptz NOT NULL,
  UNIQUE (bank_account_id, body_sha256)
);

CREATE TRIGGER bank_inbox_append_only
  BEFORE UPDATE OR DELETE ON bank_inbox
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

-- Each entry the bank shows, once by its id, booked as it is stored.
-- `amount_minor` is signed: a credit is positive. `balance_after_minor` is
-- the bank's own balance after it, which the ledger's `bank` must equal.
CREATE TABLE bank_entry (
  id               uuid PRIMARY KEY,
  bank_account_id  uuid NOT NULL REFERENCES bank_account (id),
  vendor_entry_id  text NOT NULL,
  amount_minor     bigint NOT NULL CHECK (amount_minor <> 0),
  currency         currency NOT NULL,
  remittance       text NOT NULL,
  counterparty     text NOT NULL,
  booked_on        date NOT NULL,
  balance_after_minor  bigint NOT NULL,
  inbox_id         uuid NOT NULL REFERENCES bank_inbox (id),
  recorded_at      timestamptz NOT NULL,
  UNIQUE (bank_account_id, vendor_entry_id)
);

CREATE TRIGGER bank_entry_append_only
  BEFORE UPDATE OR DELETE ON bank_entry
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

-- The bank's own balance as last shown: after the last transaction of the
-- newest page read. Only a newer page moves it (pages are read in order, so
-- their inbox ids are too). What `bank` in the ledger must equal.
CREATE TABLE bank_balance_shown (
  bank_account_id  uuid PRIMARY KEY REFERENCES bank_account (id),
  inbox_id         uuid NOT NULL REFERENCES bank_inbox (id),
  balance_minor    bigint NOT NULL
);

-- A credit is this payout: matched by its reference, or linked by a person.
-- Each side at most once.
CREATE TABLE bank_match (
  entry_id    uuid PRIMARY KEY REFERENCES bank_entry (id),
  payout_id   uuid NOT NULL UNIQUE REFERENCES settlement_payout (id),
  matched_at  timestamptz NOT NULL,
  actor_kind  actor_kind NOT NULL,
  actor_id    text NOT NULL
);

CREATE TRIGGER bank_match_append_only
  BEFORE UPDATE OR DELETE ON bank_match
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

-- A person looked at an entry no payout explains and wrote down what it
-- is. It books nothing: the entry stays in bank_suspense.
CREATE TABLE bank_entry_note (
  entry_id    uuid PRIMARY KEY REFERENCES bank_entry (id),
  note        text NOT NULL CHECK (btrim(note) <> ''),
  noted_at    timestamptz NOT NULL,
  actor_kind  actor_kind NOT NULL,
  actor_id    text NOT NULL
);

CREATE TRIGGER bank_entry_note_append_only
  BEFORE UPDATE OR DELETE ON bank_entry_note
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

-- A person looked at a payout flagged once its credit arrived (its lines
-- still open, or its credit differing). It books nothing.
CREATE TABLE settlement_payout_note (
  payout_id   uuid PRIMARY KEY REFERENCES settlement_payout (id),
  note        text NOT NULL CHECK (btrim(note) <> ''),
  noted_at    timestamptz NOT NULL,
  actor_kind  actor_kind NOT NULL,
  actor_id    text NOT NULL
);

CREATE TRIGGER settlement_payout_note_append_only
  BEFORE UPDATE OR DELETE ON settlement_payout_note
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

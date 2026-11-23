-- Settlement as vendors send it (docs/plan-settlement-reconciliation.md):
-- a line and a payout are each stored on their own, in any order, and a
-- payout is reconciled against whatever lines have arrived.
--
-- The rows of the old shape cannot be carried over meaningfully (a batch's
-- payout entry held its rounding; a batch's net check was an exception).
-- There is no prod data (confirmed 23-Nov-2026), so a database that has
-- settlement rows is refused: recreate it (migrate, then seed).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM settlement_batch) OR EXISTS (SELECT 1 FROM settlement_line) THEN
    RAISE EXCEPTION 'settlement rows of the old shape exist: recreate this database';
  END IF;
END $$;

-- What we asked for, stored with the raw bytes: a page of a payout's lines
-- does not name its payout (the filter was in our request).
ALTER TABLE psp_inbox ADD COLUMN request_ref text;

-- Reading an account's settlement. `file`: `pending` is the report being
-- read. `api`: `pending` is the payout being read, `pending_phase` says
-- whether its object is wanted (until it is `completed`) or its lines,
-- `pending_after` the last line read (the PSP's cursor). No page numbers.
CREATE TYPE psp_settlement_read_phase AS ENUM ('payout', 'lines');
ALTER TABLE psp_settlement_cursor
  DROP COLUMN pending_page,
  ADD COLUMN pending_phase psp_settlement_read_phase,
  ADD COLUMN pending_after text,
  ADD CHECK (pending_phase IS NULL OR pending IS NOT NULL),
  ADD CHECK (pending_after IS NULL OR pending_phase = 'lines');

-- A payout: one transfer to our bank, as the PSP reports it.
ALTER TABLE settlement_batch RENAME TO settlement_payout;
ALTER TABLE settlement_payout RENAME COLUMN vendor_batch_id TO vendor_payout_id;
ALTER TABLE settlement_payout
  RENAME CONSTRAINT settlement_batch_account_id_vendor_batch_id_key
  TO settlement_payout_account_id_vendor_payout_id_key;

-- Its reconciliation against the lines that have arrived: `balanced` when
-- the net is their exact sum rounded either way to our units. `difference`
-- is the net less that exact sum. `rounding_minor` is the rounding booked
-- so far, in `rounding_entries` entries: each run books the change to its
-- target, never edits one.
CREATE TYPE settlement_reconciliation AS ENUM ('open', 'balanced');
ALTER TABLE settlement_payout
  ADD COLUMN reconciliation settlement_reconciliation NOT NULL,
  ADD COLUMN difference_numerator bigint NOT NULL,
  ADD COLUMN difference_denominator bigint NOT NULL
    CHECK (difference_denominator > 0),
  ADD COLUMN rounding_minor bigint NOT NULL,
  ADD COLUMN rounding_entries int NOT NULL CHECK (rounding_entries >= 0),
  ADD COLUMN reconciled_at timestamptz;

-- A line names its payout by the PSP's id, so it can come first.
DROP INDEX settlement_line_by_batch;
ALTER TABLE settlement_line
  DROP COLUMN batch_id,
  ADD COLUMN vendor_payout_id text NOT NULL,
  ADD COLUMN settled_at timestamptz NOT NULL;
CREATE INDEX settlement_line_by_payout
  ON settlement_line (account_id, vendor_payout_id);

-- A payout's difference is shown, not raised (question 1 of 23-Nov-2026):
-- exceptions are per line only, and `net_differs` goes.
ALTER TABLE settlement_exception DROP COLUMN batch_id;
ALTER TABLE settlement_exception ALTER COLUMN line_id SET NOT NULL;
ALTER TYPE settlement_exception_kind RENAME TO settlement_exception_kind_old;
CREATE TYPE settlement_exception_kind AS ENUM (
  'unknown_reference',  -- no payment of ours by that reference and id
  'not_captured',       -- the payment ended otherwise, or is with a person
  'amount_differs',     -- the gross is not what we captured
  'currency_differs',   -- the line's currency is not the payment's
  'settled_twice',      -- a second line for a payment already settled
  'unknown_type'        -- a line type we do not book (yet)
);
ALTER TABLE settlement_exception
  ALTER COLUMN kind TYPE settlement_exception_kind
  USING kind::text::settlement_exception_kind;
DROP TYPE settlement_exception_kind_old;

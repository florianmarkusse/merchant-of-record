-- The fake PSP's settlement shaped as real PSPs send it
-- (docs/plan-settlement-reconciliation.md §4.4): in `api` mode a payout's
-- lines can be listed once its reconciliation is complete, as Stripe's
-- `reconciliation_status`; in `file` mode a batch may hold part of its net
-- back for a later one, as Adyen's `Balancetransfer`.

-- When a payout's lines become listable. Batches cut before this were
-- complete at once.
ALTER TABLE psp_fake_settlement_batch ADD COLUMN reconciled_at timestamptz;
UPDATE psp_fake_settlement_batch SET reconciled_at = paid_out_at;
ALTER TABLE psp_fake_settlement_batch ALTER COLUMN reconciled_at SET NOT NULL;

-- What a batch held back, to be paid in the account's next batch in that
-- currency.
CREATE TABLE psp_fake_carry (
  account       text NOT NULL REFERENCES psp_fake_account (name),
  currency      char(3) NOT NULL,
  amount_minor  bigint NOT NULL CHECK (amount_minor > 0),
  PRIMARY KEY (account, currency)
);

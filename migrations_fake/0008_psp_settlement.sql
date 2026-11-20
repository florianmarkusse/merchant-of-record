-- The fake PSP's settlement (docs/plan-payments.md §7.1): batches it cut,
-- their lines, and which payments it has settled. Amounts in its own minor
-- units (IDR has 2).

ALTER TYPE psp_fake_operation ADD VALUE 'settlements';

-- A batch, one per currency per cut. `seq` orders them for the lists.
CREATE TABLE psp_fake_settlement_batch (
  seq          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id           text NOT NULL UNIQUE,
  account      text NOT NULL REFERENCES psp_fake_account (name),
  currency     char(3) NOT NULL,
  net_minor    bigint NOT NULL,
  paid_out_at  timestamptz NOT NULL,
  fault        text  -- `net_differs` if cut with it: the fuzzer's evidence
);

CREATE TABLE psp_fake_settlement_line (
  seq           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id            text NOT NULL UNIQUE,
  batch_id      text NOT NULL REFERENCES psp_fake_settlement_batch (id),
  type          text NOT NULL,
  payment_id    text NOT NULL,
  reference     text NOT NULL,
  gross_minor   bigint NOT NULL,
  fee_minor     bigint NOT NULL,
  -- The fault it was cut with, if any: the fuzzer's evidence.
  fault         text
);

-- A payment is settled once, unless a fault settles it again.
CREATE TABLE psp_fake_settled (
  payment_id  text PRIMARY KEY REFERENCES psp_fake_payment (id),
  batch_id    text NOT NULL REFERENCES psp_fake_settlement_batch (id)
);

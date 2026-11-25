-- The fake bank's own state (docs/plan-payments.md §7, the bank statement),
-- in the fake_vendor schema. Our code never reads it; tests inspect it as
-- evidence of what the bank holds. Amounts in ISO 4217 minor units (IDR has
-- 2 here); the API shows them as decimals in major units.

-- One currency balance, as a bank account in our sense is.
CREATE TABLE bank_fake_account (
  name           text PRIMARY KEY,
  currency       char(3) NOT NULL,
  balance_minor  bigint NOT NULL DEFAULT 0
);

-- Where a fake PSP account pays out a currency: its cut leaves a pending
-- credit here.
CREATE TABLE bank_fake_destination (
  psp_account   text NOT NULL REFERENCES psp_fake_account (name),
  currency      char(3) NOT NULL,
  bank_account  text NOT NULL REFERENCES bank_fake_account (name),
  PRIMARY KEY (psp_account, currency)
);

CREATE TYPE bank_fake_state AS ENUM ('pending', 'booked', 'lost');

-- Every transaction, pending until `book`. `seq` orders the list and is
-- its page key. A transaction listed twice (a fault) is a second booked row
-- with the same id, later in the list. `payout_id` and `faults` are the fuzzer's evidence.
CREATE TABLE bank_fake_transaction (
  seq                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  account              text NOT NULL REFERENCES bank_fake_account (name),
  transaction_id       text,
  amount_minor         bigint NOT NULL CHECK (amount_minor <> 0),
  description          text NOT NULL,
  counterparty         text NOT NULL,
  state                bank_fake_state NOT NULL,
  booked_on            date,
  balance_after_minor  bigint,
  payout_id            text,
  faults               text[] NOT NULL DEFAULT '{}',
  CHECK ((state = 'booked') = (transaction_id IS NOT NULL)),
  CHECK ((state = 'booked') = (booked_on IS NOT NULL)),
  CHECK ((state = 'booked') = (balance_after_minor IS NOT NULL))
);

-- The next list of an account fails once, as scripted.
CREATE TABLE bank_fake_scripted (
  account  text PRIMARY KEY REFERENCES bank_fake_account (name),
  fault    text NOT NULL CHECK (fault IN ('fail_500', 'timeout'))
);

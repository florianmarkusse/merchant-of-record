-- The fake duty vendor's own state (docs/plan-quote.md §3, 4b-2), in the
-- fake_vendor schema. Our code never reads it; tests inspect it as evidence
-- of what the vendor was asked.

CREATE TYPE duty_fault_kind AS ENUM (
  'fail_500', 'timeout_after_work', 'unknown_code', 'undecodable',
  'specific_duty', 'preference_note'
);

-- Rates per destination and HS prefix, as text formulas the way a vendor
-- writes them; the longest prefix wins. Any other code gets the default.
CREATE TABLE duty_rate (
  destination   char(2) NOT NULL,
  hs_prefix     text NOT NULL,
  duty_formula  text NOT NULL,
  tax_formula   text NOT NULL,
  PRIMARY KEY (destination, hs_prefix)
);

-- Every call that did work: what a vendor would bill.
CREATE TABLE duty_call (
  seq          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  account      text NOT NULL,
  destination  char(2) NOT NULL,
  origin       char(2) NOT NULL,
  hs_code      text NOT NULL,
  called_at    timestamptz NOT NULL
);

-- Faults scripted for the next calls, taken oldest first.
CREATE TABLE duty_fault (
  seq    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  fault  duty_fault_kind NOT NULL
);

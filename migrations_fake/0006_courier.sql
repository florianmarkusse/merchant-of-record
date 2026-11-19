-- The fake courier's own state (docs/plan-quote.md §4, 4c-2), in the
-- fake_vendor schema. Our code never reads it; tests inspect it as evidence
-- of what the carrier was asked.

CREATE TYPE courier_fault_kind AS ENUM (
  'fail_500', 'timeout_after_work', 'not_served', 'undecodable',
  'parts_mismatch', 'other_currency', 'already_expired'
);

-- Prices per service and destination: a base, a price per kg of billed
-- weight, fuel in basis points of the freight, a fixed surcharge, and the
-- validity the carrier states (none: it states none). Any other pair gets
-- the defaults in mor.fakes.courier.
CREATE TABLE courier_rate (
  service          text NOT NULL,
  destination      char(2) NOT NULL,
  currency         char(3) NOT NULL,
  base_minor       bigint NOT NULL CHECK (base_minor >= 0),
  per_kg_minor     bigint NOT NULL CHECK (per_kg_minor >= 0),
  fuel_bp          int NOT NULL CHECK (fuel_bp >= 0),
  surcharge_minor  bigint NOT NULL CHECK (surcharge_minor >= 0),
  valid_until      timestamptz,
  PRIMARY KEY (service, destination)
);

-- Every call that did work.
CREATE TABLE courier_call (
  seq          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  account      text NOT NULL,
  service      text NOT NULL,
  origin       char(2) NOT NULL,
  destination  char(2) NOT NULL,
  weight_g     int NOT NULL,
  length_mm    int NOT NULL,
  width_mm     int NOT NULL,
  height_mm    int NOT NULL,
  called_at    timestamptz NOT NULL
);

-- Faults scripted for the next calls, taken oldest first.
CREATE TABLE courier_fault (
  seq    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  fault  courier_fault_kind NOT NULL
);

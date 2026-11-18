-- Duty and VAT rates from vendors (docs/plan-quote.md §3, 4b-2): a mode per
-- country, vendor accounts per name, and the lookups asked once per
-- (vendor, destination, origin, HS code) with their answers.

ALTER TYPE aggregate_type ADD VALUE 'duty_lookup';
ALTER TYPE message_type ADD VALUE 'duty_rate_needed';
ALTER TYPE message_type ADD VALUE 'duty_rate_found';
ALTER TYPE message_type ADD VALUE 'duty_rate_unknown_code';
ALTER TYPE message_type ADD VALUE 'duty_rate_needs_person';
ALTER TYPE message_type ADD VALUE 'duty_rate_disagreed';
ALTER TYPE consumer_name ADD VALUE 'duty_rate_ask';

-- Who decides a country's rates per HS code: the vendor (our rows compared
-- with it), our rows first with the vendor filling gaps, or our rows only.
CREATE TYPE duty_mode AS ENUM ('vendor', 'own_first', 'own_only');

-- duty_source replaces its `kind` with a mode and the vendor's name. It has
-- no rows anywhere (0009 shipped it empty), so the columns can be NOT NULL.
ALTER TABLE duty_source DROP COLUMN kind;
DROP TYPE duty_source_kind;
ALTER TABLE duty_source
  ADD COLUMN mode duty_mode NOT NULL,
  ADD COLUMN vendor_name text,
  ADD CONSTRAINT duty_source_vendor_named
    CHECK ((mode = 'own_only') = (vendor_name IS NULL));

-- Which module speaks to a vendor. `fake` is the fake under /_fake/duty/,
-- refused in prod by the registry.
CREATE TYPE duty_provider AS ENUM ('fake');

-- One account per vendor name in each environment's database: the same
-- name points at the real API in prod and at the fake elsewhere.
CREATE TABLE duty_vendor_account (
  id           uuid PRIMARY KEY,
  vendor_name  text NOT NULL UNIQUE,
  provider     duty_provider NOT NULL,
  base_url     text NOT NULL,
  timeout_ms   int NOT NULL CHECK (timeout_ms > 0)
);

CREATE TYPE duty_lookup_state AS ENUM
  ('requested', 'answered', 'unknown_code', 'needs_person');

-- The "ask once" guard: one lookup per combination and vendor, for good.
-- `calls` counts the calls made (each committed before it is made), so
-- every billed call shows.
CREATE TABLE duty_lookup (
  id           uuid PRIMARY KEY,
  vendor_name  text NOT NULL REFERENCES duty_vendor_account (vendor_name),
  destination  char(2) NOT NULL,
  origin       char(2) NOT NULL,
  hs_code      text NOT NULL CHECK (hs_code ~ '^[0-9]{4,10}$'),
  state        duty_lookup_state NOT NULL,
  calls        int NOT NULL DEFAULT 0 CHECK (calls >= 0),
  detail       text,
  created_at   timestamptz NOT NULL,
  updated_at   timestamptz NOT NULL,
  UNIQUE (vendor_name, destination, origin, hs_code)
);

-- A vendor's answer per charge, with what our own rows said at the time
-- (the load compared with, and our rate and line, or none).
CREATE TABLE duty_answer (
  lookup_id             uuid NOT NULL REFERENCES duty_lookup (id),
  charge                landed_charge NOT NULL,
  formula               text NOT NULL,
  rate_numerator        bigint CHECK (rate_numerator >= 0),
  rate_denominator      bigint CHECK (rate_denominator > 0),
  note                  text,
  hs_code_used          text NOT NULL,
  compared_load_id      uuid REFERENCES market_load (id),
  own_rate_numerator    bigint,
  own_rate_denominator  bigint,
  own_line_no           int,
  answered_at           timestamptz NOT NULL,
  PRIMARY KEY (lookup_id, charge),
  CHECK ((rate_numerator IS NULL) = (rate_denominator IS NULL)),
  CHECK ((own_rate_numerator IS NULL) = (own_rate_denominator IS NULL)),
  CHECK ((own_rate_numerator IS NULL) = (own_line_no IS NULL))
);

CREATE TRIGGER duty_answer_append_only BEFORE UPDATE OR DELETE ON duty_answer
  FOR EACH ROW EXECUTE FUNCTION market_refuse_change();

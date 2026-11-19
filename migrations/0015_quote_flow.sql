-- The buyer's address, the stored quote and what a quote waits on
-- (docs/plan-quote.md §5, 4d-2). Addresses and quotes are never changed:
-- a new address makes a new quote.

-- A buyer's address for one session: personal data, never logged.
-- `phone_number` is digits only; postcode and region as the country's
-- rules require (mor.checkout.addresses).
CREATE TABLE checkout_address (
  id               uuid PRIMARY KEY,
  session_id       uuid NOT NULL REFERENCES checkout_session (id),
  name             text NOT NULL CHECK (name <> ''),
  email            text NOT NULL CHECK (email <> ''),
  phone_dial_code  text NOT NULL CHECK (phone_dial_code ~ '^\+[0-9]{1,4}$'),
  phone_number     text NOT NULL CHECK (phone_number ~ '^[0-9]{4,15}$'),
  line1            text NOT NULL CHECK (line1 <> ''),
  line2            text,
  city             text NOT NULL CHECK (city <> ''),
  region           text,
  postcode         text,
  country          char(2) NOT NULL,
  created_at       timestamptz NOT NULL
);
CREATE INDEX checkout_address_by_session ON checkout_address (session_id, id);

CREATE TYPE quote_state AS ENUM ('made', 'refused');
CREATE TYPE rounding_mode AS ENUM
  ('half_even', 'half_away_from_zero', 'toward_zero', 'away_from_zero',
   'floor', 'ceiling');
CREATE TYPE quote_part_kind AS ENUM
  ('goods', 'psp_fee', 'fx', 'mor_margin', 'freight', 'surcharge', 'fuel',
   'duty_paid_fee', 'duty', 'additional_duty', 'customs_fee', 'import_tax',
   'other_tax');

-- A quote for one address: made (with its options and parts) or refused
-- (with the API's code and message). `market_load_id` is the load whose
-- rules, cards and rates it used.
CREATE TABLE quote (
  id               uuid PRIMARY KEY,
  session_id       uuid NOT NULL REFERENCES checkout_session (id),
  address_id       uuid NOT NULL REFERENCES checkout_address (id),
  market_load_id   uuid NOT NULL REFERENCES market_load (id),
  state            quote_state NOT NULL,
  made_at          timestamptz NOT NULL,
  currency         currency,
  valid_until      timestamptz,
  refusal_code     text,
  refusal_message  text,
  CHECK ((state = 'made') = (currency IS NOT NULL AND valid_until IS NOT NULL)),
  CHECK ((state = 'refused') = (refusal_code IS NOT NULL
                                AND refusal_message IS NOT NULL))
);
CREATE INDEX quote_by_session ON quote (session_id, id);

-- One row per shipping option, cheapest first (`option_no` 0 first).
CREATE TABLE quote_option (
  quote_id        uuid NOT NULL REFERENCES quote (id),
  option_no       int NOT NULL CHECK (option_no >= 0),
  key             text NOT NULL,
  service         text NOT NULL,
  service_name    text NOT NULL,
  tier            service_tier NOT NULL,
  transit_min     int,
  transit_max     int,
  total_minor     bigint NOT NULL CHECK (total_minor >= 0),
  standing        row_standing NOT NULL,
  PRIMARY KEY (quote_id, option_no),
  CHECK ((transit_min IS NULL) = (transit_max IS NULL))
);

-- Every part, exact and rounded: the goods and fee parts are shared by
-- every option (`option_no` NULL); shipping, duties and taxes are an
-- option's own.
CREATE TABLE quote_part (
  quote_id         uuid NOT NULL REFERENCES quote (id),
  option_no        int,
  part_no          int NOT NULL CHECK (part_no >= 0),
  kind             quote_part_kind NOT NULL,
  label            text NOT NULL,
  line             int,
  exact_numerator  numeric NOT NULL,
  exact_denominator numeric NOT NULL CHECK (exact_denominator > 0),
  amount_minor     bigint NOT NULL,
  rounding         rounding_mode NOT NULL,
  collector        landed_collector,
  source           text NOT NULL,
  UNIQUE NULLS NOT DISTINCT (quote_id, option_no, part_no),
  FOREIGN KEY (quote_id, option_no) REFERENCES quote_option (quote_id, option_no)
);

-- Fee rules standing in for a decision, and the services not offered,
-- as the quote said them.
CREATE TABLE quote_note (
  quote_id   uuid NOT NULL REFERENCES quote (id),
  note_no    int NOT NULL CHECK (note_no >= 0),
  kind       text NOT NULL CHECK (kind IN ('placeholder', 'not_offered')),
  key        text NOT NULL,
  reason     text NOT NULL,
  PRIMARY KEY (quote_id, note_no)
);

-- The vendor answers a quote used, by lookup: one of the two ids.
CREATE TABLE quote_input (
  quote_id                  uuid NOT NULL REFERENCES quote (id),
  duty_lookup_id            uuid REFERENCES duty_lookup (id),
  shipping_price_lookup_id  uuid REFERENCES shipping_price_lookup (id),
  CHECK ((duty_lookup_id IS NULL) <> (shipping_price_lookup_id IS NULL)),
  UNIQUE NULLS NOT DISTINCT (quote_id, duty_lookup_id, shipping_price_lookup_id)
);

-- What a session's address waits on: one row per open lookup. Found by
-- the lookup when it finishes (`quote_wait_notify`); a row is never
-- removed, and a session no longer quoting for that address ignores it.
CREATE TABLE quote_wait (
  session_id                uuid NOT NULL REFERENCES checkout_session (id),
  address_id                uuid NOT NULL REFERENCES checkout_address (id),
  duty_lookup_id            uuid REFERENCES duty_lookup (id),
  shipping_price_lookup_id  uuid REFERENCES shipping_price_lookup (id),
  CHECK ((duty_lookup_id IS NULL) <> (shipping_price_lookup_id IS NULL)),
  UNIQUE NULLS NOT DISTINCT
    (address_id, duty_lookup_id, shipping_price_lookup_id)
);
CREATE INDEX quote_wait_by_duty ON quote_wait (duty_lookup_id)
  WHERE duty_lookup_id IS NOT NULL;
CREATE INDEX quote_wait_by_price ON quote_wait (shipping_price_lookup_id)
  WHERE shipping_price_lookup_id IS NOT NULL;

-- The session points at its newest address and, once decided, its quote.
ALTER TABLE checkout_session
  ADD COLUMN address_id uuid REFERENCES checkout_address (id),
  ADD COLUMN quote_id uuid REFERENCES quote (id),
  ADD CONSTRAINT checkout_session_address_for_state
    CHECK (state NOT IN ('quoting', 'quoted', 'refused') OR address_id IS NOT NULL),
  ADD CONSTRAINT checkout_session_quote_for_state
    CHECK (state NOT IN ('quoted', 'refused') OR quote_id IS NOT NULL);

CREATE FUNCTION quote_flow_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END $$;

CREATE TRIGGER checkout_address_append_only BEFORE UPDATE OR DELETE
  ON checkout_address FOR EACH ROW EXECUTE FUNCTION quote_flow_append_only();
CREATE TRIGGER quote_append_only BEFORE UPDATE OR DELETE
  ON quote FOR EACH ROW EXECUTE FUNCTION quote_flow_append_only();
CREATE TRIGGER quote_option_append_only BEFORE UPDATE OR DELETE
  ON quote_option FOR EACH ROW EXECUTE FUNCTION quote_flow_append_only();
CREATE TRIGGER quote_part_append_only BEFORE UPDATE OR DELETE
  ON quote_part FOR EACH ROW EXECUTE FUNCTION quote_flow_append_only();
CREATE TRIGGER quote_note_append_only BEFORE UPDATE OR DELETE
  ON quote_note FOR EACH ROW EXECUTE FUNCTION quote_flow_append_only();
CREATE TRIGGER quote_input_append_only BEFORE UPDATE OR DELETE
  ON quote_input FOR EACH ROW EXECUTE FUNCTION quote_flow_append_only();
CREATE TRIGGER quote_wait_append_only BEFORE UPDATE OR DELETE
  ON quote_wait FOR EACH ROW EXECUTE FUNCTION quote_flow_append_only();

-- Live carrier prices (docs/plan-quote.md §4, 4c-2): a route names a
-- service and, for our own tables only, a rate card; courier accounts per
-- carrier; and the price lookups, with one open request per combination.

ALTER TYPE price_source ADD VALUE 'carrier_api';
ALTER TYPE aggregate_type ADD VALUE 'shipping_price_lookup';
ALTER TYPE message_type ADD VALUE 'shipping_price_needed';
ALTER TYPE message_type ADD VALUE 'shipping_price_found';
ALTER TYPE message_type ADD VALUE 'shipping_price_not_served';
ALTER TYPE message_type ADD VALUE 'shipping_price_needs_person';
ALTER TYPE consumer_name ADD VALUE 'shipping_price_ask';

-- A route names its service. Rows loaded before this migration name only a
-- card, so the service is filled from it here. The append-only trigger is
-- switched off for this one UPDATE: no prod database exists yet, and every
-- local one gets the same fill.
ALTER TABLE shipping_route ADD COLUMN service text;
ALTER TABLE shipping_route DISABLE TRIGGER shipping_route_append_only;
UPDATE shipping_route r SET service = c.service
  FROM rate_card c WHERE c.load_id = r.load_id AND c.code = r.rate_card;
ALTER TABLE shipping_route ENABLE TRIGGER shipping_route_append_only;
ALTER TABLE shipping_route
  ALTER COLUMN service SET NOT NULL,
  ALTER COLUMN rate_card DROP NOT NULL,
  ADD FOREIGN KEY (load_id, service) REFERENCES shipping_service (load_id, code),
  -- A card only for our own tables; a live price has none.
  ADD CONSTRAINT shipping_route_card_for_own_table
    CHECK ((source = 'own_table') = (rate_card IS NOT NULL)),
  DROP CONSTRAINT shipping_route_load_id_country_iso2_group_code_rate_card_key,
  -- One source per service and place: a buyer never sees a service twice.
  ADD CONSTRAINT shipping_route_one_per_service
    UNIQUE NULLS NOT DISTINCT (load_id, country_iso2, group_code, service);

-- Which module speaks to a carrier. `fake` is the fake under
-- /_fake/courier/, refused in prod by the registry.
CREATE TYPE courier_provider AS ENUM ('fake');

-- How long a stored price is used (decided 19-Nov-2026): until the earlier
-- of the carrier's own validity and our period, or our period only.
CREATE TYPE price_expiry AS ENUM ('carrier_first', 'own_period');

-- One account per carrier (`shipping_service.provider`) in each
-- environment's database: the real API in prod, the fake elsewhere.
CREATE TABLE courier_account (
  id                 uuid PRIMARY KEY,
  carrier            text NOT NULL UNIQUE,
  provider           courier_provider NOT NULL,
  base_url           text NOT NULL,
  timeout_ms         int NOT NULL CHECK (timeout_ms > 0),
  price_valid_for_s  int NOT NULL CHECK (price_valid_for_s > 0),
  price_expiry       price_expiry NOT NULL
);

CREATE TYPE shipping_price_state AS ENUM
  ('requested', 'answered', 'not_served', 'needs_person');

-- A price asked of a carrier for one service, lane and exact parcel.
-- `calls` counts the calls made, each committed before it is made.
-- `finished_at` is set when it leaves `requested`; an outcome (a price, not
-- served, needs a person) is held for the account's period from then. The
-- price's total, currency and validity are filled once, when answered;
-- with them, what our own card said for the same lane at the time.
CREATE TABLE shipping_price_lookup (
  id                   uuid PRIMARY KEY,
  carrier              text NOT NULL REFERENCES courier_account (carrier),
  service              text NOT NULL,
  origin               char(2) NOT NULL,
  destination          char(2) NOT NULL,
  weight_g             int NOT NULL CHECK (weight_g > 0),
  length_mm            int NOT NULL CHECK (length_mm > 0),
  width_mm             int NOT NULL CHECK (width_mm > 0),
  height_mm            int NOT NULL CHECK (height_mm > 0),
  state                shipping_price_state NOT NULL,
  calls                int NOT NULL DEFAULT 0 CHECK (calls >= 0),
  detail               text,
  total_minor          bigint CHECK (total_minor >= 0),
  currency             currency,
  finished_at          timestamptz,
  carrier_valid_until  timestamptz,
  own_load_id          uuid REFERENCES market_load (id),
  own_rate_card        text,
  own_total_minor      bigint,
  own_currency         currency,
  created_at           timestamptz NOT NULL,
  updated_at           timestamptz NOT NULL,
  CHECK ((state = 'answered') = (total_minor IS NOT NULL)),
  CHECK ((total_minor IS NULL) = (currency IS NULL)),
  CHECK ((state = 'requested') = (finished_at IS NULL)),
  CHECK (carrier_valid_until IS NULL OR total_minor IS NOT NULL),
  CHECK ((own_total_minor IS NULL) = (own_currency IS NULL)),
  CHECK ((own_total_minor IS NULL) = (own_rate_card IS NULL)),
  CHECK (own_total_minor IS NULL OR own_load_id IS NOT NULL)
);

-- The guard: one open request per combination at a time. A price expires,
-- so a combination is asked again later; never twice at once.
CREATE UNIQUE INDEX shipping_price_one_open
  ON shipping_price_lookup
  (carrier, service, origin, destination, weight_g, length_mm, width_mm, height_mm)
  WHERE state = 'requested';

CREATE INDEX shipping_price_by_combination
  ON shipping_price_lookup
  (carrier, service, origin, destination, weight_g, length_mm, width_mm, height_mm,
   finished_at DESC);

CREATE TYPE carrier_price_part AS ENUM ('freight', 'fuel', 'surcharge');

-- The parts of an answered price, in the carrier's currency; they sum to
-- the lookup's total (checked when the answer is decoded).
CREATE TABLE shipping_price_part (
  lookup_id     uuid NOT NULL REFERENCES shipping_price_lookup (id),
  line_no       int NOT NULL CHECK (line_no >= 1),
  kind          carrier_price_part NOT NULL,
  name          text NOT NULL,
  amount_minor  bigint NOT NULL CHECK (amount_minor >= 0),
  PRIMARY KEY (lookup_id, line_no)
);

CREATE TRIGGER shipping_price_part_append_only BEFORE UPDATE OR DELETE
  ON shipping_price_part FOR EACH ROW EXECUTE FUNCTION market_refuse_change();

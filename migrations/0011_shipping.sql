-- Shipping prices from our own rate tables, and our fee rules, as part of
-- a market load (docs/plan-quote.md §4, 4c-1): append-only, a change is a
-- new load. The rows are what `docs/markets/` holds for services, rate
-- cards, zones, weight breaks, surcharges, transit times and restrictions,
-- plus which card is offered where (`shipping_routes.csv`) and the fee
-- components (`fees.csv`).

CREATE TYPE service_tier AS ENUM ('express', 'day_definite', 'economy', 'postal');
CREATE TYPE duties_paid AS ENUM
  ('yes', 'mandatory', 'ioss_only', 'on_request', 'no', 'unknown');
CREATE TYPE card_fuel AS ENUM ('extra', 'included', 'unknown');
CREATE TYPE surcharge_basis AS ENUM
  ('percent_of_freight', 'percent_of_duties', 'percent_of_value',
   'per_shipment', 'per_piece', 'per_kg', 'per_line');
CREATE TYPE surcharge_trigger AS ENUM
  ('always', 'duties_paid', 'passthrough', 'optional', 'on_event');
CREATE TYPE fuel_applies AS ENUM ('yes', 'no', 'unknown');
CREATE TYPE restriction_rule AS ENUM ('allowed', 'restricted', 'prohibited', 'unknown');
CREATE TYPE price_source AS ENUM ('own_table');
-- `placeholder`: a stand-in until a contract or a decision; prod refuses
-- to quote on one (plan-quote.md Q5).
CREATE TYPE row_standing AS ENUM ('placeholder', 'agreed');
CREATE TYPE fee_component AS ENUM ('psp_fee', 'fx', 'mor_margin');

CREATE TABLE shipping_service (
  load_id             uuid NOT NULL REFERENCES market_load (id),
  code                text NOT NULL,
  provider            text NOT NULL,
  name                text NOT NULL,
  origin_iso2         char(2) NOT NULL,
  tier                service_tier NOT NULL,
  duties_paid         duties_paid NOT NULL,
  volumetric_divisor  integer CHECK (volumetric_divisor > 0),  -- cm³ per kg
  max_weight_numerator    bigint,
  max_weight_denominator  bigint CHECK (max_weight_denominator > 0),
  status              research_status NOT NULL,
  PRIMARY KEY (load_id, code),
  FOREIGN KEY (load_id, origin_iso2) REFERENCES market_country (load_id, iso2),
  CHECK ((max_weight_numerator IS NULL) = (max_weight_denominator IS NULL))
);

CREATE TABLE rate_card (
  load_id         uuid NOT NULL,
  code            text NOT NULL,
  service         text NOT NULL,
  currency        currency NOT NULL,
  effective_from  date,
  effective_to    date,
  fuel            card_fuel NOT NULL,
  status          research_status NOT NULL,
  source          text NOT NULL,
  PRIMARY KEY (load_id, code),
  FOREIGN KEY (load_id, service) REFERENCES shipping_service (load_id, code)
);

CREATE TABLE rate_card_zone (
  load_id           uuid NOT NULL,
  rate_card         text NOT NULL,
  destination_iso2  char(2) NOT NULL,
  zone              text NOT NULL,
  status            research_status NOT NULL,
  PRIMARY KEY (load_id, rate_card, destination_iso2),
  FOREIGN KEY (load_id, rate_card) REFERENCES rate_card (load_id, code),
  FOREIGN KEY (load_id, destination_iso2) REFERENCES market_country (load_id, iso2)
);

-- A weight break's price in the card's currency; with a maximum, a range.
CREATE TABLE rate_card_rate (
  load_id             uuid NOT NULL,
  rate_card           text NOT NULL,
  zone                text NOT NULL,
  weight_numerator    bigint NOT NULL CHECK (weight_numerator > 0),
  weight_denominator  bigint NOT NULL CHECK (weight_denominator > 0),
  amount_minor        bigint NOT NULL CHECK (amount_minor >= 0),
  amount_max_minor    bigint CHECK (amount_max_minor >= amount_minor),
  PRIMARY KEY (load_id, rate_card, zone, weight_numerator, weight_denominator),
  FOREIGN KEY (load_id, rate_card) REFERENCES rate_card (load_id, code)
);

CREATE TABLE shipping_surcharge (
  load_id           uuid NOT NULL,
  line_no           integer NOT NULL,
  service           text NOT NULL,
  rate_card         text,                  -- NULL: every card of the service
  name              text NOT NULL,
  basis             surcharge_basis NOT NULL,
  rate_numerator    bigint CHECK (rate_numerator >= 0),
  rate_denominator  bigint CHECK (rate_denominator > 0),
  amount_minor      bigint CHECK (amount_minor >= 0),
  min_amount_minor  bigint CHECK (min_amount_minor >= 0),
  currency          currency NOT NULL,
  trigger           surcharge_trigger NOT NULL,
  destinations      text[] NOT NULL,       -- countries or groups; empty: all
  fuel_applies      fuel_applies NOT NULL,
  valid_until       date,
  status            research_status NOT NULL,
  source            text NOT NULL,
  PRIMARY KEY (load_id, line_no),
  FOREIGN KEY (load_id, service) REFERENCES shipping_service (load_id, code),
  FOREIGN KEY (load_id, rate_card) REFERENCES rate_card (load_id, code),
  CHECK ((rate_numerator IS NULL) = (rate_denominator IS NULL))
);

CREATE TABLE shipping_transit (
  load_id       uuid NOT NULL,
  service       text NOT NULL,
  country_iso2  char(2),
  group_code    text,
  days_min      integer NOT NULL CHECK (days_min >= 0),
  days_max      integer NOT NULL,
  status        research_status NOT NULL,
  FOREIGN KEY (load_id, service) REFERENCES shipping_service (load_id, code),
  FOREIGN KEY (load_id, country_iso2) REFERENCES market_country (load_id, iso2),
  FOREIGN KEY (load_id, group_code) REFERENCES market_group (load_id, code),
  CHECK ((country_iso2 IS NULL) <> (group_code IS NULL)),
  CHECK (days_max >= days_min),
  UNIQUE NULLS NOT DISTINCT (load_id, service, country_iso2, group_code)
);

CREATE TABLE service_restriction (
  load_id   uuid NOT NULL,
  service   text NOT NULL,
  category  text NOT NULL,
  rule      restriction_rule NOT NULL,
  status    research_status NOT NULL,
  PRIMARY KEY (load_id, service, category),
  FOREIGN KEY (load_id, service) REFERENCES shipping_service (load_id, code),
  FOREIGN KEY (load_id, category) REFERENCES market_category (load_id, category)
);

-- A rate card offered into a country or a group.
CREATE TABLE shipping_route (
  load_id       uuid NOT NULL,
  country_iso2  char(2),
  group_code    text,
  rate_card     text NOT NULL,
  source        price_source NOT NULL,
  standing      row_standing NOT NULL,
  FOREIGN KEY (load_id, rate_card) REFERENCES rate_card (load_id, code),
  FOREIGN KEY (load_id, country_iso2) REFERENCES market_country (load_id, iso2),
  FOREIGN KEY (load_id, group_code) REFERENCES market_group (load_id, code),
  CHECK ((country_iso2 IS NULL) <> (group_code IS NULL)),
  UNIQUE NULLS NOT DISTINCT (load_id, country_iso2, group_code, rate_card)
);

-- One fee component for buyers in a country or group (both NULL: the
-- default), from a date: a percentage of the merchant's price plus an
-- optional fixed amount. Per merchant overrides come with merchants (4d).
CREATE TABLE fee_rule (
  load_id           uuid NOT NULL REFERENCES market_load (id),
  line_no           integer NOT NULL,
  component         fee_component NOT NULL,
  country_iso2      char(2),
  group_code        text,
  rate_numerator    bigint NOT NULL CHECK (rate_numerator >= 0),
  rate_denominator  bigint NOT NULL CHECK (rate_denominator > 0),
  fixed_minor       bigint CHECK (fixed_minor >= 0),
  fixed_currency    currency,
  effective_from    date NOT NULL,
  standing          row_standing NOT NULL,
  source            text NOT NULL,
  PRIMARY KEY (load_id, line_no),
  FOREIGN KEY (load_id, country_iso2) REFERENCES market_country (load_id, iso2),
  FOREIGN KEY (load_id, group_code) REFERENCES market_group (load_id, code),
  CHECK (country_iso2 IS NULL OR group_code IS NULL),
  CHECK ((fixed_minor IS NULL) = (fixed_currency IS NULL))
);

CREATE TRIGGER shipping_service_append_only BEFORE UPDATE OR DELETE
  ON shipping_service FOR EACH ROW EXECUTE FUNCTION market_refuse_change();
CREATE TRIGGER rate_card_append_only BEFORE UPDATE OR DELETE
  ON rate_card FOR EACH ROW EXECUTE FUNCTION market_refuse_change();
CREATE TRIGGER rate_card_zone_append_only BEFORE UPDATE OR DELETE
  ON rate_card_zone FOR EACH ROW EXECUTE FUNCTION market_refuse_change();
CREATE TRIGGER rate_card_rate_append_only BEFORE UPDATE OR DELETE
  ON rate_card_rate FOR EACH ROW EXECUTE FUNCTION market_refuse_change();
CREATE TRIGGER shipping_surcharge_append_only BEFORE UPDATE OR DELETE
  ON shipping_surcharge FOR EACH ROW EXECUTE FUNCTION market_refuse_change();
CREATE TRIGGER shipping_transit_append_only BEFORE UPDATE OR DELETE
  ON shipping_transit FOR EACH ROW EXECUTE FUNCTION market_refuse_change();
CREATE TRIGGER service_restriction_append_only BEFORE UPDATE OR DELETE
  ON service_restriction FOR EACH ROW EXECUTE FUNCTION market_refuse_change();
CREATE TRIGGER shipping_route_append_only BEFORE UPDATE OR DELETE
  ON shipping_route FOR EACH ROW EXECUTE FUNCTION market_refuse_change();
CREATE TRIGGER fee_rule_append_only BEFORE UPDATE OR DELETE
  ON fee_rule FOR EACH ROW EXECUTE FUNCTION market_refuse_change();

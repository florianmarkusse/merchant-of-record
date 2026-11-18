-- Rates per HS code and trade agreement, and each destination's duty
-- source (docs/plan-quote.md §3), as part of a market load: append-only,
-- a change is a new load.

CREATE TYPE preference_condition AS ENUM
  ('origin_statement', 'certificate_of_origin', 'exporter_registration',
   'direct_shipment', 'not_under_ioss');
CREATE TYPE duty_source_kind AS ENUM ('own_table', 'vendor');

-- A rate for products whose HS code starts with `hs_prefix`, into one
-- destination or group, from one origin (NULL: any). With an agreement it
-- is a preferential rate that applies only when every condition is met.
CREATE TABLE hs_rate (
  load_id           uuid NOT NULL REFERENCES market_load (id),
  line_no           integer NOT NULL,
  country_iso2      char(2),
  group_code        text,
  hs_prefix         text NOT NULL CHECK (hs_prefix ~ '^[0-9]{2,10}$'),
  charge            landed_charge NOT NULL,
  origin_iso2       char(2),
  agreement         text,
  conditions        preference_condition[] NOT NULL,
  rate_numerator    bigint NOT NULL CHECK (rate_numerator >= 0),
  rate_denominator  bigint NOT NULL CHECK (rate_denominator > 0),
  effective_from    date,
  status            research_status NOT NULL,
  source            text NOT NULL,
  source_url        text NOT NULL,
  PRIMARY KEY (load_id, line_no),
  FOREIGN KEY (load_id, country_iso2) REFERENCES market_country (load_id, iso2),
  FOREIGN KEY (load_id, group_code) REFERENCES market_group (load_id, code),
  FOREIGN KEY (load_id, origin_iso2) REFERENCES market_country (load_id, iso2),
  CHECK ((country_iso2 IS NULL) <> (group_code IS NULL)),
  CHECK (agreement IS NOT NULL OR cardinality(conditions) = 0),
  CHECK (agreement IS NULL OR origin_iso2 IS NOT NULL)
);

-- A destination not listed takes its duty rates from our own table.
CREATE TABLE duty_source (
  load_id  uuid NOT NULL,
  iso2     char(2) NOT NULL,
  kind     duty_source_kind NOT NULL,
  PRIMARY KEY (load_id, iso2),
  FOREIGN KEY (load_id, iso2) REFERENCES market_country (load_id, iso2)
);

CREATE TRIGGER hs_rate_append_only BEFORE UPDATE OR DELETE ON hs_rate
  FOR EACH ROW EXECUTE FUNCTION market_refuse_change();
CREATE TRIGGER duty_source_append_only BEFORE UPDATE OR DELETE ON duty_source
  FOR EACH ROW EXECUTE FUNCTION market_refuse_change();

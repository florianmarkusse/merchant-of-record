-- Markets in the database (docs/plan-quote.md §2): countries, groups,
-- product categories, the landed rates (duty, fees and taxes with their
-- bands), our registrations and FX rates, as versioned loads.
--
-- A load is a complete set: every row belongs to one `market_load`, and a
-- quote stores the load it used. Rows are never updated or deleted; a
-- change is a new load. Categories are rows, not an enum: they grow with
-- the data, and no code branches on one.

-- The destinations' currencies, for band limits and FX rates. Exponents
-- in mor.money.currency (ISO 4217: JPY and KRW have none).
ALTER TYPE currency ADD VALUE 'GBP';
ALTER TYPE currency ADD VALUE 'AUD';
ALTER TYPE currency ADD VALUE 'NZD';
ALTER TYPE currency ADD VALUE 'CAD';
ALTER TYPE currency ADD VALUE 'CHF';
ALTER TYPE currency ADD VALUE 'JPY';
ALTER TYPE currency ADD VALUE 'KRW';

CREATE TYPE market_status AS ENUM
  ('open', 'conditional', 'closed', 'undecided', 'not_assessed');
CREATE TYPE research_status AS ENUM
  ('verified', 'secondary', 'estimate', 'unverified', 'to_fetch',
   'awaiting_answer', 'n/a');
CREATE TYPE market_group_kind AS ENUM ('legal', 'region');
CREATE TYPE landed_charge AS ENUM
  ('duty', 'additional_duty', 'fee', 'import_tax', 'other_tax');
CREATE TYPE landed_collector AS ENUM ('border', 'seller');
CREATE TYPE landed_band AS ENUM ('always', 'up_to', 'above');
CREATE TYPE landed_limit_basis AS ENUM ('goods', 'goods_plus_freight');
CREATE TYPE landed_basis AS ENUM ('percent', 'per_line', 'per_parcel');
CREATE TYPE landed_base AS ENUM ('goods', 'goods_plus_freight', 'goods_freight_duty');

CREATE TABLE market_load (
  id              uuid PRIMARY KEY,
  -- sha256 of the normalised content: equal to the latest load's means
  -- nothing changed, and nothing is written.
  content_sha256  text NOT NULL,
  git_commit      text NOT NULL,
  loaded_at       timestamptz NOT NULL,
  actor_kind      actor_kind NOT NULL,
  actor_id        text NOT NULL
);

CREATE TABLE market_category (
  load_id   uuid NOT NULL REFERENCES market_load (id),
  category  text NOT NULL,
  label     text NOT NULL,
  PRIMARY KEY (load_id, category)
);

CREATE TABLE market_country (
  load_id      uuid NOT NULL REFERENCES market_load (id),
  iso2         char(2) NOT NULL,
  name         text NOT NULL,
  currency     char(3),                -- blank for the few without one
  status       market_status NOT NULL,
  status_note  text NOT NULL,          -- the reason shown when not open
  PRIMARY KEY (load_id, iso2)
);

CREATE TABLE market_group (
  load_id  uuid NOT NULL REFERENCES market_load (id),
  code     text NOT NULL,
  label    text NOT NULL,
  kind     market_group_kind NOT NULL,
  PRIMARY KEY (load_id, code)
);

CREATE TABLE market_group_member (
  load_id     uuid NOT NULL,
  group_code  text NOT NULL,
  iso2        char(2) NOT NULL,
  PRIMARY KEY (load_id, group_code, iso2),
  FOREIGN KEY (load_id, group_code) REFERENCES market_group (load_id, code),
  FOREIGN KEY (load_id, iso2) REFERENCES market_country (load_id, iso2)
);

-- One duty, fee or tax: who collects it, in which band of the order's
-- value, at which rate or amount, on which base. For a country or a group
-- (the most specific row wins), for one category or all (NULL).
CREATE TABLE landed_rate (
  load_id           uuid NOT NULL REFERENCES market_load (id),
  line_no           integer NOT NULL,       -- its row in the source file
  country_iso2      char(2),
  group_code        text,
  category          text,
  charge            landed_charge NOT NULL,
  collector         landed_collector NOT NULL,
  band              landed_band NOT NULL,
  limit_minor       bigint,
  limit_currency    currency,
  limit_basis       landed_limit_basis,
  basis             landed_basis NOT NULL,
  rate_numerator    bigint,                 -- an exact fraction, never float
  rate_denominator  bigint,
  amount_minor      bigint,
  amount_currency   currency,
  base              landed_base,
  effective_from    date,                   -- NULL: in force before our records
  status            research_status NOT NULL,
  source            text NOT NULL,
  source_url        text NOT NULL,
  PRIMARY KEY (load_id, line_no),
  FOREIGN KEY (load_id, country_iso2) REFERENCES market_country (load_id, iso2),
  FOREIGN KEY (load_id, group_code) REFERENCES market_group (load_id, code),
  FOREIGN KEY (load_id, category) REFERENCES market_category (load_id, category),
  CHECK ((country_iso2 IS NULL) <> (group_code IS NULL)),
  CHECK ((band = 'always') = (limit_minor IS NULL)),
  CHECK ((limit_minor IS NULL) = (limit_currency IS NULL)),
  CHECK ((limit_minor IS NULL) = (limit_basis IS NULL)),
  CHECK (limit_minor IS NULL OR limit_minor >= 0),
  CHECK ((basis = 'percent') = (rate_numerator IS NOT NULL)),
  CHECK ((rate_numerator IS NULL) = (rate_denominator IS NULL)),
  CHECK (rate_denominator IS NULL OR rate_denominator > 0),
  CHECK (rate_numerator IS NULL OR rate_numerator >= 0),
  CHECK ((basis = 'percent') = (base IS NOT NULL)),
  CHECK ((basis = 'percent') = (amount_minor IS NULL)),
  CHECK ((amount_minor IS NULL) = (amount_currency IS NULL)),
  CHECK (amount_minor IS NULL OR amount_minor >= 0)
);

-- Our seller-scheme registrations. Empty until we register; a country's
-- seller-collected band applies to an order only with a row here.
CREATE TABLE market_registration (
  load_id         uuid NOT NULL,
  iso2            char(2) NOT NULL,
  scheme          text NOT NULL,          -- e.g. "UK VAT", "OVR", "IOSS"
  number          text NOT NULL,
  effective_from  date NOT NULL,
  PRIMARY KEY (load_id, iso2, scheme),
  FOREIGN KEY (load_id, iso2) REFERENCES market_country (load_id, iso2)
);

-- Units of `quote_currency` per one unit of `base_currency`, exact.
CREATE TABLE market_fx_rate (
  load_id           uuid NOT NULL REFERENCES market_load (id),
  base_currency     currency NOT NULL,
  quote_currency    currency NOT NULL,
  rate_numerator    bigint NOT NULL CHECK (rate_numerator > 0),
  rate_denominator  bigint NOT NULL CHECK (rate_denominator > 0),
  as_of             date NOT NULL,
  status            research_status NOT NULL,
  source            text NOT NULL,
  PRIMARY KEY (load_id, base_currency, quote_currency)
);

-- Append-only, like the ledger: a change is a new load.
CREATE FUNCTION market_refuse_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: % refused', TG_TABLE_NAME, TG_OP;
END $$;

CREATE TRIGGER market_load_append_only BEFORE UPDATE OR DELETE ON market_load
  FOR EACH ROW EXECUTE FUNCTION market_refuse_change();
CREATE TRIGGER market_category_append_only BEFORE UPDATE OR DELETE ON market_category
  FOR EACH ROW EXECUTE FUNCTION market_refuse_change();
CREATE TRIGGER market_country_append_only BEFORE UPDATE OR DELETE ON market_country
  FOR EACH ROW EXECUTE FUNCTION market_refuse_change();
CREATE TRIGGER market_group_append_only BEFORE UPDATE OR DELETE ON market_group
  FOR EACH ROW EXECUTE FUNCTION market_refuse_change();
CREATE TRIGGER market_group_member_append_only BEFORE UPDATE OR DELETE ON market_group_member
  FOR EACH ROW EXECUTE FUNCTION market_refuse_change();
CREATE TRIGGER landed_rate_append_only BEFORE UPDATE OR DELETE ON landed_rate
  FOR EACH ROW EXECUTE FUNCTION market_refuse_change();
CREATE TRIGGER market_registration_append_only BEFORE UPDATE OR DELETE ON market_registration
  FOR EACH ROW EXECUTE FUNCTION market_refuse_change();
CREATE TRIGGER market_fx_rate_append_only BEFORE UPDATE OR DELETE ON market_fx_rate
  FOR EACH ROW EXECUTE FUNCTION market_refuse_change();

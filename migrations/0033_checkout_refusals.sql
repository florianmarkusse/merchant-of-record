-- What checkout refuses, step 6a-1 (docs/plan-shipments.md §4): a line
-- is sold only with our HS classification of its SKU, and a merchant
-- opens sessions only with a ship-from address. Both tables are
-- append-only; the newest row per key is in force (ids are uuid7, so
-- they sort by time).
--
-- A database holding session lines without a `merchant_sku` cannot take
-- this migration: those lines were never classified, and a line is
-- append-only. Such databases (the test setup's) are recreated.

CREATE TABLE sku_classification (
  id            uuid PRIMARY KEY,
  merchant_id   uuid NOT NULL REFERENCES merchant (id),
  merchant_sku  text NOT NULL CHECK (merchant_sku <> ''),
  -- Ours, at least the six digits every country shares.
  hs_code       text NOT NULL CHECK (hs_code ~ '^[0-9]{6,10}$'),
  created_at    timestamptz NOT NULL,
  actor_kind    actor_kind NOT NULL,
  actor_id      text NOT NULL
);
CREATE INDEX sku_classification_in_force
  ON sku_classification (merchant_id, merchant_sku, id DESC);

CREATE TABLE merchant_ship_from (
  id               uuid PRIMARY KEY,
  merchant_id      uuid NOT NULL REFERENCES merchant (id),
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
  created_at       timestamptz NOT NULL,
  actor_kind       actor_kind NOT NULL,
  actor_id         text NOT NULL
);
CREATE INDEX merchant_ship_from_in_force ON merchant_ship_from (merchant_id, id DESC);

CREATE FUNCTION checkout_refusals_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END $$;

CREATE TRIGGER sku_classification_append_only BEFORE UPDATE OR DELETE
  ON sku_classification FOR EACH ROW EXECUTE FUNCTION checkout_refusals_append_only();
CREATE TRIGGER merchant_ship_from_append_only BEFORE UPDATE OR DELETE
  ON merchant_ship_from FOR EACH ROW EXECUTE FUNCTION checkout_refusals_append_only();

-- The line keeps the merchant's code as sent, and the classification it
-- was sold under with our code, so a later reclassification never
-- changes what was sold or declared.
ALTER TABLE checkout_session_line RENAME COLUMN hs_code TO merchant_hs_code;
ALTER TABLE checkout_session_line
  RENAME CONSTRAINT checkout_session_line_hs_code_check
  TO checkout_session_line_merchant_hs_code_check;
ALTER TABLE checkout_session_line
  ADD COLUMN classification_id uuid NOT NULL REFERENCES sku_classification (id),
  ADD COLUMN hs_code text NOT NULL CHECK (hs_code ~ '^[0-9]{6,10}$'),
  ALTER COLUMN merchant_sku SET NOT NULL;

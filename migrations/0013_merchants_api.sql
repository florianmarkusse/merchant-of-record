-- Merchants, API keys, idempotency and checkout sessions
-- (docs/plan-quote.md §5, 4d-1).

ALTER TYPE aggregate_type ADD VALUE 'checkout_session';
ALTER TYPE message_type ADD VALUE 'checkout_session_created';

-- A minimal merchant: KYB comes in its own slice. `daily_vendor_asks`
-- caps the new vendor lookups its estimates may start per UTC day.
CREATE TABLE merchant (
  id                 uuid PRIMARY KEY,
  legal_name         text NOT NULL CHECK (legal_name <> ''),
  display_name       text NOT NULL CHECK (display_name <> ''),
  country            char(2) NOT NULL,
  daily_vendor_asks  int NOT NULL CHECK (daily_vendor_asks >= 0),
  created_at         timestamptz NOT NULL,
  actor_kind         actor_kind NOT NULL,
  actor_id           text NOT NULL
);

-- Which environment a key works in, as its text shows (`sk_test_…`).
CREATE TYPE api_key_env AS ENUM ('dev', 'test', 'live');
CREATE TYPE api_key_state AS ENUM ('active', 'revoked');

-- A secret key: `sk_<env>_<id>_<secret>`. Only the SHA-256 of the secret
-- is kept; the secret is random and long, so a slow hash adds nothing.
-- `id` is the public part used to find the row.
CREATE TABLE api_key (
  id             text PRIMARY KEY,
  merchant_id    uuid NOT NULL REFERENCES merchant (id),
  env            api_key_env NOT NULL,
  secret_sha256  bytea NOT NULL CHECK (length(secret_sha256) = 32),
  state          api_key_state NOT NULL,
  created_at     timestamptz NOT NULL,
  revoked_at     timestamptz,
  CHECK ((state = 'revoked') = (revoked_at IS NOT NULL))
);

-- One row per merchant and Idempotency-Key, kept for good
-- (plan-architecture §4). Inserted first in the transaction that does the
-- work, so a concurrent request with the same key waits on the primary
-- key and then replays the stored response.
CREATE TABLE api_idempotency (
  merchant_id     uuid NOT NULL REFERENCES merchant (id),
  key             text NOT NULL CHECK (key <> '' AND length(key) <= 255),
  request_sha256  bytea NOT NULL CHECK (length(request_sha256) = 32),
  status          int,
  response        bytea,
  created_at      timestamptz NOT NULL,
  PRIMARY KEY (merchant_id, key),
  CHECK ((status IS NULL) = (response IS NULL))
);

-- New vendor lookups a merchant's estimates started on a UTC day: read
-- and raised under this row's lock (decided 19-Nov-2026).
CREATE TABLE merchant_vendor_asks (
  merchant_id  uuid NOT NULL REFERENCES merchant (id),
  day          date NOT NULL,
  asks         int NOT NULL CHECK (asks >= 0),
  PRIMARY KEY (merchant_id, day)
);

CREATE TYPE checkout_session_state AS ENUM ('open', 'expired', 'canceled');

CREATE TABLE checkout_session (
  id                  uuid PRIMARY KEY,
  merchant_id         uuid NOT NULL REFERENCES merchant (id),
  state               checkout_session_state NOT NULL,
  merchant_reference  text,
  return_url          text NOT NULL,
  cancel_url          text NOT NULL,
  weight_g            int NOT NULL CHECK (weight_g > 0),
  length_mm           int NOT NULL CHECK (length_mm > 0),
  width_mm            int NOT NULL CHECK (width_mm > 0),
  height_mm           int NOT NULL CHECK (height_mm > 0),
  created_at          timestamptz NOT NULL,
  expires_at          timestamptz NOT NULL CHECK (expires_at > created_at),
  actor_kind          actor_kind NOT NULL,
  actor_id            text NOT NULL
);
CREATE INDEX checkout_session_by_merchant ON checkout_session (merchant_id, id);

-- What was sold, as the merchant sent it: a snapshot, never changed.
-- `hs_code` is the merchant's (a hint: classification is ours).
CREATE TABLE checkout_session_line (
  session_id           uuid NOT NULL REFERENCES checkout_session (id),
  line_no              int NOT NULL CHECK (line_no >= 0),
  name                 text NOT NULL CHECK (name <> ''),
  quantity             int NOT NULL CHECK (quantity > 0),
  unit_price_minor     bigint NOT NULL CHECK (unit_price_minor >= 0),
  currency             currency NOT NULL,
  category             text NOT NULL,
  merchant_sku         text,
  customs_description  text NOT NULL CHECK (customs_description <> ''),
  origin_country       char(2) NOT NULL,
  weight_g             int NOT NULL CHECK (weight_g > 0),
  hs_code              text CHECK (hs_code ~ '^[0-9]{4,10}$'),
  PRIMARY KEY (session_id, line_no)
);

CREATE FUNCTION checkout_session_line_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'checkout_session_line is append-only';
END $$;

CREATE TRIGGER checkout_session_line_append_only
  BEFORE UPDATE OR DELETE ON checkout_session_line
  FOR EACH ROW EXECUTE FUNCTION checkout_session_line_append_only();

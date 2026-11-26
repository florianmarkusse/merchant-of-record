-- Booking a shipment, step 6a-2 (docs/plan-shipments.md §2, §5, §6): the
-- carrier account's capabilities, the shipment with its customs lines,
-- each request to the carrier, and the documents it returned.

-- Capabilities as account data, as psp_account has them, so feature code
-- switches on the account's values, never on a provider. Booking may be
-- `compensable`: a label never handed over costs nothing and is
-- cancelled. Tracking is used from 6b on.
CREATE TYPE carrier_tracking_mode AS ENUM ('push_fat', 'push_thin', 'poll');

ALTER TABLE carrier_account
  ADD COLUMN book_guard duplicate_guard NOT NULL DEFAULT 'vendor_constraint',
  ADD COLUMN tracking_mode carrier_tracking_mode NOT NULL DEFAULT 'poll',
  ADD COLUMN poll_interval_s int NOT NULL DEFAULT 300 CHECK (poll_interval_s > 0),
  -- A made-up secret for a fake account. A real one names a key in .env
  -- (credentials_ref): secrets never sit in the database.
  ADD COLUMN webhook_secret text NOT NULL DEFAULT '',
  ADD COLUMN credentials_ref text,
  ADD CHECK ((kind = 'fake') = (credentials_ref IS NULL));
ALTER TABLE carrier_account
  ALTER COLUMN book_guard DROP DEFAULT,
  ALTER COLUMN tracking_mode DROP DEFAULT,
  ALTER COLUMN poll_interval_s DROP DEFAULT,
  ALTER COLUMN webhook_secret DROP DEFAULT;

-- The fake carrier names its duplicate guard in the path, as the fake
-- echo vendor does: the existing accounts keep keys (`vendor_constraint`).
UPDATE carrier_account
SET base_url = replace(base_url, '/_fake/carrier/', '/_fake/carrier/keyed/')
WHERE provider = 'fake';

CREATE TYPE shipment_booking_state AS ENUM (
  'booking',        -- asked of the carrier, no label recorded yet
  'labelled',       -- one label, its documents stored
  'deduplicating',  -- a `compensable` resend may have made extra labels
  'label_failed',   -- refused, by the carrier or before asking it
  'needs_person'    -- no answer on a `none` guard and the carrier shows none
);
-- Derived from the carrier's events from 6b on; `none` until then.
CREATE TYPE shipment_tracking_status AS ENUM (
  'none', 'picked_up', 'in_transit', 'held_at_customs', 'delivered',
  'returning', 'returned', 'lost'
);
CREATE TYPE shipment_failure AS ENUM (
  'ship_from_elsewhere',  -- the ship-from is not in the service's origin
  'no_carrier_account',   -- the service's carrier has no account with us
  'address_refused',      -- the carrier refused the receiver's address
  'customs_refused',      -- the carrier refused the customs data
  'refused'               -- the carrier refused for another reason
);
CREATE TYPE incoterm AS ENUM ('ddp', 'dap');
CREATE TYPE carrier_operation AS ENUM ('book', 'cancel');
CREATE TYPE shipment_document_kind AS ENUM ('label', 'commercial_invoice');

-- One per order step (`sequence` 0 is the outbound parcel; returns come
-- in slice 8). The ship-from and the receiver are referenced, not copied:
-- both tables are append-only, so the row in force when the shipment was
-- made is its copy, and a later change never alters a booked waybill.
-- `market_load_id` is the quote's load: the routing version.
CREATE TABLE shipment (
  id                     uuid PRIMARY KEY,
  order_id               uuid NOT NULL REFERENCES order_ (id),
  sequence               int NOT NULL CHECK (sequence >= 0),
  carrier                text NOT NULL,
  carrier_account_id     uuid REFERENCES carrier_account (id),
  service                text NOT NULL,
  market_load_id         uuid NOT NULL REFERENCES market_load (id),
  weight_g               int NOT NULL CHECK (weight_g > 0),
  length_mm              int NOT NULL CHECK (length_mm > 0),
  width_mm               int NOT NULL CHECK (width_mm > 0),
  height_mm              int NOT NULL CHECK (height_mm > 0),
  ship_from_id           uuid NOT NULL REFERENCES merchant_ship_from (id),
  receiver_address_id    uuid NOT NULL REFERENCES checkout_address (id),
  incoterm               incoterm NOT NULL,
  booking_state          shipment_booking_state NOT NULL,
  failure                shipment_failure,
  -- Why it failed or needs a person, for the staff page.
  detail                 text,
  tracking_status        shipment_tracking_status NOT NULL,
  tracking_number        text,
  export_declaration_no  text,
  created_at             timestamptz NOT NULL,
  updated_at             timestamptz NOT NULL,
  UNIQUE (order_id, sequence),
  UNIQUE (carrier_account_id, tracking_number),
  -- A refusal is kept while the extra labels are sorted out, and gone
  -- once a label is kept.
  CHECK (booking_state <> 'label_failed' OR failure IS NOT NULL),
  CHECK (booking_state IN ('label_failed', 'deduplicating') OR failure IS NULL),
  CHECK (booking_state <> 'labelled'
         OR (tracking_number IS NOT NULL AND export_declaration_no IS NOT NULL)),
  CHECK (carrier_account_id IS NOT NULL OR failure = 'no_carrier_account')
);
CREATE INDEX shipment_by_state ON shipment (booking_state, id);

-- What we tell customs, frozen from the sold lines when the shipment is
-- made: our HS code, never the merchant's.
CREATE TABLE shipment_customs_line (
  shipment_id  uuid NOT NULL REFERENCES shipment (id),
  line_no      int NOT NULL CHECK (line_no >= 0),
  description  text NOT NULL CHECK (description <> ''),
  hs_code      text NOT NULL CHECK (hs_code ~ '^[0-9]{6,10}$'),
  origin       char(2) NOT NULL,
  quantity     int NOT NULL CHECK (quantity > 0),
  value_minor  bigint NOT NULL CHECK (value_minor >= 0),
  currency     currency NOT NULL,
  PRIMARY KEY (shipment_id, line_no)
);
CREATE TRIGGER shipment_customs_line_append_only
  BEFORE UPDATE OR DELETE ON shipment_customs_line
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

-- Each request to the carrier, as the duplicate guard sees it. A `book`
-- request's id is its idempotency key at the carrier; a `cancel` names
-- the extra label it cancels.
CREATE TABLE carrier_request (
  id                      uuid PRIMARY KEY,
  shipment_id             uuid NOT NULL REFERENCES shipment (id),
  operation               carrier_operation NOT NULL,
  number                  int NOT NULL CHECK (number >= 1),
  idempotency_key         text NOT NULL UNIQUE,
  state                   vendor_request_state NOT NULL,
  sends                   int NOT NULL DEFAULT 0 CHECK (sends >= 0),
  target_tracking_number  text,
  result                  text,
  created_at              timestamptz NOT NULL,
  completed_at            timestamptz,
  UNIQUE (shipment_id, operation, number),
  CHECK ((operation = 'cancel') = (target_tracking_number IS NOT NULL))
);

-- The label and the commercial invoice of the label kept, once each.
CREATE TABLE shipment_document (
  shipment_id   uuid NOT NULL REFERENCES shipment (id),
  kind          shipment_document_kind NOT NULL,
  content_type  text NOT NULL,
  body          bytea NOT NULL CHECK (length(body) > 0),
  created_at    timestamptz NOT NULL,
  PRIMARY KEY (shipment_id, kind)
);
CREATE TRIGGER shipment_document_append_only
  BEFORE UPDATE OR DELETE ON shipment_document
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

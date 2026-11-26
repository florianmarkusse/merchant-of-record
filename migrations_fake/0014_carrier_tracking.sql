-- The fake carrier's tracking (docs/plan-shipments.md §3, 6b step 1): each
-- label plays a script of events, released only when told, pushed to a
-- subscribed webhook and served per tracking number to a poll.

-- Picked at booking from the receiver's first address line.
CREATE TYPE carrier_script AS ENUM ('delivered', 'lost', 'return', 'withdrawn');
ALTER TABLE carrier_shipment
  ADD COLUMN script carrier_script NOT NULL DEFAULT 'delivered';
ALTER TABLE carrier_shipment ALTER COLUMN script DROP DEFAULT;

CREATE TYPE carrier_webhook_payload AS ENUM ('fat', 'thin');
CREATE TYPE carrier_push_state AS ENUM ('not_subscribed', 'delivered', 'failed');

-- As registered on a carrier's dashboard: where to push, and the secret to
-- sign with. A polling account has none.
CREATE TABLE carrier_webhook (
  flavour  carrier_flavour NOT NULL,
  account  text NOT NULL,
  url      text NOT NULL,
  secret   text NOT NULL,
  payload  carrier_webhook_payload NOT NULL,
  PRIMARY KEY (flavour, account)
);

-- Events released so far, in release order; `step` is the event's place
-- in its label's script, so each is released once.
CREATE TABLE carrier_track_event (
  seq              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id               text NOT NULL UNIQUE,
  tracking_number  text NOT NULL REFERENCES carrier_shipment (tracking_number),
  step             int NOT NULL,
  code             text NOT NULL,
  occurred_at      timestamptz NOT NULL,
  released_at      timestamptz NOT NULL,
  push             carrier_push_state NOT NULL,
  UNIQUE (tracking_number, step)
);

ALTER TYPE carrier_fault_operation ADD VALUE 'track';

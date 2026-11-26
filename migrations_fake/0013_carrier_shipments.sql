-- The fake carrier books labels (docs/plan-shipments.md §3, 6a-2), in
-- three flavours, one per duplicate guard: `keyed` replays a booking for
-- a key it has seen, for good; `cancellable` books again but lets a label
-- be cancelled; `plain` does neither. Our code never reads these tables;
-- tests inspect them as evidence of what the carrier was asked to do.

CREATE TYPE carrier_flavour AS ENUM ('keyed', 'cancellable', 'plain');
CREATE TYPE carrier_label_state AS ENUM ('live', 'cancelled');

-- Every label the carrier ever made, cancelled or not, with the
-- documents it handed out.
CREATE TABLE carrier_shipment (
  tracking_number        text PRIMARY KEY,
  flavour                carrier_flavour NOT NULL,
  account                text NOT NULL,
  reference              text NOT NULL,
  idempotency_key        text,
  service                text NOT NULL,
  origin                 char(2) NOT NULL,
  destination            char(2) NOT NULL,
  export_declaration_no  text NOT NULL,
  state                  carrier_label_state NOT NULL,
  label                  bytea NOT NULL,
  commercial_invoice     bytea NOT NULL,
  created_at             timestamptz NOT NULL
);
CREATE INDEX carrier_shipment_by_reference
  ON carrier_shipment (flavour, account, reference);

-- The `keyed` flavour's promise: one label per key, for good.
CREATE UNIQUE INDEX carrier_shipment_keyed_once
  ON carrier_shipment (account, idempotency_key) WHERE flavour = 'keyed';

-- Faults are scripted per operation, so a fault meant for a booking is
-- never taken by a rate call.
CREATE TYPE carrier_fault_operation AS ENUM ('rate', 'book', 'find', 'cancel');
ALTER TYPE carrier_fault_kind ADD VALUE 'fail_500_after_work';
ALTER TABLE carrier_fault
  ADD COLUMN operation carrier_fault_operation NOT NULL DEFAULT 'rate';
ALTER TABLE carrier_fault ALTER COLUMN operation DROP DEFAULT;

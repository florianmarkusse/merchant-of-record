-- The fake echo vendor's own state (docs/plan-messaging.md §10.5). Applied
-- outside prod only, with search_path = fake_vendor, so everything here
-- lands in that schema. A third party's data: our code never reads it; tests
-- inspect it as evidence of what the vendor was asked to do.

CREATE TYPE echo_flavour AS ENUM ('keyed', 'cancellable', 'plain');
CREATE TYPE echo_state AS ENUM ('live', 'cancelled');
CREATE TYPE echo_fault_kind AS ENUM (
  'fail_500', 'fail_500_after_work', 'timeout_after_work', 'refuse'
);

-- Every echo the vendor ever made, cancelled or not.
CREATE TABLE echo (
  id               text PRIMARY KEY,
  flavour          echo_flavour NOT NULL,
  reference        text NOT NULL,
  idempotency_key  text,
  message          text NOT NULL,
  state            echo_state NOT NULL,
  created_at       timestamptz NOT NULL
);

CREATE INDEX echo_by_reference ON echo (flavour, reference);

-- The `keyed` flavour's promise: one echo per key, for good.
CREATE UNIQUE INDEX echo_keyed_once ON echo (idempotency_key)
  WHERE flavour = 'keyed';

-- Faults scripted for the next calls of a flavour, taken oldest first.
CREATE TABLE echo_fault (
  seq      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  flavour  echo_flavour NOT NULL,
  fault    echo_fault_kind NOT NULL
);

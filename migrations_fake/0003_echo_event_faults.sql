-- Faults can be scripted for the next events fetch too, not only sends.

CREATE TYPE echo_fault_operation AS ENUM ('send', 'events');
ALTER TABLE echo_fault
  ADD COLUMN operation echo_fault_operation NOT NULL DEFAULT 'send';

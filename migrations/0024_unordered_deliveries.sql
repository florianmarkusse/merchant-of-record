-- Deliveries run in any order (decided 23-Nov-2026,
-- docs/plan-messaging-rework.md §2): the worker no longer looks for the
-- oldest open delivery per consumer and aggregate, only for open ones that
-- are due, and the idle worker for the next one to come due.
DROP INDEX outbox_delivery_pending;

CREATE INDEX outbox_delivery_due
  ON outbox_delivery (not_before)
  WHERE handled_at IS NULL;

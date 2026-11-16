-- Wake idle workers when deliveries are written (docs/plan-messaging.md §4).
-- NOTIFY is delivered only when the transaction commits, and dropped if it
-- rolls back, so a worker never wakes for a delivery it cannot see. It is
-- only a wake-up: workers still poll as a fallback, because a notification
-- sent while no worker listens is not kept. No payload.

CREATE FUNCTION outbox_delivery_notify() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM pg_notify('outbox_delivery', '');
  RETURN NULL;
END $$;

CREATE TRIGGER outbox_delivery_inserted
  AFTER INSERT ON outbox_delivery
  FOR EACH STATEMENT EXECUTE FUNCTION outbox_delivery_notify();

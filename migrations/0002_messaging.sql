-- Facts, their deliveries, the inbox, and the probe (docs/plan-messaging.md).

CREATE TYPE aggregate_type AS ENUM ('probe');
CREATE TYPE message_type AS ENUM ('probe_started', 'probe_echoed');
CREATE TYPE consumer_name AS ENUM ('probe_echo', 'probe_finish');

CREATE FUNCTION refuse_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: % refused', TG_TABLE_NAME, TG_OP;
END $$;

-- Facts: immutable, so append-only.
CREATE TABLE outbox (
  seq             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id              uuid NOT NULL UNIQUE,
  aggregate_type  aggregate_type NOT NULL,
  aggregate_id    uuid NOT NULL,
  message_type    message_type NOT NULL,
  schema_version  int NOT NULL CHECK (schema_version >= 1),
  payload         jsonb NOT NULL,
  created_at      timestamptz NOT NULL,
  actor_kind      actor_kind NOT NULL,
  actor_id        text NOT NULL
);

CREATE TRIGGER outbox_append_only
  BEFORE UPDATE OR DELETE ON outbox
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

-- One row per consumer of a fact, written with the fact.
CREATE TABLE outbox_delivery (
  message_id      uuid NOT NULL REFERENCES outbox (id),
  consumer        consumer_name NOT NULL,
  aggregate_type  aggregate_type NOT NULL,
  aggregate_id    uuid NOT NULL,
  seq             bigint NOT NULL,
  not_before      timestamptz NOT NULL,
  attempts        int NOT NULL DEFAULT 0,
  last_error      text,
  handled_at      timestamptz,
  PRIMARY KEY (message_id, consumer)
);

CREATE INDEX outbox_delivery_pending
  ON outbox_delivery (consumer, aggregate_type, aggregate_id, seq)
  WHERE handled_at IS NULL;

-- A delivery's identity never changes, and once handled it is final.
CREATE FUNCTION outbox_delivery_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'outbox_delivery rows are never deleted';
  END IF;
  IF OLD.handled_at IS NOT NULL THEN
    RAISE EXCEPTION 'delivery % to % is already handled',
      OLD.message_id, OLD.consumer;
  END IF;
  IF NEW.message_id <> OLD.message_id OR NEW.consumer <> OLD.consumer
     OR NEW.aggregate_type <> OLD.aggregate_type
     OR NEW.aggregate_id <> OLD.aggregate_id OR NEW.seq <> OLD.seq THEN
    RAISE EXCEPTION 'delivery % to %: identity columns are fixed',
      OLD.message_id, OLD.consumer;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER outbox_delivery_guarded
  BEFORE UPDATE OR DELETE ON outbox_delivery
  FOR EACH ROW EXECUTE FUNCTION outbox_delivery_guard();

-- What each consumer has handled: the dedupe a broker would also need.
CREATE TABLE inbox (
  consumer    consumer_name NOT NULL,
  message_id  uuid NOT NULL REFERENCES outbox (id),
  handled_at  timestamptz NOT NULL,
  PRIMARY KEY (consumer, message_id)
);

CREATE TRIGGER inbox_append_only
  BEFORE UPDATE OR DELETE ON inbox
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

-- The probe: a two-step flow that proves the worker runs (deploy check).
CREATE TYPE probe_state AS ENUM ('started', 'echoed', 'finished');

CREATE TABLE probe (
  id           uuid PRIMARY KEY,
  state        probe_state NOT NULL,
  created_at   timestamptz NOT NULL,
  echoed_at    timestamptz,
  finished_at  timestamptz
);

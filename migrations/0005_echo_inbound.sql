-- Inbound events for the echo flow, step 1: the inbox, decoded events,
-- milestones, push webhooks, the `none` mode and manual events
-- (docs/plan-messaging.md §12). Transitional with the echo flow.

ALTER TYPE aggregate_type ADD VALUE 'echo_account';
ALTER TYPE message_type ADD VALUE 'echo_inbox_stored';
ALTER TYPE message_type ADD VALUE 'echo_vendor_event_received';
ALTER TYPE message_type ADD VALUE 'echo_job_confirmed';
ALTER TYPE message_type ADD VALUE 'echo_job_archived';
ALTER TYPE consumer_name ADD VALUE 'echo_inbox_decode';
ALTER TYPE consumer_name ADD VALUE 'echo_event_apply';

CREATE TYPE echo_event_mode AS ENUM ('push', 'push_thin', 'poll', 'file', 'none');
CREATE TYPE echo_event_source AS ENUM ('vendor', 'manual');
CREATE TYPE echo_event_kind AS ENUM ('confirmed', 'archived');
CREATE TYPE echo_vendor_status AS ENUM ('none', 'confirmed', 'archived');

-- The defaults only fill rows that exist already; code always sets them.
ALTER TABLE echo_account
  ADD COLUMN event_mode echo_event_mode NOT NULL DEFAULT 'none',
  -- A made-up secret for a fake account. A real port names a key in .env
  -- instead (credentials_ref): secrets never sit in the database.
  ADD COLUMN webhook_secret text NOT NULL DEFAULT '';
ALTER TABLE echo_job
  ADD COLUMN vendor_status echo_vendor_status NOT NULL DEFAULT 'none';

-- Raw bodies as received, before any decoding; append-only. The same body
-- twice (a webhook resent, a page fetched again) is stored once.
CREATE TABLE echo_inbox (
  id           uuid PRIMARY KEY,
  account_id   uuid NOT NULL REFERENCES echo_account (id),
  mode         echo_event_mode NOT NULL,
  body_sha256  text NOT NULL,
  body         bytea NOT NULL,
  received_at  timestamptz NOT NULL,
  UNIQUE (account_id, body_sha256)
);

CREATE TRIGGER echo_inbox_append_only
  BEFORE UPDATE OR DELETE ON echo_inbox
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

-- Decoded events per job, from the vendor or recorded by hand; once each.
-- Who recorded a manual event is the actor on its outbox fact.
CREATE TABLE echo_job_event (
  echo_job_id      uuid NOT NULL REFERENCES echo_job (id),
  vendor_event_id  text NOT NULL,
  kind             echo_event_kind NOT NULL,
  occurred_at      timestamptz NOT NULL,
  source           echo_event_source NOT NULL,
  inbox_id         uuid REFERENCES echo_inbox (id),
  recorded_at      timestamptz NOT NULL,
  PRIMARY KEY (echo_job_id, vendor_event_id),
  CHECK ((source = 'vendor') = (inbox_id IS NOT NULL))
);

CREATE TRIGGER echo_job_event_append_only
  BEFORE UPDATE OR DELETE ON echo_job_event
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

-- A status first reached: the milestone fact is emitted once, by this key.
CREATE TABLE echo_job_milestone (
  echo_job_id  uuid NOT NULL REFERENCES echo_job (id),
  status       echo_vendor_status NOT NULL CHECK (status <> 'none'),
  reached_at   timestamptz NOT NULL,
  PRIMARY KEY (echo_job_id, status)
);

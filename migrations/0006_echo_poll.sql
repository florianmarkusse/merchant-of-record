-- Inbound events for the echo flow, step 2: polling and thin webhooks
-- (docs/plan-messaging.md §12). Transitional with the echo flow.

ALTER TYPE message_type ADD VALUE 'echo_poll_due';
ALTER TYPE message_type ADD VALUE 'echo_events_wanted';
ALTER TYPE consumer_name ADD VALUE 'echo_tracking_start';
ALTER TYPE consumer_name ADD VALUE 'echo_poll';

-- How long a `poll` account waits between polls of one job. Time only
-- starts a poll; whether it is still wanted is decided under the job lock.
ALTER TABLE echo_account
  ADD COLUMN poll_interval_s int NOT NULL DEFAULT 300 CHECK (poll_interval_s > 0);

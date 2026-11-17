-- Inbound events for the echo flow, step 3: report files (docs/plan-messaging.md
-- §12). Transitional with the echo flow.

ALTER TYPE message_type ADD VALUE 'echo_report_due';
ALTER TYPE consumer_name ADD VALUE 'echo_report_fetch';

-- Per `file` account: the last report stored, and the one being fetched
-- (named by the vendor's listing, stored, then moved to last_report).
CREATE TABLE echo_report_cursor (
  account_id      uuid PRIMARY KEY REFERENCES echo_account (id),
  last_report     text,
  pending_report  text
);

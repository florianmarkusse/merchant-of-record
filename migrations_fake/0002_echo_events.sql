-- The fake echo vendor reports what happens to an echo (docs/plan-messaging.md
-- §12.6): vendor-side accounts, webhook subscriptions, and released events.

ALTER TABLE echo ADD COLUMN vendor_account text NOT NULL DEFAULT '';

CREATE TYPE echo_webhook_payload AS ENUM ('full', 'thin');
CREATE TYPE echo_event_kind AS ENUM ('confirmed', 'archived');
CREATE TYPE echo_push_state AS ENUM ('not_subscribed', 'delivered', 'failed');

-- As registered on a vendor's dashboard: where to push, and the secret to
-- sign with.
CREATE TABLE echo_webhook (
  flavour         echo_flavour NOT NULL,
  vendor_account  text NOT NULL,
  url             text NOT NULL,
  secret          text NOT NULL,
  payload         echo_webhook_payload NOT NULL,
  PRIMARY KEY (flavour, vendor_account)
);

-- Events released so far, in release order.
CREATE TABLE echo_event (
  seq          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id           text NOT NULL UNIQUE,
  echo_id      text NOT NULL REFERENCES echo (id),
  kind         echo_event_kind NOT NULL,
  occurred_at  timestamptz NOT NULL,
  push         echo_push_state NOT NULL,
  UNIQUE (echo_id, kind)
);

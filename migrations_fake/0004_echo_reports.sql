-- Report files: each holds the events released since the account's
-- previous report, cut when a test or dev page asks.

CREATE TABLE echo_report (
  name            text PRIMARY KEY,           -- sortable: r000001, r000002, …
  flavour         echo_flavour NOT NULL,
  vendor_account  text NOT NULL,
  body            text NOT NULL,              -- CSV
  cut_at          timestamptz NOT NULL
);

ALTER TABLE echo_event ADD COLUMN report text REFERENCES echo_report (name);

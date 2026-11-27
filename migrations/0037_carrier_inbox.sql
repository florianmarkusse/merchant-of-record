-- What carriers send us, stored raw before decoding (docs/plan-shipments.md
-- §7, 6b step 2), and the events no shipment of ours has (yet); and the PSP
-- events naming no payment of ours, recorded instead of failing their
-- delivery (docs/plan-payments.md §4).

-- Raw bodies as received, append-only, as psp_inbox. The body hash only
-- means "these bytes are stored already"; the guard against a duplicate
-- event is the carrier's event id. `mode` is how the bytes came: a fat or
-- thin push, or a poll page.
CREATE TABLE carrier_inbox (
  id           uuid PRIMARY KEY,
  account_id   uuid NOT NULL REFERENCES carrier_account (id),
  mode         carrier_tracking_mode NOT NULL,
  body_sha256  text NOT NULL,
  body         bytea NOT NULL,
  received_at  timestamptz NOT NULL,
  UNIQUE (account_id, body_sha256)
);

CREATE TRIGGER carrier_inbox_append_only
  BEFORE UPDATE OR DELETE ON carrier_inbox
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

-- An event (or a thin notice) for a tracking number no shipment of this
-- account had when it was decoded: once per carrier event id. A label we
-- learn of later (a lost booking answer) takes its events from here
-- (`carrier_event_match`); one that never becomes ours (an extra label
-- cancelled) stays for a person. Whether it matched since is read from
-- `shipment`, never written here.
CREATE TABLE carrier_event_unmatched (
  account_id        uuid NOT NULL REFERENCES carrier_account (id),
  carrier_event_id  text NOT NULL,
  tracking_number   text NOT NULL,
  inbox_id          uuid NOT NULL REFERENCES carrier_inbox (id),
  recorded_at       timestamptz NOT NULL,
  PRIMARY KEY (account_id, carrier_event_id)
);
CREATE INDEX carrier_event_unmatched_by_tracking_number
  ON carrier_event_unmatched (account_id, tracking_number);

CREATE TRIGGER carrier_event_unmatched_append_only
  BEFORE UPDATE OR DELETE ON carrier_event_unmatched
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

-- A PSP event (or thin notice) whose reference names no payment of this
-- account. Our attempt exists before the PSP hears of it, so only a PSP
-- mistake makes one: it never matches later, and a person looks at it.
CREATE TABLE psp_event_unmatched (
  account_id       uuid NOT NULL REFERENCES psp_account (id),
  vendor_event_id  text NOT NULL,
  reference        text NOT NULL,
  inbox_id         uuid NOT NULL REFERENCES psp_inbox (id),
  recorded_at      timestamptz NOT NULL,
  PRIMARY KEY (account_id, vendor_event_id)
);

CREATE TRIGGER psp_event_unmatched_append_only
  BEFORE UPDATE OR DELETE ON psp_event_unmatched
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

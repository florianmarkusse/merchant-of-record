-- Four places the schema had drifted from the conventions (audit of
-- 21-Nov-2026).

-- 1. A PSP event's amount is stored with its currency, as every amount is
-- (AGENTS.md, Money and Ledger). The PSP names it in the event; rows from
-- before take it from the fact that carried the event, else the attempt's.
ALTER TABLE payment_event ADD COLUMN currency currency;

ALTER TABLE payment_event DISABLE TRIGGER payment_event_append_only;
UPDATE payment_event e
SET currency = coalesce(
  (SELECT (o.payload ->> 'currency')::currency
   FROM outbox o
   WHERE o.message_type = 'psp_event_received'
     AND (o.payload ->> 'payment_attempt_id')::uuid = e.attempt_id
     AND o.payload ->> 'vendor_event_id' = e.vendor_event_id
   ORDER BY o.seq
   LIMIT 1),
  (SELECT a.currency FROM payment_attempt a WHERE a.id = e.attempt_id)
);
ALTER TABLE payment_event ENABLE TRIGGER payment_event_append_only;

ALTER TABLE payment_event ALTER COLUMN currency SET NOT NULL;

-- 2. quote_note held two things told apart by a text `kind`: a table per
-- thing instead, and the reasons a closed set.
CREATE TYPE not_offered_reason AS ENUM (
  'service_not_confirmed',
  'card_not_in_force',
  'card_not_confirmed',
  'no_zone',
  'zone_not_confirmed',
  'over_weight',
  'price_range',
  'category_restricted',
  'surcharge_not_confirmed',
  'fuel_unknown',
  'not_duty_paid',
  'fx_missing',
  'carrier_not_served',
  'carrier_needs_person',
  'placeholder'
);

-- A fee rule standing in for a decision, as the quote named it.
CREATE TABLE quote_placeholder (
  quote_id        uuid NOT NULL REFERENCES quote (id),
  placeholder_no  int NOT NULL CHECK (placeholder_no >= 0),
  key             text NOT NULL,
  PRIMARY KEY (quote_id, placeholder_no)
);

-- A service the quote did not offer, and why.
CREATE TABLE quote_not_offered (
  quote_id         uuid NOT NULL REFERENCES quote (id),
  not_offered_no   int NOT NULL CHECK (not_offered_no >= 0),
  key              text NOT NULL,
  reason           not_offered_reason NOT NULL,
  PRIMARY KEY (quote_id, not_offered_no)
);

INSERT INTO quote_placeholder (quote_id, placeholder_no, key)
SELECT quote_id, row_number() OVER (PARTITION BY quote_id ORDER BY note_no) - 1, key
FROM quote_note WHERE kind = 'placeholder';

INSERT INTO quote_not_offered (quote_id, not_offered_no, key, reason)
SELECT quote_id, row_number() OVER (PARTITION BY quote_id ORDER BY note_no) - 1,
       key, reason::not_offered_reason
FROM quote_note WHERE kind = 'not_offered';

DROP TABLE quote_note;

CREATE TRIGGER quote_placeholder_append_only BEFORE UPDATE OR DELETE
  ON quote_placeholder FOR EACH ROW EXECUTE FUNCTION quote_flow_append_only();
CREATE TRIGGER quote_not_offered_append_only BEFORE UPDATE OR DELETE
  ON quote_not_offered FOR EACH ROW EXECUTE FUNCTION quote_flow_append_only();

-- 3. An order is a fact once made: append-only, as every other record of
-- one is.
CREATE TRIGGER order_append_only
  BEFORE UPDATE OR DELETE ON order_
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

-- 4. Not a change: say why these two columns are numeric where every other
-- fraction is a bigint pair.
COMMENT ON COLUMN quote_part.exact_numerator IS
  'numeric on purpose: an exact part is a product of a price (up to 10^15 '
  'minor units), FX and tax rates, and can outgrow bigint';
COMMENT ON COLUMN quote_part.exact_denominator IS
  'numeric on purpose: see exact_numerator';

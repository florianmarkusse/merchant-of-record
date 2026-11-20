-- The invoice and the merchant's KYB state and settings, step 5b
-- (docs/plan-payments.md §6.1).

-- KYB is ours, never the merchant's: the pay step refuses a new attempt
-- unless `approved`, in every environment. The column is what the pay step
-- reads; `merchant_kyb_change` is who changed it and when.
CREATE TYPE merchant_kyb_state AS ENUM ('pending', 'approved', 'suspended');

ALTER TABLE merchant
  ADD COLUMN kyb_state merchant_kyb_state NOT NULL DEFAULT 'pending',
  -- The merchant's own settings, set through the API with its key.
  ADD COLUMN support_email text CHECK (support_email <> ''),
  ADD COLUMN settings_changed_at timestamptz,
  ADD COLUMN settings_actor_kind actor_kind,
  ADD COLUMN settings_actor_id text,
  ADD CHECK ((settings_changed_at IS NULL) = (settings_actor_kind IS NULL)
             AND (settings_changed_at IS NULL) = (settings_actor_id IS NULL));

CREATE TABLE merchant_kyb_change (
  id           uuid PRIMARY KEY,
  merchant_id  uuid NOT NULL REFERENCES merchant (id),
  state        merchant_kyb_state NOT NULL,
  changed_at   timestamptz NOT NULL,
  actor_kind   actor_kind NOT NULL,
  actor_id     text NOT NULL
);
CREATE INDEX merchant_kyb_change_by_merchant ON merchant_kyb_change (merchant_id, id);

CREATE TRIGGER merchant_kyb_change_append_only
  BEFORE UPDATE OR DELETE ON merchant_kyb_change
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

-- Where the buyer lands once paid, per session: our order page, or the
-- session's return_url with `session_id` added. The code always names it.
CREATE TYPE after_payment AS ENUM ('our_page', 'merchant_page');

ALTER TABLE checkout_session
  ADD COLUMN after_payment after_payment NOT NULL DEFAULT 'our_page';
ALTER TABLE checkout_session ALTER COLUMN after_payment DROP DEFAULT;

-- One counter per numbering series. The series themselves (prefix, year,
-- width, entity) are plain data in src/mor/invoices/numbering.py, so a new
-- series is a new entry there; its counter row is inserted on first use.
-- `next_number` is taken under this row's lock in the transaction that
-- inserts the invoice, so a rollback gives it back and the numbers run
-- without a gap. Each invoice stores its number text whole, so a format
-- change never touches an issued number.
CREATE TABLE invoice_series (
  code         text PRIMARY KEY CHECK (code ~ '^[A-Z][A-Z0-9-]*$'),
  next_number  bigint NOT NULL CHECK (next_number >= 1)
);

-- A snapshot, never re-derived and never changed. Name and address are
-- copied: an invoice is kept for its legal retention period, the session's
-- address may not be. Email and phone are not copied.
CREATE TABLE invoice (
  id                   uuid PRIMARY KEY,
  order_id             uuid NOT NULL UNIQUE REFERENCES order_ (id),
  series               text NOT NULL REFERENCES invoice_series (code),
  number               bigint NOT NULL CHECK (number >= 1),
  number_text          text NOT NULL UNIQUE,
  issued_at            timestamptz NOT NULL,
  paid_at              timestamptz NOT NULL,
  currency             currency NOT NULL,
  total_minor          bigint NOT NULL CHECK (total_minor >= 0),
  seller_name          text NOT NULL,
  seller_address       text NOT NULL,
  seller_tax_label     text,
  seller_tax_id        text,
  merchant_legal_name  text NOT NULL,
  buyer_name           text NOT NULL,
  buyer_line1          text NOT NULL,
  buyer_line2          text,
  buyer_city           text NOT NULL,
  buyer_region         text,
  buyer_postcode       text,
  buyer_country        char(2) NOT NULL,
  UNIQUE (series, number),
  CHECK ((seller_tax_label IS NULL) = (seller_tax_id IS NULL))
);

-- `goods`: one per order line, our fee inside; `shipping`: one;
-- `tax`: what we collect and file; `border`: duty and tax paid at the
-- border on the buyer's behalf.
CREATE TYPE invoice_section AS ENUM ('goods', 'shipping', 'tax', 'border');

CREATE TABLE invoice_line (
  invoice_id    uuid NOT NULL REFERENCES invoice (id),
  line_no       int NOT NULL CHECK (line_no >= 0),
  section       invoice_section NOT NULL,
  description   text NOT NULL CHECK (description <> ''),
  quantity      int CHECK (quantity > 0),
  amount_minor  bigint NOT NULL,
  rule_text     text,
  source        text,
  PRIMARY KEY (invoice_id, line_no)
);

CREATE TRIGGER invoice_append_only
  BEFORE UPDATE OR DELETE ON invoice
  FOR EACH ROW EXECUTE FUNCTION refuse_change();
CREATE TRIGGER invoice_line_append_only
  BEFORE UPDATE OR DELETE ON invoice_line
  FOR EACH ROW EXECUTE FUNCTION refuse_change();

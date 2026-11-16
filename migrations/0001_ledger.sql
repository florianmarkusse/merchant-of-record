-- The double-entry ledger (plan-skeleton.md §6, accounts from
-- plan-architecture.md §9). Append-only; balances are derived, never stored.

CREATE TYPE currency AS ENUM ('VND', 'IDR', 'SGD', 'MYR', 'THB', 'PHP', 'USD', 'EUR');

CREATE TYPE account_kind AS ENUM (
  'psp_clearing',
  'tax_payable',
  'duties_payable',
  'shipping_payable',
  'merchant_payable',
  'psp_fee_collected',
  'psp_fee_variance',
  'revenue_mor_margin',
  'courier_payable',
  'courier_variance',
  'reserve',
  'rounding'
);

CREATE TYPE actor_kind AS ENUM ('system', 'staff', 'merchant_api_key');

CREATE TABLE journal_entry (
  id               uuid PRIMARY KEY,           -- uuid7, made in code
  idempotency_key  text NOT NULL UNIQUE,       -- kept for good, never expires
  description      text NOT NULL,
  effective_at     timestamptz NOT NULL,
  recorded_at      timestamptz NOT NULL,
  actor_kind       actor_kind NOT NULL,
  actor_id         text NOT NULL
);

CREATE TABLE journal_line (
  entry_id       uuid NOT NULL REFERENCES journal_entry (id),
  line_no        int NOT NULL,
  account_kind   account_kind NOT NULL,
  account_scope  text NOT NULL,                -- merchant id, country, courier, or ''
  amount_minor   bigint NOT NULL CHECK (amount_minor <> 0),  -- debit +, credit -
  currency       currency NOT NULL,
  PRIMARY KEY (entry_id, line_no)
);

CREATE INDEX journal_line_account
  ON journal_line (account_kind, account_scope, currency);

-- Balance, checked at commit: at least two lines, and the lines sum to zero
-- per currency. Deferred, so an entry and its lines are inserted in any
-- order within one transaction.
CREATE FUNCTION ledger_check_entry(checked_entry uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
  line_count int;
  unbalanced currency;
BEGIN
  SELECT count(*) INTO line_count FROM journal_line WHERE entry_id = checked_entry;
  IF line_count < 2 THEN
    RAISE EXCEPTION 'journal entry % has % lines, needs at least 2',
      checked_entry, line_count;
  END IF;
  SELECT currency INTO unbalanced
  FROM journal_line
  WHERE entry_id = checked_entry
  GROUP BY currency
  HAVING sum(amount_minor) <> 0
  LIMIT 1;
  IF FOUND THEN
    RAISE EXCEPTION 'journal entry % does not balance in %', checked_entry, unbalanced;
  END IF;
END $$;

CREATE FUNCTION ledger_check_entry_row() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM ledger_check_entry(NEW.id);
  RETURN NULL;
END $$;

CREATE FUNCTION ledger_check_line_row() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM ledger_check_entry(NEW.entry_id);
  RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER journal_entry_balanced
  AFTER INSERT ON journal_entry
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION ledger_check_entry_row();

CREATE CONSTRAINT TRIGGER journal_line_balanced
  AFTER INSERT ON journal_line
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION ledger_check_line_row();

-- Append-only: a correction is a new reversing entry. TRUNCATE is left to
-- privileges: the app's prod role will not be granted it (the test suite
-- truncates its own database between tests).
CREATE FUNCTION ledger_refuse_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: % refused', TG_TABLE_NAME, TG_OP;
END $$;

CREATE TRIGGER journal_entry_append_only
  BEFORE UPDATE OR DELETE ON journal_entry
  FOR EACH ROW EXECUTE FUNCTION ledger_refuse_change();

CREATE TRIGGER journal_line_append_only
  BEFORE UPDATE OR DELETE ON journal_line
  FOR EACH ROW EXECUTE FUNCTION ledger_refuse_change();

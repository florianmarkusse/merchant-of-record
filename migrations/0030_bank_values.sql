-- The enum values the bank statement needs (docs/plan-payments.md §7, the
-- bank statement), in a file of their own: a value cannot be used in the
-- transaction that adds it.

-- Money in one of our bank accounts, and what the bank shows that nothing
-- explains yet, each per bank account.
ALTER TYPE account_kind ADD VALUE 'bank';
ALTER TYPE account_kind ADD VALUE 'bank_suspense';

ALTER TYPE aggregate_type ADD VALUE 'bank_account';

ALTER TYPE message_type ADD VALUE 'bank_statement_due';
ALTER TYPE message_type ADD VALUE 'bank_inbox_stored';
ALTER TYPE message_type ADD VALUE 'bank_entry_received';

ALTER TYPE consumer_name ADD VALUE 'bank_statement_fetch';
ALTER TYPE consumer_name ADD VALUE 'bank_inbox_decode';
ALTER TYPE consumer_name ADD VALUE 'bank_entry_match';

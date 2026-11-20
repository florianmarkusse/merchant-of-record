-- The enum values step 5b needs (docs/plan-payments.md §6.1), in a file of
-- their own: a value cannot be used in the transaction that adds it.

ALTER TYPE message_type ADD VALUE 'invoice_issued';
ALTER TYPE consumer_name ADD VALUE 'invoice_issue';

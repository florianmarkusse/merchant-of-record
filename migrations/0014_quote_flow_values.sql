-- New enum values for the quote flow (docs/plan-quote.md §5, 4d-2), in a
-- file of their own: a value added in a transaction cannot be used in the
-- same one, and 0015 uses them in its checks.

ALTER TYPE checkout_session_state ADD VALUE 'quoting';
ALTER TYPE checkout_session_state ADD VALUE 'quoted';
ALTER TYPE checkout_session_state ADD VALUE 'refused';
ALTER TYPE message_type ADD VALUE 'quote_requested';
ALTER TYPE message_type ADD VALUE 'quote_made';
ALTER TYPE message_type ADD VALUE 'quote_refused';
ALTER TYPE message_type ADD VALUE 'quote_inputs_arrived';
ALTER TYPE message_type ADD VALUE 'checkout_session_expiry_due';
ALTER TYPE message_type ADD VALUE 'checkout_session_expired';
ALTER TYPE consumer_name ADD VALUE 'quote_wait_notify';
ALTER TYPE consumer_name ADD VALUE 'quote_build';
ALTER TYPE consumer_name ADD VALUE 'checkout_session_expire';

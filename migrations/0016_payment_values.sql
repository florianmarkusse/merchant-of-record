-- The enum values payments need (docs/plan-payments.md, 5a), in a file of
-- their own: a value cannot be used in the transaction that adds it.

ALTER TYPE checkout_session_state ADD VALUE 'paying';
ALTER TYPE checkout_session_state ADD VALUE 'completed';

ALTER TYPE aggregate_type ADD VALUE 'payment_attempt';
ALTER TYPE aggregate_type ADD VALUE 'psp_account';
ALTER TYPE aggregate_type ADD VALUE 'order';

ALTER TYPE message_type ADD VALUE 'payment_attempt_created';
ALTER TYPE message_type ADD VALUE 'payment_action_required';
ALTER TYPE message_type ADD VALUE 'payment_authorised';
ALTER TYPE message_type ADD VALUE 'payment_captured';
ALTER TYPE message_type ADD VALUE 'payment_declined';
ALTER TYPE message_type ADD VALUE 'payment_expired';
ALTER TYPE message_type ADD VALUE 'payment_cancelled';
ALTER TYPE message_type ADD VALUE 'payment_needs_person';
ALTER TYPE message_type ADD VALUE 'payment_cancel_wanted';
ALTER TYPE message_type ADD VALUE 'payment_poll_due';
ALTER TYPE message_type ADD VALUE 'payment_events_wanted';
ALTER TYPE message_type ADD VALUE 'psp_inbox_stored';
ALTER TYPE message_type ADD VALUE 'psp_event_received';
ALTER TYPE message_type ADD VALUE 'checkout_session_completed';
ALTER TYPE message_type ADD VALUE 'order_paid';

ALTER TYPE consumer_name ADD VALUE 'psp_payment_create';
ALTER TYPE consumer_name ADD VALUE 'psp_payment_capture';
ALTER TYPE consumer_name ADD VALUE 'psp_payment_cancel';
ALTER TYPE consumer_name ADD VALUE 'psp_payment_poll';
-- A thin webhook's fetch has a consumer of its own: deliveries run in order
-- per consumer and attempt, and a poll scheduled for a QR's expiry must not
-- hold back a fetch that is due now.
ALTER TYPE consumer_name ADD VALUE 'psp_payment_fetch';
ALTER TYPE consumer_name ADD VALUE 'psp_inbox_decode';
ALTER TYPE consumer_name ADD VALUE 'payment_event_apply';
ALTER TYPE consumer_name ADD VALUE 'order_create';
ALTER TYPE consumer_name ADD VALUE 'session_payment_ended';

-- The buyer's currency on one side, the merchant's on the other: the
-- capture entry converts through it (docs/plan-payments.md §5).
ALTER TYPE account_kind ADD VALUE 'fx_position';

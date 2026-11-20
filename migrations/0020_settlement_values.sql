-- The enum values settlement needs (docs/plan-payments.md §7.1, 5c-1), in
-- a file of their own: a value cannot be used in the transaction that adds
-- it.

ALTER TYPE aggregate_type ADD VALUE 'settlement_line';
ALTER TYPE aggregate_type ADD VALUE 'settlement_batch';

ALTER TYPE message_type ADD VALUE 'psp_settlement_due';
ALTER TYPE message_type ADD VALUE 'settlement_line_received';
ALTER TYPE message_type ADD VALUE 'settlement_batch_received';

ALTER TYPE consumer_name ADD VALUE 'psp_settlement_fetch';
ALTER TYPE consumer_name ADD VALUE 'settlement_line_book';
ALTER TYPE consumer_name ADD VALUE 'settlement_payout_book';

-- What a PSP says it paid out and our bank has not shown yet (question 9
-- of 20-Nov-2026): the bank statement import (5c-2) moves it to `bank`.
ALTER TYPE account_kind ADD VALUE 'payout_in_transit';

ALTER TYPE psp_inbound_mode ADD VALUE 'settlement_file';
ALTER TYPE psp_inbound_mode ADD VALUE 'settlement_page';

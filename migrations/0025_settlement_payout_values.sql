-- The enum values settlement by payout needs
-- (docs/plan-settlement-reconciliation.md), in a file of their own: a value
-- cannot be used in the transaction that adds it. Renamed, not added: there
-- is no settlement data on prod (confirmed 23-Nov-2026), so the old names
-- need no transition.

ALTER TYPE aggregate_type RENAME VALUE 'settlement_batch' TO 'settlement_payout';
ALTER TYPE message_type
  RENAME VALUE 'settlement_batch_received' TO 'settlement_payout_reported';

ALTER TYPE consumer_name ADD VALUE 'settlement_payout_reconcile';

-- A payout object (Stripe's shape); a page of a payout's lines stays
-- `settlement_page`.
ALTER TYPE psp_inbound_mode ADD VALUE 'settlement_payout';

-- Part of a batch's net held back for, or paid from, another batch
-- (Adyen's Balancetransfer): it books nothing and counts towards its
-- payout, so its state is `counted`.
ALTER TYPE settlement_line_type ADD VALUE 'balance_transfer';
ALTER TYPE settlement_line_state ADD VALUE 'counted';

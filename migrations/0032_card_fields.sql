-- Card fields, step 5c-3 (docs/plan-payments.md §4, card fields): what our
-- page hands the PSP's script to mount its card form, beside the hosted
-- page's URL. Only a card payment's action has one, and only from a PSP
-- that offers card fields.
ALTER TABLE payment_attempt ADD COLUMN action_fields_secret text;
ALTER TABLE payment_attempt
  ADD CHECK (action_fields_secret IS NULL OR action_kind = 'redirect');

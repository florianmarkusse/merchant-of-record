-- One name for the company that carries a parcel: carrier, never courier
-- (decided 24-Nov-2026, AGENTS.md). Renames only; no row changes but the
-- fake carrier's URL, which moved from /_fake/courier/ to /_fake/carrier/.

ALTER TYPE courier_provider RENAME TO carrier_provider;
ALTER TABLE courier_account RENAME TO carrier_account;

ALTER TABLE carrier_account RENAME CONSTRAINT courier_account_pkey TO carrier_account_pkey;
ALTER TABLE carrier_account RENAME CONSTRAINT courier_account_carrier_key TO carrier_account_carrier_key;
ALTER TABLE carrier_account RENAME CONSTRAINT courier_account_check TO carrier_account_check;
ALTER TABLE carrier_account RENAME CONSTRAINT courier_account_timeout_ms_check TO carrier_account_timeout_ms_check;
ALTER TABLE carrier_account RENAME CONSTRAINT courier_account_price_valid_for_s_check TO carrier_account_price_valid_for_s_check;
ALTER TABLE carrier_account RENAME CONSTRAINT courier_account_id_not_null TO carrier_account_id_not_null;
ALTER TABLE carrier_account RENAME CONSTRAINT courier_account_carrier_not_null TO carrier_account_carrier_not_null;
ALTER TABLE carrier_account RENAME CONSTRAINT courier_account_provider_not_null TO carrier_account_provider_not_null;
ALTER TABLE carrier_account RENAME CONSTRAINT courier_account_base_url_not_null TO carrier_account_base_url_not_null;
ALTER TABLE carrier_account RENAME CONSTRAINT courier_account_timeout_ms_not_null TO carrier_account_timeout_ms_not_null;
ALTER TABLE carrier_account RENAME CONSTRAINT courier_account_price_valid_for_s_not_null TO carrier_account_price_valid_for_s_not_null;
ALTER TABLE carrier_account RENAME CONSTRAINT courier_account_price_expiry_not_null TO carrier_account_price_expiry_not_null;
ALTER TABLE carrier_account RENAME CONSTRAINT courier_account_kind_not_null TO carrier_account_kind_not_null;

UPDATE carrier_account
SET base_url = replace(base_url, '/_fake/courier/', '/_fake/carrier/')
WHERE base_url LIKE '%/_fake/courier/%';

ALTER TYPE account_kind RENAME VALUE 'courier_payable' TO 'carrier_payable';
ALTER TYPE account_kind RENAME VALUE 'courier_variance' TO 'carrier_variance';

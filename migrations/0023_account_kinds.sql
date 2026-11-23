-- Every provider account says which environments may use it (decided
-- 19-Nov-2026, plan-architecture §6): couriers, duty vendors and the echo
-- vendor get the `kind` psp_account has, and every registry checks it with
-- `mor.provider_accounts.require_allowed`. Only fake providers exist for
-- these, so every row so far is a fake account.
ALTER TABLE courier_account
  ADD COLUMN kind provider_account_kind NOT NULL DEFAULT 'fake';
ALTER TABLE courier_account ALTER COLUMN kind DROP DEFAULT;

ALTER TABLE duty_vendor_account
  ADD COLUMN kind provider_account_kind NOT NULL DEFAULT 'fake';
ALTER TABLE duty_vendor_account ALTER COLUMN kind DROP DEFAULT;

ALTER TABLE echo_account
  ADD COLUMN kind provider_account_kind NOT NULL DEFAULT 'fake';
ALTER TABLE echo_account ALTER COLUMN kind DROP DEFAULT;

-- A fake provider is always a fake account, so the kind check alone keeps
-- the fakes out of prod (it replaces the registries' "fake in prod" check).
ALTER TABLE courier_account
  ADD CHECK (provider <> 'fake' OR kind = 'fake');
ALTER TABLE duty_vendor_account
  ADD CHECK (provider <> 'fake' OR kind = 'fake');
ALTER TABLE psp_account
  ADD CHECK (provider <> 'fake' OR kind = 'fake');
-- Every echo provider is a fake (the echo flow is transitional,
-- docs/plan-messaging.md §10).
ALTER TABLE echo_account ADD CHECK (kind = 'fake');

-- A service's carrier is named so, as in the code and the services file
-- (decided 25-Nov-2026); "provider" is left to the module that speaks to
-- a vendor (`carrier_provider`) and to the vendor directory in
-- docs/markets/providers.csv, whose ids a carrier code is.
ALTER TABLE shipping_service RENAME COLUMN provider TO carrier;
ALTER TABLE shipping_service
  RENAME CONSTRAINT shipping_service_provider_not_null TO shipping_service_carrier_not_null;

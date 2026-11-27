-- The enum values the carrier inbox needs (docs/plan-shipments.md §7, 6b
-- step 2), in a file of their own: a value cannot be used in the
-- transaction that adds it.

ALTER TYPE aggregate_type ADD VALUE 'carrier_account';

ALTER TYPE message_type ADD VALUE 'carrier_inbox_stored';
ALTER TYPE message_type ADD VALUE 'carrier_event_received';
ALTER TYPE message_type ADD VALUE 'carrier_event_unmatched';
ALTER TYPE message_type ADD VALUE 'shipment_events_wanted';

ALTER TYPE consumer_name ADD VALUE 'carrier_inbox_decode';
ALTER TYPE consumer_name ADD VALUE 'carrier_event_match';

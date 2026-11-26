-- The enum values booking a shipment needs (docs/plan-shipments.md, 6a-2),
-- in a file of their own: a value cannot be used in the transaction that
-- adds it.

ALTER TYPE aggregate_type ADD VALUE 'shipment';

ALTER TYPE message_type ADD VALUE 'shipment_created';
ALTER TYPE message_type ADD VALUE 'shipment_label_booked';
ALTER TYPE message_type ADD VALUE 'shipment_label_failed';
ALTER TYPE message_type ADD VALUE 'shipment_needs_person';
ALTER TYPE message_type ADD VALUE 'shipment_booking_retried';

ALTER TYPE consumer_name ADD VALUE 'shipment_create';
ALTER TYPE consumer_name ADD VALUE 'carrier_book';

# Shipments: booking, tracking and milestones (slice 6)

Status: 6a-1 and 6a-2 are built (§4, §6); 6b, tracking, is next, then
6c.

Applies `plan-architecture.md` §5, §6, §7 and §9 (booking, tracking) to
shipments against the fake carrier in every mode. Slice 5 left a paid
order (`order_paid`); this slice books its label, follows the parcel and
records each milestone once. The echo flow retires with it.

Out of scope (later slices): the carrier invoice and its ledger entries
(slice 7), returns, refunds and cancelling a shipment for a refund
(slice 8), releasing the merchant's money on delivery (slice 9), a real
carrier module (slice 10), the merchant dashboard (merchant surfaces).
Booking and tracking move no money, so this slice writes no journal
entries.

## 1. Steps

- **6a-1: what checkout refuses (built).** Apparel only, our
  classification per SKU with its staff page, the merchant's ship-from
  address (§4). No messaging; it makes every sold line shippable before
  6a-2 ships it.
- **6a-2: booking (built).** Carrier account capabilities, the shipment
  with its customs snapshot and the ship-from it was made with on
  `order_paid`, quotes offering only services from the ship-from's
  country, the `carrier_book` effect under each duplicate guard, label and
  commercial invoice stored, a refused booking, cancelling the extra
  labels a `compensable` resend made, a staff shipments page, the label
  through the orders API.
- **6b: tracking (next).** The carrier inbox in every mode (fat and thin
  webhooks, polling), shipment events, the status derived from the set of
  events, milestones and their facts, the order page and orders API
  showing them.
- **6c: the echo flow retired.** Its code, routes, fuzzer machine and
  `typed-ids` ignores go; its tables are dropped, its enum values stay
  (`plan-messaging.md` §9). The shipment machine takes over its reach
  milestones (the `compensable` guard, a status from a set of events).

## 2. Accounts and capabilities

`carrier_account` gains the capability columns `psp_account` already has,
so feature code switches on the account's values, never on a provider:

- `book_guard duplicate_guard` (`plan-architecture.md` §5). Booking may be
  `compensable`: a label never handed over costs nothing and is
  cancelled. Cancel needs no guard: cancelling a label twice leaves it
  cancelled.
- `tracking_mode carrier_tracking_mode`: `push_fat`, `push_thin`, `poll`
  (DHL Express polls, FedEx pushes signed webhooks, `plan-logistics.md`
  §1).
- `poll_interval_s`, `webhook_secret` and `credentials_ref`, with the same
  CHECK as `psp_account` (a fake account has no `credentials_ref`).

One account per carrier stays (`UNIQUE (carrier)`): the quote's service
names the carrier, the carrier names the account. The seed makes one for
every carrier a route names, and spreads the wirings over them in carrier
order (`CARRIER_WIRINGS` in `mor.seed`), printing which carrier has which,
so the test setup exercises every mode; the fuzzer makes its own account
with a drawn guard. A routing row per merchant (`psp_route`'s shape) comes
with the first vendor sandbox account, as `plan-architecture.md` §6 says.

## 3. The fake carrier

`mor.fakes.carrier` grows from the rate call into a carrier, in the
echo fake's shape (flavour in the path, state in `fake_vendor`, events
released only when told):

```
POST A/rates                a live price (plan-quote.md §4)
POST A/shipments            book: label PDF, commercial invoice PDF,
                            tracking number, export declaration number
GET  A/shipments?reference  every label with our reference, live or
                            cancelled, with its documents
POST A/shipments/{tn}/cancel   `cancellable` only
POST /_fake/carrier/faults  {"operation", "fault"}: script the next call
                            of `rate`, `book`, `find` or `cancel` to fail
6b adds:
GET  A/tracking?cursor      one page of events (poll, thin webhooks)
POST A/events/release       next event of a shipment's script (dev page,
                            CLI, fuzzer); `all` plays the whole script
```

`A` is `/_fake/carrier/{flavour}/{account}`, the flavour one of `keyed`,
`cancellable` or `plain`, matching the account's `book_guard`. The
scenario comes from values in the request: a receiver whose first address
line starts with `REFUSE` is a refused address (a postcode is checked by
format at checkout, so it cannot carry the word), an item description
starting `REFUSE` is refused customs data; from 6b `LOST` and `RETURN`
pick those scripts, anything else is delivered. Faults: a 500 before or
after the work, a timeout after the work; from 6b a webhook sent twice or
with a bad signature, events released out of order. Its labels, numbered
from a sequence, are rows per reference: the fuzzer's proof that nothing
was booked twice. Its PDFs are one-page WeasyPrint renders of its own
markup.

## 4. What checkout refuses

A paid order must be shippable, so what a shipment needs is checked when
the session (or an estimate) is created, never after the buyer paid.

**Apparel only.** A line of any category but `apparel` is refused with
422 `category_not_offered`. The product gate and the other categories
wait until the whole flow is built (§12).

**Our classification.** Correct HS classification is the importer of
record's responsibility, and as merchant of record that is our side
(`plan-quote.md` decision 2), so we decide a code before a line is sold:

```
sku_classification  id, merchant_id, merchant_sku, hs_code,
                    actor_kind, actor_id, created_at
                    append-only; the newest per (merchant_id,
                    merchant_sku) is in force
```

- `merchant_sku` is required on every line; the merchant's `hs_code`
  stays optional and is only a hint.
- A line whose SKU has no classification is refused with 422
  `classification_needed`, naming the SKU. The session line stores the
  classification it used (`classification_id`) and our code (`hs_code`)
  beside the merchant's (`merchant_hs_code`), so a later
  reclassification never changes what was sold or declared.
- The quote, the estimate and the customs lines use our code, never the
  merchant's.
- Staff classify on `/staff/classifications`: a form for merchant, SKU
  and code, and the codes in force per merchant, each changeable. The
  person is recorded as the actor. Until the merchant surfaces, a
  merchant sends its new SKUs to us by hand; a list of refused SKUs
  comes with the dashboard. The seed classifies the Sandbox Shop's
  SKUs.

**The merchant's ship-from address.** The label needs one (the pickup
and the shipper field, `plan-logistics.md` §6). A `merchant_ship_from` row
per change, append-only, the newest in force; set by
`python -m mor.merchants ship-from mer_… …` and by the seed. The shipment
names the row in force at its creation, so a later change never alters a
booked waybill. Its country must be the origin of a service we offer: the
CLI checks that against the market data in force when it is set. The
quote and the estimate offer only services whose origin is the
ship-from's country (`routes_from` in `mor.quote.shipping`). The
shipment checks it once more when it is created (§6), since the address
can change between the quote and the payment.

A merchant without one cannot open a checkout session or ask for an
estimate: either answers 422 `ship_from_missing`. An order we cannot
ship is a refund waiting to happen, so it is refused before the buyer
pays, not held after.

## 5. The shipment

```
shipment               id, order_id, sequence (0 outbound; slice 8 adds
                       returns), carrier, carrier_account_id, service,
                       market_load_id (the routing version), parcel
                       columns, ship_from_id, receiver_address_id,
                       incoterm, booking_state, failure, detail,
                       tracking_status, tracking_number,
                       export_declaration_no, created_at, updated_at
                       UNIQUE (order_id, sequence)
shipment_customs_line  shipment_id, line_no, description, hs_code,
                       origin, quantity, value_minor, currency
                       (from checkout_session_line, frozen; append-only)
carrier_request        shipment_id, operation (book | cancel), number,
                       idempotency_key UNIQUE, state, sends,
                       target_tracking_number (a cancel's label), result
                       UNIQUE (shipment_id, operation, number)
shipment_document      shipment_id, kind (label | commercial_invoice),
                       content_type, body bytea; once per kind,
                       append-only
```

- The ship-from and the receiver are referenced, not copied: both tables
  are append-only, so the row in force when the shipment was made is its
  copy, and a later change never alters a booked waybill.
- `carrier_account_id` is empty only on a shipment that failed for want
  of an account (`no_carrier_account`); a person books it again once the
  account exists.
- `incoterm` is `ddp` when the chosen option charged the buyer something
  the border collects (the quote offers that only on a duty-paid service,
  whose carrier pays it and bills us), else `dap`: nothing is due.
- A customs line's value is the sold line's unit price times its
  quantity, in its currency, under our HS code.

Two closed sets, not the one list in `plan-logistics.md` §7, because they
are two machines that change independently (a scan can arrive while we
deduplicate labels):

- **Booking state**, which we drive: `booking → labelled`, with
  `deduplicating` (a `compensable` resend may have made extra labels),
  `label_failed` (a typed refusal, its reason stored) and `needs_person`
  (no answer on a `none` guard and the carrier does not find it).
- **Tracking status**, derived from events (§7): `none`, `picked_up`,
  `in_transit`, `held_at_customs`, `delivered`, `returning`, `returned`,
  `lost`.

`plan-logistics.md` §7 is rewritten to match when this is built.

## 6. Booking

```
order_paid ─▶ shipment_create (local, order lock): shipment `booking`,
              customs lines, request #1 not sent; shipment_created
shipment_created ┐
shipment_booking_retried ┴▶ carrier_book (effect, shipment lock)
    prepare:  still `booking`? mark the open request `sent`
    call:     book, with our shipment id as the shipper reference and
              the request id as the idempotency key
    complete: Booked → `labelled`, documents and numbers stored,
              shipment_label_booked
              Refused → `label_failed`, shipment_label_failed
              Unavailable / OutcomeUnknown → deferred, stays `booking`
```

If the ship-from in force at `shipment_create` is not in the chosen
service's origin country (`ship_from_elsewhere`), or the service's carrier
has no account (`no_carrier_account`), the shipment is created
`label_failed` with that reason and no request, and a person takes it
from there as below. An answer that is not what the carrier documents,
on a booking, a lookup or a cancel, also goes to a person
(`needs_person`, with the answer in `detail`).

After a `sent` with no result, the account's `book_guard` decides, as in
the payment attempt: send again with the same key; send again and move
to `deduplicating`; or look up by reference only, found → `labelled`,
not found → `needs_person` and `shipment_needs_person`. In
`deduplicating`, `find_by_reference` lists every label for the shipment;
the one we recorded stays, each other gets a `cancel` request, then back
to `labelled`.

A person on `/staff/shipments` can book again a `label_failed` or
`needs_person` shipment (request number + 1, `shipment_booking_retried`,
the person as actor), unless its ship-from is still outside the service's
origin (it never changes: that one is booked with the carrier by hand) or
its carrier still has no account. Correcting the merchant's data waits
for the merchant surfaces; until then the fix is made with the carrier
and the merchant by hand, then booked again. On a `none` guard after a
lost answer the person checks with the carrier first: it may hold a label
already.

The merchant gets the label from `GET /v1/orders/{id}/label` (PDF, 404
until labelled), and the order in the API has a `shipment` object (id,
booking state, tracking status, carrier, service, tracking number,
`label_url` once labelled).

## 7. Tracking

```
webhook (fat or thin) or poll page ─▶ carrier_inbox (raw, per account)
    + carrier_inbox_stored
carrier_inbox_decode (local, per account): one carrier_event_received
    per event; thin webhook → shipment_events_wanted
shipment_event_apply (local, shipment lock): the event once
    (UNIQUE (shipment_id, carrier_event_id)), status = derive_status(all
    events), each newly reached milestone inserted once
    (UNIQUE (shipment_id, milestone)) with its fact
shipment_label_booked ─▶ shipment_tracking_start: on a `poll` account,
    the first shipment_poll_due
shipment_poll_due ┐
shipment_events_wanted ┴▶ carrier_poll (effect): fetch one page into the
    inbox; on a scheduled poll, schedule the next unless polling is over
```

`carrier_inbox` has `psp_inbox`'s shape: the body hash only says "these
bytes are stored", the guard against a duplicate event is the carrier's
event id. An event for a tracking number the account never booked is a
vendor's doing, so it is a `carrier_event_unmatched` row on the staff
page, not a raise.

`derive_status(events: frozenset[CarrierEvent]) -> TrackingStatus`, pure:
the highest-ranked status among the events wins, except that a
`delivery_withdrawn` event voids every `delivered` event that occurred
before it. Milestones: `picked_up`, `held_at_customs`, `delivered`,
`delivery_withdrawn`, `returning`, `returned`, `lost`, one fact type each
(`shipment_delivered`, …), so a consumer subscribes to the one it needs
(slice 9 takes `shipment_delivered`). A milestone is never removed: a
withdrawal is a milestone of its own.

Polling ends at `returned` or `lost`, and for a delivered shipment
when the order's return window closes: a delivery withdrawn after the
merchant's money is released costs us. The window's length per buyer
country is legal input not yet given (`plan-merchant-surfaces.md` §9,
question 2), so it comes as `return_window_days` per country in market
data, `placeholder` until answered (prod refuses a placeholder, as for
fees). Reaching `delivered` stores `return_window_ends_at` on the
shipment; a withdrawal and a later delivery move it. The poll compares
it under the shipment lock; slices 8 and 9 read the same value. A
return is a shipment of its own (slice 8), followed by its own polls. Time only starts a
poll; what the poll finds is decided under the shipment lock.

## 8. Facts and consumers

Facts: `shipment_created`, `shipment_label_booked`,
`shipment_label_failed`, `shipment_needs_person`,
`shipment_booking_retried`, `carrier_inbox_stored`,
`carrier_event_received`, `shipment_events_wanted`, `shipment_poll_due`
and the seven milestone facts. Consumers: `shipment_create`,
`carrier_book`, `carrier_inbox_decode`, `shipment_event_apply`,
`shipment_tracking_start`, `carrier_poll`. Ids are typed
(`ShipmentId`, `CarrierRequestId`, `CarrierInboxId`), so the echo's
`typed-ids` ignores go with it.

## 9. Testing and fuzzing

- Properties: `derive_status` gives the same status for every order and
  duplication of a set of events; the decoders give a typed record or a
  typed error for any bytes.
- Booking tests (`tests/shipments/test_booking.py`): the customs lines
  carry every sold line at its value, a lost answer under each guard, a
  refusal, a ship-from moved after the quote, a carrier without an
  account, book again, the label through the API.
- A flow machine, `tests/flow/test_shipment_flow_fuzz.py`, starting deep:
  one to three paid orders, nothing shipped. Built for booking: a drawn
  guard, buyers whose address the carrier refuses, faults planned per
  run, crashes around a booking or inside a local step, lost answers, a
  person booking again. After every action: a `keyed` or `plain` carrier
  never holds two labels for a shipment, a `cancellable` one only while
  it books or sorts them out; a labelled shipment's label is live with
  both documents stored; a failed or stuck one has none live. After
  draining, and a person booking every stuck one again: each shipment
  labelled with exactly one live label, or its address refused. Reach
  milestones: labelled under each guard, an extra label cancelled,
  `needs_person`, `label_failed`, booked again. Its soak target is
  `shipment-flow`.
- 6b adds to the machine: release the next event, send it twice, release
  out of order, a bad signature, clock jumps for polls; after draining,
  each milestone at most once and each status equal to `derive_status`
  of the events the fake released; reach milestones for each tracking
  mode, `delivered` and `delivery_withdrawn`.
- The browser smoke suite: the order page shows the tracking status.

## 10. Compliance touchpoints (named, not answered)

- Whose names go in the shipper, exporter and seller fields, and whether
  our entity may hold the account for exports from VN
  (`plan-logistics.md` §6, adviser and carrier).
- The merchant's HS code is a hint; classification is ours (§4), and
  ours is the declared code.
- The buyer's address goes to the carrier: the privacy notice names
  carriers as recipients (consumer law adviser).
- The export declaration number is kept on every shipment for the duty
  free re-import of a return (`plan-logistics.md` §5).

## 11. Rules this applies

Facts only, consumers that take facts late, twice and out of order; a row
lock around every read-then-write; the vendor's own id as the inbound
guard; the duplicate guard after a call with no answer, never a clock;
capabilities as account data and the registry as the only place that
matches on a provider; fakes over HTTP in every mode; typed ids;
expected failures returned, a vendor's mistake never a raise.

## 12. Scope

- **Pickup** is not an operation of its own in this slice: the booking
  asks for a pickup at the ship-from address, and the fake accepts it. A
  separate `book_pickup` comes if the chosen carrier needs one (checked
  in its current API docs when slice 10 starts).
- **Apparel only.** Checkout takes the `apparel` category alone; a line
  of any other category is refused at session creation with 422
  `category_not_offered`. The product gate (`plan-logistics.md` §2, §7:
  `product_category` against the destination, `food_contact`,
  `wood_species`) and the other categories wait until the whole flow is
  built and we expand. The other categories stay in
  `docs/markets/categories.csv` and their research stays.


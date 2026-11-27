"""What the shipment fuzzer checks about tracking (docs/plan-shipments.md
§7, §9), against what the fake carrier released.

After every action: an event reaches a shipment only if it is an event of
that shipment's kept label. After draining: every event the fake released
for a kept label reached its shipment; under pushes, every event of a label
no shipment kept (an extra label, cancelled) is kept unmatched; under
polling, nothing is unmatched, as only our own labels are fetched.
"""

from dataclasses import dataclass

from psycopg.rows import class_row

from mor.carriers.records import CarrierTrackingMode
from mor.db.connect import Connection
from mor.db.fetch import one_value


@dataclass(frozen=True, kw_only=True)
class _Misrouted:
    shipment_id: str
    shipment_tracking_number: str | None
    carrier_event_id: str
    event_tracking_number: str | None


async def check_events_routed(conn: Connection) -> None:
    """Every `carrier_event_received` names an event the fake released for
    the label its shipment kept: never an extra label's, never another's."""
    async with conn.cursor(row_factory=class_row(_Misrouted)) as cur:
        await cur.execute(
            """
            SELECT o.payload->>'shipment_id' AS shipment_id,
                   s.tracking_number AS shipment_tracking_number,
                   o.payload->>'carrier_event_id' AS carrier_event_id,
                   e.tracking_number AS event_tracking_number
            FROM outbox o
            JOIN shipment s ON s.id = (o.payload->>'shipment_id')::uuid
            LEFT JOIN fake_vendor.carrier_track_event e
              ON e.id = o.payload->>'carrier_event_id'
            WHERE o.message_type = 'carrier_event_received'
              AND e.tracking_number IS DISTINCT FROM s.tracking_number
            ORDER BY o.seq
            """
        )
        misrouted = await cur.fetchall()
    assert misrouted == [], f"events on the wrong shipment: {misrouted}"


@dataclass(frozen=True)
class _Missing:
    carrier_event_id: str
    tracking_number: str


async def check_tracking_drained(conn: Connection, mode: CarrierTrackingMode) -> None:
    async with conn.cursor(row_factory=class_row(_Missing)) as cur:
        await cur.execute(
            """
            SELECT e.id AS carrier_event_id, e.tracking_number
            FROM fake_vendor.carrier_track_event e
            JOIN shipment s ON s.tracking_number = e.tracking_number
            WHERE NOT EXISTS (
              SELECT 1 FROM outbox o
              WHERE o.message_type = 'carrier_event_received'
                AND o.payload->>'shipment_id' = s.id::text
                AND o.payload->>'carrier_event_id' = e.id)
            ORDER BY e.seq
            """
        )
        unreached = await cur.fetchall()
    assert unreached == [], f"kept labels' events never reached: {unreached}"
    match mode:
        case CarrierTrackingMode.PUSH_FAT | CarrierTrackingMode.PUSH_THIN:
            async with conn.cursor(row_factory=class_row(_Missing)) as cur:
                await cur.execute(
                    """
                    SELECT e.id AS carrier_event_id, e.tracking_number
                    FROM fake_vendor.carrier_track_event e
                    WHERE NOT EXISTS (
                      SELECT 1 FROM shipment s
                      WHERE s.tracking_number = e.tracking_number)
                    AND NOT EXISTS (
                      SELECT 1 FROM carrier_event_unmatched u
                      WHERE u.carrier_event_id = e.id
                        AND u.tracking_number = e.tracking_number)
                    ORDER BY e.seq
                    """
                )
                lost = await cur.fetchall()
            assert lost == [], f"other labels' events neither kept nor sent on: {lost}"
        case CarrierTrackingMode.POLL:
            unmatched = await one_value(
                conn, "SELECT count(*)::int FROM carrier_event_unmatched", None, int
            )
            assert unmatched == 0, "a poll fetches only our own labels"


async def tracking_reached(conn: Connection, mode: CarrierTrackingMode) -> set[str]:
    """The tracking milestones a run reached before its final drain."""
    reached: set[str] = set()
    tracked = await one_value(
        conn,
        "SELECT count(*)::int FROM outbox WHERE message_type IN"
        " ('carrier_event_received', 'shipment_events_wanted')",
        None,
        int,
    )
    if tracked:
        reached.add(f"tracked by {mode.value}")
    matched = await one_value(
        conn,
        "SELECT count(*)::int FROM outbox WHERE actor_id = 'carrier_event_match'",
        None,
        int,
    )
    if matched:
        reached.add("an event before its label was ours, matched")
    # Both sides ran the match after both had committed: the same event (or
    # a thin notice's fetch) sent on twice.
    twice = await one_value(
        conn,
        """
        SELECT count(*)::int FROM (
          SELECT 1 FROM outbox WHERE actor_id = 'carrier_event_match'
          GROUP BY message_type, payload->>'shipment_id',
                   payload->>'carrier_event_id'
          HAVING count(*) > 1) twice
        """,
        None,
        int,
    )
    if twice:
        reached.add("matched by both sides")
    never_ours = await one_value(
        conn,
        """
        SELECT count(*)::int FROM carrier_event_unmatched u
        WHERE NOT EXISTS (
          SELECT 1 FROM shipment s WHERE s.tracking_number = u.tracking_number)
        """,
        None,
        int,
    )
    if never_ours:
        reached.add("an extra label's event kept unmatched")
    return reached

"""The order a capture makes (docs/plan-payments.md §5).

    payment_captured ─▶ order_create (local, the session's lock): the order,
        the capture's journal entry, the session `completed`, and
        checkout_session_completed + order_paid

"One order per session" and "one per capture" are unique keys: a
redelivered fact finds the order made and changes nothing.
"""

import secrets
import uuid
from datetime import datetime

from mor.audit import Actor, ActorKind
from mor.checkout.quotes import read_made_quote
from mor.checkout.records import LockedSession, SessionState
from mor.checkout.sessions import read_address, set_session_state
from mor.db.connect import Connection
from mor.db.fetch import one_value
from mor.ids import CarrierCode, MarketLoadId, OrderId, ServiceCode
from mor.ledger.store import record_entry
from mor.messaging.facts import (
    CheckoutSessionCompletedV1,
    OrderPaidV1,
    PaymentCapturedV1,
)
from mor.messaging.outcome import Applied, ConsumerOutcome, Stale
from mor.messaging.store import append_fact
from mor.orders.posting import capture_entry
from mor.orders.records import OrderState
from mor.payments.attempts import read_attempt
from mor.payments.transitions import AttemptState


ACTOR = Actor(ActorKind.SYSTEM, "order_create")


async def _carrier(
    conn: Connection, load_id: MarketLoadId, service: ServiceCode
) -> CarrierCode:
    carrier = await one_value(
        conn,
        "SELECT carrier FROM shipping_service WHERE load_id = %s AND code = %s",
        (load_id, service),
        str,
    )
    return CarrierCode(carrier)


async def create_order(
    conn: Connection,
    session: LockedSession,
    captured: PaymentCapturedV1,
    now: datetime,
) -> ConsumerOutcome:
    """Consumer `order_create`, the session locked: one order per session
    (its unique key), for the captured attempt."""
    attempt_id = captured.payment_attempt_id
    attempt = await read_attempt(conn, attempt_id)
    assert attempt is not None and attempt.state == AttemptState.CAPTURED
    quote = await read_made_quote(conn, attempt.quote_id)
    order_id = OrderId(uuid.uuid7())
    cur = await conn.execute(
        """
        INSERT INTO order_
          (id, session_id, attempt_id, merchant_id, quote_id, option_no,
           address_id, state, paid_at, buyer_token, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (session_id) DO NOTHING
        RETURNING id
        """,
        (
            order_id,
            session.id,
            attempt.id,
            session.merchant_id,
            quote.id,
            attempt.option_no,
            quote.address_id,
            OrderState.PAID,
            now,
            secrets.token_urlsafe(32),
            now,
        ),
    )
    if await cur.fetchone() is None:
        return Stale()  # made already
    address = await read_address(conn, quote.address_id)
    assert address is not None
    option = quote.options[attempt.option_no]
    entry = capture_entry(
        attempt.id,
        quote,
        attempt.option_no,
        session.lines,
        session.merchant_id,
        attempt.psp_account_id,
        address.country,
        await _carrier(conn, quote.load_id, option.service),
        now,
        ACTOR,
        now,
    )
    await record_entry(conn, entry)
    await set_session_state(
        conn, session, SessionState.COMPLETED, session.address_id, session.quote_id
    )
    await append_fact(
        conn,
        CheckoutSessionCompletedV1(checkout_session_id=session.id, order_id=order_id),
        ACTOR,
        now,
    )
    await append_fact(
        conn,
        OrderPaidV1(
            order_id=order_id,
            checkout_session_id=session.id,
            merchant_id=session.merchant_id,
        ),
        ACTOR,
        now,
    )
    return Applied()

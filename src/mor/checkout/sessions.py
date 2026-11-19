"""Checkout sessions in the database. Persistence functions take the
connection first and never commit: a session and its
`checkout_session_created` commit together."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from mor.audit import Actor
from mor.checkout.records import (
    SESSION_LIFETIME,
    CheckoutSession,
    Customs,
    SessionDraft,
    SessionState,
    SoldLine,
)
from mor.couriers.records import Parcel
from mor.db.connect import Connection
from mor.messaging.facts import CheckoutSessionCreatedV1
from mor.messaging.store import append_fact
from mor.money.amount import Money
from mor.money.currency import Currency


async def create_session(
    conn: Connection,
    merchant_id: uuid.UUID,
    draft: SessionDraft,
    actor: Actor,
    now: datetime,
) -> CheckoutSession:
    session_id = uuid.uuid7()
    expires_at = now + SESSION_LIFETIME
    parcel = draft.parcel
    await conn.execute(
        """
        INSERT INTO checkout_session
          (id, merchant_id, state, merchant_reference, return_url, cancel_url,
           weight_g, length_mm, width_mm, height_mm, created_at, expires_at,
           actor_kind, actor_id)
        VALUES (%s, %s, 'open', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            session_id,
            merchant_id,
            draft.merchant_reference,
            draft.return_url,
            draft.cancel_url,
            parcel.weight_g,
            parcel.length_mm,
            parcel.width_mm,
            parcel.height_mm,
            now,
            expires_at,
            actor.kind.value,
            actor.id,
        ),
    )
    for line_no, line in enumerate(draft.lines):
        await conn.execute(
            """
            INSERT INTO checkout_session_line
              (session_id, line_no, name, quantity, unit_price_minor, currency,
               category, merchant_sku, customs_description, origin_country,
               weight_g, hs_code)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                session_id,
                line_no,
                line.name,
                line.quantity,
                line.unit_price.amount_minor,
                line.unit_price.currency.value,
                line.category,
                line.merchant_sku,
                line.customs.description,
                line.customs.origin_country,
                line.customs.weight_g,
                line.customs.hs_code,
            ),
        )
    await append_fact(
        conn,
        CheckoutSessionCreatedV1(
            checkout_session_id=session_id, merchant_id=merchant_id
        ),
        actor,
        now,
    )
    return CheckoutSession(
        session_id,
        merchant_id,
        SessionState.OPEN,
        draft.lines,
        draft.parcel,
        draft.merchant_reference,
        draft.return_url,
        draft.cancel_url,
        now,
        expires_at,
    )


@dataclass(frozen=True)
class _SessionRow:
    id: uuid.UUID
    merchant_id: uuid.UUID
    state: str
    merchant_reference: str | None
    return_url: str
    cancel_url: str
    weight_g: int
    length_mm: int
    width_mm: int
    height_mm: int
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class _LineRow:
    name: str
    quantity: int
    unit_price_minor: int
    currency: str
    category: str
    merchant_sku: str | None
    customs_description: str
    origin_country: str
    weight_g: int
    hs_code: str | None


async def read_session(
    conn: Connection, merchant_id: uuid.UUID, session_id: uuid.UUID
) -> CheckoutSession | None:
    """The merchant's own session; another merchant's reads as none."""
    async with conn.cursor(row_factory=class_row(_SessionRow)) as cur:
        await cur.execute(
            """
            SELECT id, merchant_id, state::text, merchant_reference, return_url,
                   cancel_url, weight_g, length_mm, width_mm, height_mm,
                   created_at, expires_at
            FROM checkout_session WHERE id = %s AND merchant_id = %s
            """,
            (session_id, merchant_id),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    async with conn.cursor(row_factory=class_row(_LineRow)) as cur:
        await cur.execute(
            """
            SELECT name, quantity, unit_price_minor, currency::text, category,
                   merchant_sku, customs_description, origin_country, weight_g,
                   hs_code
            FROM checkout_session_line WHERE session_id = %s ORDER BY line_no
            """,
            (session_id,),
        )
        line_rows = await cur.fetchall()
    lines: list[SoldLine] = []
    for line in line_rows:
        lines.append(
            SoldLine(
                line.name,
                line.quantity,
                Money(line.unit_price_minor, Currency(line.currency)),
                line.category,
                line.merchant_sku,
                Customs(
                    line.customs_description,
                    line.origin_country,
                    line.weight_g,
                    line.hs_code,
                ),
            )
        )
    return CheckoutSession(
        row.id,
        row.merchant_id,
        SessionState(row.state),
        tuple(lines),
        Parcel(row.weight_g, row.length_mm, row.width_mm, row.height_mm),
        row.merchant_reference,
        row.return_url,
        row.cancel_url,
        row.created_at,
        row.expires_at,
    )

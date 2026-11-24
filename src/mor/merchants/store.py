"""Merchants and API keys in the database. Persistence functions take the
connection first and never commit."""

import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from mor.audit import Actor
from mor.config import Environment
from mor.db.connect import Connection
from mor.db.fetch import one_value
from mor.merchants.records import (
    Authenticated,
    KeyEnv,
    KeyState,
    KybState,
    Merchant,
    NewKey,
)


KEY_PREFIX = "sk"
KEY_ID_BYTES = 8
KEY_SECRET_BYTES = 32


def key_env(environment: Environment) -> KeyEnv:
    """The keys an environment issues and accepts."""
    match environment:
        case Environment.DEV:
            return KeyEnv.DEV
        case Environment.TEST:
            return KeyEnv.TEST
        case Environment.PROD:
            return KeyEnv.LIVE


def _base32(raw: bytes) -> str:
    return base64.b32encode(raw).decode().rstrip("=").lower()


def _sha256(text: str) -> bytes:
    return hashlib.sha256(text.encode()).digest()


async def create_merchant(
    conn: Connection,
    legal_name: str,
    display_name: str,
    country: str,
    daily_vendor_asks: int,
    actor: Actor,
    now: datetime,
) -> Merchant:
    merchant_id = uuid.uuid7()
    await conn.execute(
        """
        INSERT INTO merchant
          (id, legal_name, display_name, country, daily_vendor_asks, created_at,
           actor_kind, actor_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            merchant_id,
            legal_name,
            display_name,
            country,
            daily_vendor_asks,
            now,
            actor.kind.value,
            actor.id,
        ),
    )
    return Merchant(
        id=merchant_id,
        legal_name=legal_name,
        display_name=display_name,
        country=country,
        daily_vendor_asks=daily_vendor_asks,
        created_at=now,
        kyb_state=KybState.PENDING,
        support_email=None,
    )


async def read_merchant(conn: Connection, merchant_id: uuid.UUID) -> Merchant | None:
    async with conn.cursor(row_factory=class_row(Merchant)) as cur:
        await cur.execute(
            """
            SELECT id, legal_name, display_name, country, daily_vendor_asks,
                   created_at, kyb_state, support_email
            FROM merchant WHERE id = %s
            """,
            (merchant_id,),
        )
        return await cur.fetchone()


async def merchants(conn: Connection) -> list[Merchant]:
    async with conn.cursor(row_factory=class_row(Merchant)) as cur:
        await cur.execute(
            """
            SELECT id, legal_name, display_name, country, daily_vendor_asks,
                   created_at, kyb_state, support_email
            FROM merchant ORDER BY id
            """
        )
        return await cur.fetchall()


async def set_kyb_state(
    conn: Connection,
    merchant_id: uuid.UUID,
    state: KybState,
    actor: Actor,
    now: datetime,
) -> bool:
    """Under the merchant's row lock, so a change and a pay step (which
    reads the state `FOR SHARE`) cannot cross. Records who changed it.
    False if there is no such merchant. Never commits."""
    cur = await conn.execute(
        "SELECT 1 FROM merchant WHERE id = %s FOR UPDATE", (merchant_id,)
    )
    if await cur.fetchone() is None:
        return False
    await conn.execute(
        "UPDATE merchant SET kyb_state = %s WHERE id = %s",
        (state.value, merchant_id),
    )
    await conn.execute(
        """
        INSERT INTO merchant_kyb_change
          (id, merchant_id, state, changed_at, actor_kind, actor_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (uuid.uuid7(), merchant_id, state.value, now, actor.kind.value, actor.id),
    )
    return True


async def kyb_state_for_pay(conn: Connection, merchant_id: uuid.UUID) -> KybState:
    """The state a pay step decides on, held `FOR SHARE` until it commits:
    a KYB change waits for it, and it waits for a KYB change."""
    return await one_value(
        conn,
        "SELECT kyb_state FROM merchant WHERE id = %s FOR SHARE",
        (merchant_id,),
        KybState,
    )


async def set_support_email(
    conn: Connection,
    merchant_id: uuid.UUID,
    support_email: str | None,
    actor: Actor,
    now: datetime,
) -> None:
    """The merchant's own setting. Never commits."""
    await conn.execute(
        """
        UPDATE merchant
        SET support_email = %s, settings_changed_at = %s,
            settings_actor_kind = %s, settings_actor_id = %s
        WHERE id = %s
        """,
        (support_email, now, actor.kind.value, actor.id, merchant_id),
    )


async def create_api_key(
    conn: Connection, merchant_id: uuid.UUID, env: KeyEnv, now: datetime
) -> NewKey:
    """A new key for the merchant. Its text is returned once; only the
    SHA-256 of its secret is stored."""
    key_id = _base32(secrets.token_bytes(KEY_ID_BYTES))
    secret = _base32(secrets.token_bytes(KEY_SECRET_BYTES))
    await conn.execute(
        """
        INSERT INTO api_key (id, merchant_id, env, secret_sha256, state, created_at)
        VALUES (%s, %s, %s, %s, 'active', %s)
        """,
        (key_id, merchant_id, env.value, _sha256(secret), now),
    )
    return NewKey(key_id, f"{KEY_PREFIX}_{env.value}_{key_id}_{secret}")


async def revoke_api_key(conn: Connection, key_id: str, now: datetime) -> bool:
    """False if there is no such active key."""
    cur = await conn.execute(
        """
        UPDATE api_key SET state = 'revoked', revoked_at = %s
        WHERE id = %s AND state = 'active'
        RETURNING id
        """,
        (now, key_id),
    )
    return await cur.fetchone() is not None


async def merchant_has_key(conn: Connection, merchant_id: uuid.UUID) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM api_key WHERE merchant_id = %s AND state = 'active'",
        (merchant_id,),
    )
    return await cur.fetchone() is not None


@dataclass(frozen=True, kw_only=True)
class _KeyRow:
    merchant_id: uuid.UUID
    env: KeyEnv
    secret_sha256: bytes
    state: KeyState


async def authenticate(
    conn: Connection, authorization: str | None, env: KeyEnv
) -> Authenticated | None:
    """The merchant behind `Authorization: Bearer sk_…`, if the key is ours,
    active and for this environment. None otherwise, with no reason: the
    caller is told only that it failed."""
    if authorization is None or not authorization.startswith("Bearer "):
        return None
    parts = authorization.removeprefix("Bearer ").strip().split("_")
    if len(parts) != 4 or parts[0] != KEY_PREFIX or parts[1] != env.value:
        return None
    key_id = parts[2]
    secret = parts[3]
    async with conn.cursor(row_factory=class_row(_KeyRow)) as cur:
        await cur.execute(
            """
            SELECT merchant_id, env, secret_sha256, state
            FROM api_key WHERE id = %s
            """,
            (key_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    matches = hmac.compare_digest(_sha256(secret), row.secret_sha256)
    if not matches or row.env != env or row.state != KeyState.ACTIVE:
        return None
    merchant = await read_merchant(conn, row.merchant_id)
    assert merchant is not None, "a key's merchant exists: a foreign key says so"
    return Authenticated(key_id, merchant)

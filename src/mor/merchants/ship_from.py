"""Where a merchant's parcels leave from (docs/plan-shipments.md §4): one
row per change, append-only, the newest in force. A merchant without one
cannot open a checkout session. Never commits."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from mor.audit import Actor
from mor.checkout.addresses import Address
from mor.db.connect import Connection
from mor.ids import CountryCode, MerchantId, MerchantShipFromId
from mor.markets.records import MarketSet


@dataclass(frozen=True, kw_only=True)
class ShipFrom:
    id: MerchantShipFromId
    merchant_id: MerchantId
    name: str
    email: str
    phone_dial_code: str
    phone_number: str
    line1: str
    line2: str | None
    city: str
    region: str | None
    postcode: str | None
    country: CountryCode
    created_at: datetime


def ship_from_countries(market: MarketSet) -> frozenset[CountryCode]:
    """Where a parcel may leave from: the origin of a service we offer.
    A ship-from elsewhere would have no service to book."""
    origins: set[CountryCode] = set()
    for service in market.services:
        origins.add(service.origin)
    return frozenset(origins)


async def set_ship_from(
    conn: Connection,
    merchant_id: MerchantId,
    address: Address,
    actor: Actor,
    now: datetime,
) -> MerchantShipFromId:
    """A new address in force from now; earlier ones stay as they were."""
    ship_from_id = MerchantShipFromId(uuid.uuid7())
    await conn.execute(
        """
        INSERT INTO merchant_ship_from
          (id, merchant_id, name, email, phone_dial_code, phone_number,
           line1, line2, city, region, postcode, country, created_at,
           actor_kind, actor_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            ship_from_id,
            merchant_id,
            address.name,
            address.email,
            address.phone_dial_code,
            address.phone_number,
            address.line1,
            address.line2,
            address.city,
            address.region,
            address.postcode,
            address.country,
            now,
            actor.kind.value,
            actor.id,
        ),
    )
    return ship_from_id


async def ship_from_in_force(
    conn: Connection, merchant_id: MerchantId
) -> ShipFrom | None:
    async with conn.cursor(row_factory=class_row(ShipFrom)) as cur:
        await cur.execute(
            """
            SELECT id, merchant_id, name, email, phone_dial_code, phone_number,
                   line1, line2, city, region, postcode, country, created_at
            FROM merchant_ship_from
            WHERE merchant_id = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (merchant_id,),
        )
        return await cur.fetchone()

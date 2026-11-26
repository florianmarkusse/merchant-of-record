"""Our HS classification of a merchant's SKUs (docs/plan-shipments.md §4):
classification is the importer of record's responsibility, ours as
merchant of record, so a line is sold only under a code we set. One row
per change, append-only, the newest per SKU in force. Never commits."""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from mor.audit import Actor
from mor.db.connect import Connection
from mor.ids import HsCode, MerchantId, MerchantSku, SkuClassificationId


# At least the six digits every country shares; the database checks it too.
_OUR_CODE = re.compile(r"^[0-9]{6,10}$")


@dataclass(frozen=True, kw_only=True)
class SkuClassification:
    id: SkuClassificationId
    merchant_id: MerchantId
    merchant_sku: MerchantSku
    hs_code: HsCode
    created_at: datetime
    actor_id: str  # who set it


def parse_our_code(text: str) -> HsCode | None:
    """A code as staff type it (`6109.10`), digits only, or None."""
    digits = text.replace(".", "").replace(" ", "")
    if _OUR_CODE.match(digits) is None:
        return None
    return HsCode(digits)


async def classify(
    conn: Connection,
    merchant_id: MerchantId,
    merchant_sku: MerchantSku,
    hs_code: HsCode,
    actor: Actor,
    now: datetime,
) -> SkuClassificationId:
    """The SKU's code from now; lines already sold keep theirs."""
    classification_id = SkuClassificationId(uuid.uuid7())
    await conn.execute(
        """
        INSERT INTO sku_classification
          (id, merchant_id, merchant_sku, hs_code, created_at, actor_kind,
           actor_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            classification_id,
            merchant_id,
            merchant_sku,
            hs_code,
            now,
            actor.kind.value,
            actor.id,
        ),
    )
    return classification_id


async def classifications_in_force(
    conn: Connection, merchant_id: MerchantId
) -> dict[MerchantSku, SkuClassification]:
    """The code in force for each SKU of the merchant we classified."""
    async with conn.cursor(row_factory=class_row(SkuClassification)) as cur:
        await cur.execute(
            """
            SELECT DISTINCT ON (merchant_sku)
                   id, merchant_id, merchant_sku, hs_code, created_at, actor_id
            FROM sku_classification
            WHERE merchant_id = %s
            ORDER BY merchant_sku, id DESC
            """,
            (merchant_id,),
        )
        rows = await cur.fetchall()
    in_force: dict[MerchantSku, SkuClassification] = {}
    for row in rows:
        in_force[row.merchant_sku] = row
    return in_force


async def classifications_of(
    conn: Connection, merchant_id: MerchantId, skus: list[MerchantSku]
) -> dict[MerchantSku, SkuClassification]:
    """The code in force for each of these SKUs we classified; a SKU we
    did not is missing from the answer."""
    async with conn.cursor(row_factory=class_row(SkuClassification)) as cur:
        await cur.execute(
            """
            SELECT DISTINCT ON (merchant_sku)
                   id, merchant_id, merchant_sku, hs_code, created_at, actor_id
            FROM sku_classification
            WHERE merchant_id = %s AND merchant_sku = ANY(%s)
            ORDER BY merchant_sku, id DESC
            """,
            (merchant_id, skus),
        )
        rows = await cur.fetchall()
    in_force: dict[MerchantSku, SkuClassification] = {}
    for row in rows:
        in_force[row.merchant_sku] = row
    return in_force

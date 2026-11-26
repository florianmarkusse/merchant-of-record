"""Ids as the API, the buyer's links and the command line show them: a
prefix and the uuid7 in hex (`cs_…`, `mer_…`). The database keeps the uuid.
uuid7 has 74 random bits, enough for a session link that is its own bearer
token. One pair of functions per id type, so a prefix cannot be put on
another kind of id."""

import uuid

from mor.ids import CheckoutSessionId, MerchantId, OrderId, QuoteId, ShipmentId


_SESSION_PREFIX = "cs_"
_MERCHANT_PREFIX = "mer_"
_QUOTE_PREFIX = "qt_"
_ORDER_PREFIX = "ord_"
_SHIPMENT_PREFIX = "shp_"


def session_public_id(session_id: CheckoutSessionId) -> str:
    return f"{_SESSION_PREFIX}{session_id.hex}"


def merchant_public_id(merchant_id: MerchantId) -> str:
    return f"{_MERCHANT_PREFIX}{merchant_id.hex}"


def quote_public_id(quote_id: QuoteId) -> str:
    return f"{_QUOTE_PREFIX}{quote_id.hex}"


def order_public_id(order_id: OrderId) -> str:
    return f"{_ORDER_PREFIX}{order_id.hex}"


def shipment_public_id(shipment_id: ShipmentId) -> str:
    return f"{_SHIPMENT_PREFIX}{shipment_id.hex}"


def parse_session_id(text: str) -> CheckoutSessionId | None:
    found = _parse(_SESSION_PREFIX, text)
    if found is None:
        return None
    return CheckoutSessionId(found)


def parse_merchant_id(text: str) -> MerchantId | None:
    found = _parse(_MERCHANT_PREFIX, text)
    if found is None:
        return None
    return MerchantId(found)


def parse_order_id(text: str) -> OrderId | None:
    found = _parse(_ORDER_PREFIX, text)
    if found is None:
        return None
    return OrderId(found)


def parse_shipment_id(text: str) -> ShipmentId | None:
    found = _parse(_SHIPMENT_PREFIX, text)
    if found is None:
        return None
    return ShipmentId(found)


def _parse(prefix: str, text: str) -> uuid.UUID | None:
    if not text.startswith(prefix):
        return None
    hex_part = text.removeprefix(prefix)
    if len(hex_part) != 32:
        return None
    try:
        return uuid.UUID(hex=hex_part)
    except ValueError:
        return None

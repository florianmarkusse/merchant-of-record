"""Ids as the API and the command line show them: a prefix and the
uuid7 in hex (`cs_…`, `mer_…`). The database keeps the uuid. uuid7 has 74
random bits, enough for a session link that is its own bearer token."""

import uuid


SESSION_PREFIX = "cs_"
MERCHANT_PREFIX = "mer_"
QUOTE_PREFIX = "qt_"
ORDER_PREFIX = "ord_"


def public_id(prefix: str, value: uuid.UUID) -> str:
    return f"{prefix}{value.hex}"


def parse_id(prefix: str, text: str) -> uuid.UUID | None:
    if not text.startswith(prefix):
        return None
    hex_part = text.removeprefix(prefix)
    if len(hex_part) != 32:
        return None
    try:
        return uuid.UUID(hex=hex_part)
    except ValueError:
        return None

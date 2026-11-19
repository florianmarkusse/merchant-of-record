"""Merchants and API keys from the command line, until there is a
dashboard (docs/plan-quote.md §5, 4d-1).

    uv run python -m mor.merchants create "Legal Name Co., Ltd." "Shop Name"
    uv run python -m mor.merchants list
    uv run python -m mor.merchants key mer_…        # prints the key, once
    uv run python -m mor.merchants revoke <key id>

Keys are for the environment the command runs in (`ENVIRONMENT`), and
work only there.
"""

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime

from mor.audit import Actor, ActorKind
from mor.config import load_settings
from mor.db.connect import connect
from mor.merchants.records import DEFAULT_DAILY_VENDOR_ASKS
from mor.merchants.store import (
    create_api_key,
    create_merchant,
    key_env,
    merchants,
    read_merchant,
    revoke_api_key,
)
from mor.public_ids import MERCHANT_PREFIX, parse_id, public_id


OPERATOR = Actor(ActorKind.STAFF, "cli")


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m mor.merchants")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create a merchant")
    create.add_argument("legal_name")
    create.add_argument("display_name")
    create.add_argument("--country", default="VN")
    create.add_argument(
        "--daily-vendor-asks", type=int, default=DEFAULT_DAILY_VENDOR_ASKS
    )
    commands.add_parser("list", help="list merchants")
    key = commands.add_parser("key", help="create an API key, shown once")
    key.add_argument("merchant")
    revoke = commands.add_parser("revoke", help="revoke an API key")
    revoke.add_argument("key_id")
    args = parser.parse_args(argv)
    settings = load_settings(os.environ)
    now = datetime.now(UTC)
    async with await connect(settings.database_url) as conn:
        match args.command:
            case "create":
                merchant = await create_merchant(
                    conn,
                    args.legal_name,
                    args.display_name,
                    args.country,
                    args.daily_vendor_asks,
                    OPERATOR,
                    now,
                )
                await conn.commit()
                print(public_id(MERCHANT_PREFIX, merchant.id))
            case "list":
                for merchant in await merchants(conn):
                    print(
                        public_id(MERCHANT_PREFIX, merchant.id),
                        merchant.display_name,
                        f"({merchant.legal_name})",
                    )
            case "key":
                merchant_id = parse_id(MERCHANT_PREFIX, args.merchant)
                if (
                    merchant_id is None
                    or await read_merchant(conn, merchant_id) is None
                ):
                    print(f"no merchant {args.merchant}", file=sys.stderr)
                    return 1
                new = await create_api_key(
                    conn, merchant_id, key_env(settings.environment), now
                )
                await conn.commit()
                print(new.text)
                print(f"key id {new.id}: shown once, store it now", file=sys.stderr)
            case "revoke":
                if not await revoke_api_key(conn, args.key_id, now):
                    print(f"no active key {args.key_id}", file=sys.stderr)
                    return 1
                await conn.commit()
                print(f"revoked {args.key_id}")
            case _:
                raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))

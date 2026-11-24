"""Merchants, API keys and KYB from the command line, until there is a
dashboard and staff sign-in (docs/plan-quote.md §5, 4d-1;
docs/plan-payments.md §6).

    uv run python -m mor.merchants create "Legal Name Co., Ltd." "Shop Name"
    uv run python -m mor.merchants list
    uv run python -m mor.merchants key mer_…        # prints the key, once
    uv run python -m mor.merchants revoke <key id>
    uv run python -m mor.merchants kyb mer_… approved   # pending, suspended

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
from mor.merchants.records import DEFAULT_DAILY_VENDOR_ASKS, KybState
from mor.merchants.store import (
    create_api_key,
    create_merchant,
    key_env,
    merchants,
    read_merchant,
    revoke_api_key,
    set_kyb_state,
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
    kyb = commands.add_parser("kyb", help="set a merchant's KYB state")
    kyb.add_argument("merchant")
    kyb.add_argument("state", choices=[state.value for state in KybState])
    args = parser.parse_args(argv)
    settings = load_settings(os.environ)
    now = datetime.now(UTC)
    async with await connect(settings.database_url) as conn:
        match args.command:
            case "create":
                seller = await create_merchant(
                    conn,
                    args.legal_name,
                    args.display_name,
                    args.country,
                    args.daily_vendor_asks,
                    OPERATOR,
                    now,
                )
                await conn.commit()
                print(public_id(MERCHANT_PREFIX, seller.id))
            case "list":
                for seller in await merchants(conn):
                    print(
                        public_id(MERCHANT_PREFIX, seller.id),
                        seller.display_name,
                        f"({seller.legal_name})",
                        f"kyb {seller.kyb_state}",
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
            case "kyb":
                merchant_id = parse_id(MERCHANT_PREFIX, args.merchant)
                state = KybState(args.state)
                if merchant_id is None or not await set_kyb_state(
                    conn, merchant_id, state, OPERATOR, now
                ):
                    print(f"no merchant {args.merchant}", file=sys.stderr)
                    return 1
                await conn.commit()
                print(f"{args.merchant} kyb {state}")
            case _:
                raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))

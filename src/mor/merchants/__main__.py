"""Merchants, API keys and KYB from the command line, until there is a
dashboard and staff sign-in (docs/plan-quote.md §5, 4d-1;
docs/plan-payments.md §6).

    uv run python -m mor.merchants create "Legal Name Co., Ltd." "Shop Name"
    uv run python -m mor.merchants list
    uv run python -m mor.merchants key mer_…        # prints the key, once
    uv run python -m mor.merchants revoke <key id>
    uv run python -m mor.merchants kyb mer_… approved   # pending, suspended
    uv run python -m mor.merchants ship-from mer_… "Name" email phone \
        "street" "city" --region "Province" [--line2 …] [--postcode …]

Keys are for the environment the command runs in (`ENVIRONMENT`), and
work only there.
"""

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime

from mor.audit import Actor, ActorKind
from mor.checkout import addresses
from mor.checkout.addresses import Address, parse_address
from mor.config import load_settings
from mor.db.connect import connect
from mor.markets.store import market_in_force
from mor.merchants.records import DEFAULT_DAILY_VENDOR_ASKS, KybState
from mor.merchants.ship_from import set_ship_from, ship_from_countries
from mor.merchants.store import (
    create_api_key,
    create_merchant,
    key_env,
    merchants,
    read_merchant,
    revoke_api_key,
    set_kyb_state,
)
from mor.public_ids import merchant_public_id, parse_merchant_id


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
    ship_from = commands.add_parser(
        "ship-from", help="set where a merchant's parcels leave from"
    )
    ship_from.add_argument("merchant")
    ship_from.add_argument("name")
    ship_from.add_argument("email")
    ship_from.add_argument("phone")
    ship_from.add_argument("line1")
    ship_from.add_argument("city")
    ship_from.add_argument("--line2", default="")
    ship_from.add_argument("--region", default="")
    ship_from.add_argument("--postcode", default="")
    ship_from.add_argument("--country", default="VN")
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
                print(merchant_public_id(seller.id))
            case "list":
                for seller in await merchants(conn):
                    print(
                        merchant_public_id(seller.id),
                        seller.display_name,
                        f"({seller.legal_name})",
                        f"kyb {seller.kyb_state}",
                    )
            case "key":
                merchant_id = parse_merchant_id(args.merchant)
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
                merchant_id = parse_merchant_id(args.merchant)
                state = KybState(args.state)
                if merchant_id is None or not await set_kyb_state(
                    conn, merchant_id, state, OPERATOR, now
                ):
                    print(f"no merchant {args.merchant}", file=sys.stderr)
                    return 1
                await conn.commit()
                print(f"{args.merchant} kyb {state}")
            case "ship-from":
                merchant_id = parse_merchant_id(args.merchant)
                if (
                    merchant_id is None
                    or await read_merchant(conn, merchant_id) is None
                ):
                    print(f"no merchant {args.merchant}", file=sys.stderr)
                    return 1
                market = await market_in_force(conn)
                if market is None:
                    print("no market data loaded", file=sys.stderr)
                    return 1
                parsed = parse_address(
                    {
                        addresses.NAME: args.name,
                        addresses.EMAIL: args.email,
                        addresses.PHONE: args.phone,
                        addresses.LINE1: args.line1,
                        addresses.LINE2: args.line2,
                        addresses.CITY: args.city,
                        addresses.REGION: args.region,
                        addresses.POSTCODE: args.postcode,
                        addresses.COUNTRY: args.country,
                    },
                    ship_from_countries(market),
                )
                if not isinstance(parsed, Address):
                    for field, problem in sorted(parsed.items()):
                        print(f"{field}: {problem}", file=sys.stderr)
                    return 1
                await set_ship_from(conn, merchant_id, parsed, OPERATOR, now)
                await conn.commit()
                print(f"{args.merchant} ships from {parsed.city}, {parsed.country}")
            case _:
                raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))

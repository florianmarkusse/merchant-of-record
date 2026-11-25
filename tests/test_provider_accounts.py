"""A fake provider is always a fake account, so the registries' kind check
keeps every fake out of prod."""

import asyncio
from datetime import timedelta

import psycopg
import pytest

from mor.carriers.prices import create_carrier_account
from mor.carriers.records import CarrierProvider, PriceExpiry
from mor.db.connect import connect
from mor.duties.lookups import create_vendor_account
from mor.duties.port import DutyProvider
from mor.ids import CarrierCode
from mor.provider_accounts import ProviderAccountKind


def test_a_fake_carrier_cannot_be_a_live_account(database_url: str) -> None:
    async def body() -> None:
        async with await connect(database_url) as conn:
            await create_carrier_account(
                conn,
                CarrierCode("carrier"),
                CarrierProvider.FAKE,
                ProviderAccountKind.LIVE,
                "http://mor/_fake/carrier/acct",
                timedelta(seconds=5),
                timedelta(hours=24),
                PriceExpiry.CARRIER_FIRST,
            )

    with pytest.raises(psycopg.errors.CheckViolation):
        asyncio.run(body())


def test_a_fake_duty_vendor_cannot_be_a_sandbox_account(database_url: str) -> None:
    async def body() -> None:
        async with await connect(database_url) as conn:
            await create_vendor_account(
                conn,
                "acme",
                DutyProvider.FAKE,
                ProviderAccountKind.SANDBOX,
                "http://mor/_fake/duty/acct",
                timedelta(seconds=5),
            )

    with pytest.raises(psycopg.errors.CheckViolation):
        asyncio.run(body())

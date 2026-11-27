"""Webhook handlers: verify, store the raw body, answer. Nothing is decoded
here, so a decoder bug never loses an event (docs/plan-messaging.md §7).

Webhooks carry no cookies, so the cross-site rule for POSTs is met by the
signature: a forged request has no valid one.
"""

import uuid
from datetime import datetime

from starlette.requests import Request
from starlette.responses import Response

import mor.carriers.registry
import mor.payments.inbound
import mor.psp.registry
import mor.shipments.inbound
from mor.carriers.accounts import read_carrier_account
from mor.carriers.records import CarrierTrackingMode
from mor.db.connect import connect
from mor.echo import registry
from mor.echo.fake_client import SIGNATURE_HEADER
from mor.echo.inbound import inbound_account, store_inbox
from mor.echo.port import EventMode
from mor.ids import CarrierAccountId, PspAccountId
from mor.psp.accounts import read_psp_account
from mor.psp.port import InboundMode, StatusMode


async def echo_webhook(request: Request, database_url: str, now: datetime) -> Response:
    try:
        account_id = uuid.UUID(request.path_params["account_id"])
    except ValueError:
        return Response(status_code=404)
    body = await request.body()
    async with await connect(database_url) as conn:
        inbound = await inbound_account(conn, account_id)
        if inbound is None or inbound.event_mode not in (
            EventMode.PUSH,
            EventMode.PUSH_THIN,
        ):
            return Response(status_code=404)
        signature = request.headers.get(SIGNATURE_HEADER)
        if not registry.verify_webhook(
            inbound.account, inbound.webhook_secret, body, signature
        ):
            return Response(status_code=401)
        await store_inbox(conn, account_id, inbound.event_mode, body, now)
        await conn.commit()
    # Stored now or before: either way the vendor may stop sending it.
    return Response(status_code=200)


async def psp_webhook(request: Request, database_url: str, now: datetime) -> Response:
    """A PSP's webhook (docs/plan-payments.md §4): verify, store raw, 200."""
    try:
        account_id = PspAccountId(uuid.UUID(request.path_params["account_id"]))
    except ValueError:
        return Response(status_code=404)
    body = await request.body()
    async with await connect(database_url) as conn:
        account = await read_psp_account(conn, account_id)
        if account is None or account.status_mode == StatusMode.POLL:
            return Response(status_code=404)
        signature = request.headers.get(mor.psp.registry.signature_header(account))
        if not mor.psp.registry.verify_webhook(account, body, signature):
            return Response(status_code=401)
        await mor.payments.inbound.store_inbox(
            conn, account_id, InboundMode(account.status_mode.value), body, None, now
        )
        await conn.commit()
    # Stored now or before: either way the PSP may stop sending it.
    return Response(status_code=200)


async def carrier_webhook(
    request: Request, database_url: str, now: datetime
) -> Response:
    """A carrier's tracking webhook (docs/plan-shipments.md §7): verify,
    store raw, 200. An account that polls takes no webhooks."""
    try:
        account_id = CarrierAccountId(uuid.UUID(request.path_params["account_id"]))
    except ValueError:
        return Response(status_code=404)
    body = await request.body()
    async with await connect(database_url) as conn:
        account = await read_carrier_account(conn, account_id)
        if account is None or account.tracking_mode == CarrierTrackingMode.POLL:
            return Response(status_code=404)
        signature = request.headers.get(mor.carriers.registry.signature_header(account))
        if not mor.carriers.registry.verify_webhook(account, body, signature):
            return Response(status_code=401)
        await mor.shipments.inbound.store_inbox(
            conn, account_id, account.tracking_mode, body, now
        )
        await conn.commit()
    # Stored now or before: either way the carrier may stop sending it.
    return Response(status_code=200)

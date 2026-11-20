"""Webhook handlers: verify, store the raw body, answer. Nothing is decoded
here, so a decoder bug never loses an event (docs/plan-messaging.md §12).

Webhooks carry no cookies, so the cross-site rule for POSTs is met by the
signature: a forged request has no valid one.
"""

import uuid
from datetime import datetime

from starlette.requests import Request
from starlette.responses import Response

from mor.db.connect import connect
from mor.echo import registry
from mor.echo.fake_client import SIGNATURE_HEADER
from mor.echo.inbound import inbound_account, store_inbox
from mor.echo.port import EventMode
from mor.payments.inbound import store_inbox as store_psp_inbox
from mor.psp import registry as psp_registry
from mor.psp.accounts import read_psp_account
from mor.psp.port import StatusMode


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
        account_id = uuid.UUID(request.path_params["account_id"])
    except ValueError:
        return Response(status_code=404)
    body = await request.body()
    async with await connect(database_url) as conn:
        account = await read_psp_account(conn, account_id)
        if account is None or account.status_mode == StatusMode.POLL:
            return Response(status_code=404)
        signature = request.headers.get(psp_registry.signature_header(account))
        if not psp_registry.verify_webhook(account, body, signature):
            return Response(status_code=401)
        await store_psp_inbox(conn, account_id, account.status_mode, body, now)
        await conn.commit()
    # Stored now or before: either way the PSP may stop sending it.
    return Response(status_code=200)

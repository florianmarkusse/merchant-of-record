"""The merchant API's handlers (plan-merchant-surfaces §4;
docs/plan-quote.md §5, 4d-1).

Bearer keys, no cookies: a cross-site page cannot send one, so the
cross-site rule for POSTs is met by the key itself.

Order of work for a POST that changes state: authenticate, read the
Idempotency-Key, decode the body, then claim the key in the transaction
that does the work. A replay or a reused key answers there. A refusal
after the claim rolls back, so the key stays free for the corrected
request.
"""

from datetime import UTC, datetime, time, timedelta

from starlette.requests import Request
from starlette.responses import Response

from mor.audit import Actor, ActorKind
from mor.checkout.quotes import StoredQuote, read_made_quote, read_quote
from mor.checkout.sessions import create_session, read_address, read_session
from mor.config import Settings
from mor.db.connect import Connection, connect
from mor.invoices.store import invoice_for_order
from mor.markets.store import market_in_force
from mor.merchants.idempotency import (
    Claimed,
    KeyReused,
    Replay,
    claim_key,
    store_response,
)
from mor.merchants.records import Authenticated
from mor.merchants.store import (
    authenticate,
    key_env,
    read_merchant,
    set_support_email,
)
from mor.merchants.vendor_asks import take_vendor_asks
from mor.orders.store import order_by_session, read_order
from mor.public_ids import ORDER_PREFIX, SESSION_PREFIX, parse_id
from mor.quote.build import (
    InputsMissing,
    Quote,
    QuoteLine,
    QuoteRequest,
    build_quote,
)
from mor.quote.inputs import (
    NoAccount,
    ask_missing,
    held_lookups,
    held_prices,
    new_asks,
)
from mor.quote.refusals import Refusal, offered_in, refusal_of
from mor.web.api_estimates import estimate_out, refusal_error
from mor.web.api_wire import (
    ApiError,
    ErrorType,
    EstimateIn,
    SessionIn,
    SettingsIn,
    decode,
    error_response,
    json_response,
    merchant_out,
    order_out,
    parcel_of,
    request_sha256,
    session_draft,
    session_out,
    sold_lines,
)


ESTIMATE_RETRY_S = 2
MAX_IDEMPOTENCY_KEY = 255

AUTH_FAILED = ApiError(
    status=401,
    type=ErrorType.AUTHENTICATION,
    code="authentication_failed",
    message="send a valid API key as `Authorization: Bearer sk_…`",
)
NO_MARKET = ApiError(
    status=503,
    type=ErrorType.UNAVAILABLE,
    code="no_market_data",
    message="pricing is not available yet",
)


async def _authenticated(
    conn: Connection, request: Request, settings: Settings
) -> Authenticated | None:
    return await authenticate(
        conn, request.headers.get("authorization"), key_env(settings.environment)
    )


async def create_checkout_session(
    request: Request, settings: Settings, now: datetime
) -> Response:
    async with await connect(settings.database_url) as conn:
        auth = await _authenticated(conn, request, settings)
        if auth is None:
            return error_response(AUTH_FAILED)
        key = request.headers.get("idempotency-key", "")
        if not key or len(key) > MAX_IDEMPOTENCY_KEY:
            return error_response(
                ApiError(
                    status=400,
                    type=ErrorType.INVALID_REQUEST,
                    code="idempotency_key_missing",
                    message="send an Idempotency-Key header of 1 to 255 characters",
                )
            )
        decoded = decode(await request.body(), SessionIn)
        if isinstance(decoded, ApiError):
            return error_response(decoded)
        merchant_id = auth.merchant.id
        claim = await claim_key(
            conn, merchant_id, key, request_sha256("checkout_sessions", decoded), now
        )
        match claim:
            case Replay(status=status, response=stored):
                await conn.rollback()
                return json_response(status, stored)
            case KeyReused():
                await conn.rollback()
                return error_response(
                    ApiError(
                        status=409,
                        type=ErrorType.IDEMPOTENCY,
                        code="idempotency_key_reused",
                        message="this Idempotency-Key was used for a different request",
                    )
                )
            case Claimed():
                pass
        market = await market_in_force(conn)
        if market is None:
            await conn.rollback()
            return error_response(NO_MARKET)
        draft = session_draft(market, decoded)
        if isinstance(draft, ApiError):
            await conn.rollback()
            return error_response(draft)
        actor = Actor(ActorKind.MERCHANT_API_KEY, auth.key_id)
        session = await create_session(conn, merchant_id, draft, actor, now)
        body = session_out(session, settings.self_url, None, None, None)
        await store_response(conn, merchant_id, key, 201, body)
        await conn.commit()
    return json_response(201, body)


async def get_checkout_session(request: Request, settings: Settings) -> Response:
    not_found = ApiError(
        status=404,
        type=ErrorType.NOT_FOUND,
        code="not_found",
        message="no such checkout session",
        param="id",
    )
    async with await connect(settings.database_url) as conn:
        auth = await _authenticated(conn, request, settings)
        if auth is None:
            return error_response(AUTH_FAILED)
        session_id = parse_id(SESSION_PREFIX, request.path_params["session_id"])
        if session_id is None:
            return error_response(not_found)
        session = await read_session(conn, auth.merchant.id, session_id)
        if session is None:
            return error_response(not_found)
        country: str | None = None
        if session.address_id is not None:
            address = await read_address(conn, session.address_id)
            assert address is not None
            country = address.country
        quote: StoredQuote | None = None
        if session.quote_id is not None:
            quote = await read_quote(conn, session.quote_id)
        order = await order_by_session(conn, session.id)
    order_id = None if order is None else order.id
    return json_response(
        200, session_out(session, settings.self_url, country, quote, order_id)
    )


async def get_order(request: Request, settings: Settings) -> Response:
    """The merchant's own order, with the full address for fulfilment
    (docs/plan-payments.md §6.1); another merchant's is a 404."""
    not_found = ApiError(
        status=404,
        type=ErrorType.NOT_FOUND,
        code="not_found",
        message="no such order",
        param="id",
    )
    async with await connect(settings.database_url) as conn:
        auth = await _authenticated(conn, request, settings)
        if auth is None:
            return error_response(AUTH_FAILED)
        order_id = parse_id(ORDER_PREFIX, request.path_params["order_id"])
        if order_id is None:
            return error_response(not_found)
        order = await read_order(conn, auth.merchant.id, order_id)
        if order is None:
            return error_response(not_found)
        session = await read_session(conn, auth.merchant.id, order.session_id)
        assert session is not None
        quote = await read_made_quote(conn, order.quote_id)
        address = await read_address(conn, order.address_id)
        assert address is not None
        tax_invoice = await invoice_for_order(conn, order.id)
    number = None if tax_invoice is None else tax_invoice.number_text
    return json_response(
        200, order_out(order, session, quote, address, number, settings.self_url)
    )


async def get_merchant(request: Request, settings: Settings) -> Response:
    async with await connect(settings.database_url) as conn:
        auth = await _authenticated(conn, request, settings)
    if auth is None:
        return error_response(AUTH_FAILED)
    return json_response(200, merchant_out(auth.merchant))


async def post_merchant_settings(
    request: Request, settings: Settings, now: datetime
) -> Response:
    """The merchant's own settings (docs/plan-payments.md §6.1). No
    Idempotency-Key: setting the same values again changes nothing."""
    async with await connect(settings.database_url) as conn:
        auth = await _authenticated(conn, request, settings)
        if auth is None:
            return error_response(AUTH_FAILED)
        decoded = decode(await request.body(), SettingsIn)
        if isinstance(decoded, ApiError):
            return error_response(decoded)
        actor = Actor(ActorKind.MERCHANT_API_KEY, auth.key_id)
        await set_support_email(
            conn, auth.merchant.id, decoded.support_email, actor, now
        )
        seller = await read_merchant(conn, auth.merchant.id)
        assert seller is not None
        await conn.commit()
    return json_response(200, merchant_out(seller))


def _next_utc_midnight_s(now: datetime) -> int:
    midnight = datetime.combine(now.date() + timedelta(days=1), time(), tzinfo=UTC)
    return max(1, int((midnight - now).total_seconds()))


async def create_estimate(
    request: Request, settings: Settings, now: datetime
) -> Response:
    """No Idempotency-Key: an estimate stores nothing but asks, and asks are
    deduplicated by their own guards (answer 3 of 19-Nov-2026)."""
    async with await connect(settings.database_url) as conn:
        auth = await _authenticated(conn, request, settings)
        if auth is None:
            return error_response(AUTH_FAILED)
        decoded = decode(await request.body(), EstimateIn)
        if isinstance(decoded, ApiError):
            return error_response(decoded)
        market = await market_in_force(conn)
        if market is None:
            return error_response(NO_MARKET)
        lines = sold_lines(market, decoded.lines)
        if isinstance(lines, ApiError):
            return error_response(lines)
        quote_lines: list[QuoteLine] = []
        for line in lines:
            quote_lines.append(
                QuoteLine(
                    category=line.category,
                    hs_code=line.customs.hs_code,
                    origin=line.customs.origin_country,
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                )
            )
        quote_request = QuoteRequest(
            destination=decoded.country,
            lines=tuple(quote_lines),
            parcel=parcel_of(decoded.parcel),
            on=now.date(),
            now=now,
        )
        lookups = await held_lookups(conn, market, quote_request)
        prices = await held_prices(
            conn, market, quote_request.destination, quote_request.parcel
        )
        result = build_quote(market, quote_request, lookups, prices)
        match result:
            case Quote():
                offered = offered_in(result, settings.environment)
                if isinstance(offered, Refusal):
                    return error_response(refusal_error(offered))
                return json_response(200, estimate_out(offered))
            case InputsMissing():
                return error_response(await _ask(conn, auth, result, now))
            case _:
                return error_response(
                    refusal_error(refusal_of(result, decoded.country))
                )


async def _ask(
    conn: Connection, auth: Authenticated, missing: InputsMissing, now: datetime
) -> ApiError:
    """Ask for what is missing, within the merchant's daily cap on new
    lookups, and answer `estimate_pending`; or `rate_limited` with nothing
    asked. Commits what it asked."""
    pending = ApiError(
        status=503,
        type=ErrorType.PENDING,
        code="estimate_pending",
        message="prices are being fetched: send the same request again shortly",
        retry_after_s=ESTIMATE_RETRY_S,
    )
    count = new_asks(missing)
    if count == 0:
        return pending
    seller = auth.merchant
    if not await take_vendor_asks(
        conn, seller.id, now.date(), count, seller.daily_vendor_asks
    ):
        await conn.rollback()
        return ApiError(
            status=429,
            type=ErrorType.RATE_LIMITED,
            code="rate_limited",
            message="today's limit of new duty and carrier lookups is reached",
            retry_after_s=_next_utc_midnight_s(now),
        )
    actor = Actor(ActorKind.MERCHANT_API_KEY, auth.key_id)
    asked = await ask_missing(conn, missing, actor, now)
    if isinstance(asked, NoAccount):
        await conn.rollback()
        return ApiError(
            status=503,
            type=ErrorType.UNAVAILABLE,
            code="pricing_unavailable",
            message=f"pricing through {asked.name} is not set up here",
        )
    await conn.commit()
    return pending

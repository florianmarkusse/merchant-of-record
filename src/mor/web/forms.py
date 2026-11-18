"""What every form POST of ours shares: the cross-site guard and decoding
the form into its record."""

from urllib.parse import parse_qsl, urlsplit

import msgspec
from starlette.requests import Request


def same_origin(request: Request) -> bool:
    """The cross-site guard for every POST: the browser's Origin must name
    the host the request was sent to. No login or session cookie exists
    yet, so a SameSite cookie would guard nothing; it comes with the login
    (plan-test-setup §1)."""
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    if origin is None or host is None:
        return False
    return urlsplit(origin).netloc == host


async def read_form[T](request: Request, form_type: type[T]) -> T | None:
    """A URL-encoded form into its record, or None if it is not one."""
    body = await request.body()
    try:
        fields = dict(parse_qsl(body.decode("utf-8"), strict_parsing=False))
        return msgspec.convert(fields, form_type)
    except UnicodeDecodeError, msgspec.ValidationError:
        return None

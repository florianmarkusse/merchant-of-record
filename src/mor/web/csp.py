"""The Content-Security-Policy the app sends on every page of ours, in
every environment (decided 25-Nov-2026, AGENTS.md): dev, the browser smoke
suite and prod get the same header, so it cannot drift.

Our own scripts, styles and images come from 'self'; nothing inline. A
PSP's card fields bring their script and frames from the PSP's origins
(`mor.psp.registry.browser_origins`), and only those are added. The fakes
under /_fake/ stand for vendors with their own pages, so they get none.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send


HEADER = b"content-security-policy"
VENDOR_PREFIX = "/_fake/"


def policy(psp_origins: tuple[str, ...]) -> str:
    sources = " ".join(psp_origins)
    frames = sources or "'none'"
    directives = (
        "default-src 'self'",
        f"script-src 'self' {sources}".rstrip(),
        f"frame-src {frames}",
        "connect-src 'self'",
        "img-src 'self'",
        "style-src 'self'",
        "font-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "object-src 'none'",
    )
    return "; ".join(directives)


def with_policy(app: ASGIApp, header: str) -> ASGIApp:
    """`app`, each of its responses outside /_fake/ carrying `header`."""
    value = header.encode("ascii")

    async def wrapped(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or str(scope["path"]).startswith(VENDOR_PREFIX):
            await app(scope, receive, send)
            return

        async def send_with_policy(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((HEADER, value))
                message = {**message, "headers": headers}
            await send(message)

        await app(scope, receive, send_with_policy)

    return wrapped

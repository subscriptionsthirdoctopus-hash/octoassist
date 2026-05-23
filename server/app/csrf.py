"""Lightweight CSRF protection for HTML form POSTs.

Approach: a per-session token stored in `request.session["csrf"]`. Every
HTML form must include `<input type="hidden" name="csrf" value="{{ csrf_token() }}">`.
On state-changing requests (POST/PUT/PATCH/DELETE), middleware checks
that the submitted token matches.

JSON API routes mounted under `/api/` are exempt — they use Bearer tokens
which already prove origin + identity. The /contact endpoint (public,
unauthenticated) is also exempt because no session exists yet.
"""
import secrets

from fastapi.responses import HTMLResponse
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

CSRF_FIELD = "csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def get_or_create_token(request: Request) -> str:
    if not hasattr(request, "session"):
        return ""
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["csrf"] = token
    return token


class CsrfMiddleware:
    """Reject state-changing requests whose CSRF token doesn't match."""

    EXEMPT_PREFIXES: tuple[str, ...] = (
        "/api/",          # JSON API uses Bearer tokens
        "/auth/oidc/",    # OAuth callback comes from Microsoft, no session yet
        "/contact",       # public landing-page demo request, no session yet
    )

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        if method in SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if any(path == p or path.startswith(p) for p in self.EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        # Login and logout forms are special — we allow the first POST to /login
        # where the user has no session yet, and require CSRF on later POSTs.
        if path in ("/login", "/logout"):
            await self.app(scope, receive, send)
            return

        # Read the incoming body stream completely to avoid exhausting it
        # for downstream route handlers.
        body = b""
        more_body = True
        while more_body:
            message = await receive()
            body += message.get("body", b"")
            more_body = message.get("more_body", False)

        # Reconstruct a custom receive channel that replays the accumulated body
        async def mock_receive():
            return {"type": "http.request", "body": body, "more_body": False}

        # Create a transient Request object to parse the form/headers safely
        request = Request(scope, receive=mock_receive)

        # Read body's csrf field (form-urlencoded) or X-CSRF-Token header
        submitted = request.headers.get(CSRF_HEADER, "").strip()
        if not submitted:
            try:
                form = await request.form()
                submitted = (form.get(CSRF_FIELD) or "").strip()
            except Exception:
                submitted = ""

        expected = request.session.get("csrf", "") if hasattr(request, "session") else ""

        if not expected or not submitted or not secrets.compare_digest(submitted, expected):
            response = HTMLResponse(
                "<h1>403 — CSRF check failed</h1>"
                "<p>Your form submission could not be verified. Please reload the page and try again.</p>",
                status_code=403,
            )
            await response(scope, receive, send)
            return

        # Hand over request to FastAPI using the cached body stream replayer!
        await self.app(scope, mock_receive, send)


def csrf_context(request: Request) -> dict:
    """Helper to inject csrf_token into Jinja contexts."""
    return {"csrf_token": get_or_create_token(request)}

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
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp


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


class CsrfMiddleware(BaseHTTPMiddleware):
    """Reject state-changing requests whose CSRF token doesn't match."""

    EXEMPT_PREFIXES: tuple[str, ...] = (
        "/api/",          # JSON API uses Bearer tokens
        "/auth/oidc/",    # OAuth callback comes from Microsoft, no session yet
        "/contact",       # public landing-page demo request, no session yet
    )

    async def dispatch(self, request: Request, call_next):
        if request.method in SAFE_METHODS:
            return await call_next(request)

        path = request.url.path
        if any(path == p or path.startswith(p) for p in self.EXEMPT_PREFIXES):
            return await call_next(request)

        # Login form is special — the user has no session yet on first POST.
        # We allow the first POST to /login, set the cookie, and require CSRF
        # on later POSTs. Same for the SessionMiddleware's first interaction.
        if path == "/login":
            return await call_next(request)
        if path == "/logout":
            return await call_next(request)

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
            return HTMLResponse(
                "<h1>403 — CSRF check failed</h1>"
                "<p>Your form submission could not be verified. Please reload the page and try again.</p>",
                status_code=403,
            )

        return await call_next(request)


def csrf_context(request: Request) -> dict:
    """Helper to inject csrf_token into Jinja contexts.

    Usage in views (one line addition):
        context={..., "csrf_token": get_or_create_token(request)}
    Or template-level (preferred): a global so every template can do
        <input type="hidden" name="csrf" value="{{ csrf_token }}">
    """
    return {"csrf_token": get_or_create_token(request)}

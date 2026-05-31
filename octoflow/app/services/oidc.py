"""Microsoft Entra ID OIDC (authorization code flow).

Per-tenant: each OctoFlow tenant has its own IdentityProvider row with
its own Azure AD tenant_id + client_id + client_secret. Users signing
in through that IdP are auto-provisioned into the IdP's OctoFlow tenant
— there is structurally no way for a user to land in the wrong tenant.

We use the simple code-for-token flow with no PKCE (server-side, the
client_secret guards the exchange).  We trust login.microsoftonline.com
end-to-end over TLS rather than validating the ID token signature
locally; the token is only ever obtained from the official Microsoft
token endpoint over HTTPS, so the trust boundary is the same.
"""
from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from ..crypto import decrypt
from ..models import IdentityProvider


AUTHORIZE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
TOKEN     = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
GRAPH_ME  = "https://graph.microsoft.com/v1.0/me"

SCOPES    = "openid profile email User.Read"


class OidcError(Exception):
    pass


@dataclass
class SsoUser:
    subject:      str          # Entra `sub` claim (or `oid` fallback) — globally unique
    email:        str
    display_name: str
    raw:          dict          # full claims for debugging


def build_authorize_url(idp: IdentityProvider, redirect_uri: str,
                        state: str, nonce: str) -> str:
    if not idp.entra_tenant_id or not idp.entra_client_id:
        raise OidcError("Identity provider not fully configured (missing tenant_id or client_id)")
    params = {
        "client_id":     idp.entra_client_id,
        "response_type": "code",
        "redirect_uri":  redirect_uri,
        "response_mode": "query",
        "scope":         SCOPES,
        "state":         state,
        "nonce":         nonce,
    }
    return AUTHORIZE.format(tenant=idp.entra_tenant_id) + "?" + urlencode(params)


def random_state() -> str:
    return secrets.token_urlsafe(24)


def _decode_id_token_claims(id_token: str) -> dict:
    """Extract claims from the ID token WITHOUT verifying the signature.
    The token came from the official Microsoft token endpoint over TLS,
    so transport-level trust suffices for sub/email/name extraction.
    """
    try:
        _, payload_b64, _ = id_token.split(".")
        padding = "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    except Exception as e:
        raise OidcError(f"Could not decode ID token: {e}")


async def exchange_code_for_user(idp: IdentityProvider, code: str, redirect_uri: str) -> SsoUser:
    """Exchange the authorization code for tokens, then extract the user."""
    secret = decrypt(idp.entra_client_secret)
    if not secret:
        raise OidcError("Identity provider client_secret is missing or unreadable")

    data = {
        "grant_type":    "authorization_code",
        "client_id":     idp.entra_client_id,
        "client_secret": secret,
        "code":          code,
        "redirect_uri":  redirect_uri,
        "scope":         SCOPES,
    }
    token_url = TOKEN.format(tenant=idp.entra_tenant_id)
    async with httpx.AsyncClient(timeout=15) as cli:
        r = await cli.post(token_url, data=data,
                           headers={"Content-Type": "application/x-www-form-urlencoded"})
    if r.status_code != 200:
        raise OidcError(f"Token exchange failed: HTTP {r.status_code} {r.text[:200]}")
    tok = r.json()

    # Prefer the ID-token claims (they include sub + email + name)
    claims: dict = {}
    if "id_token" in tok:
        claims = _decode_id_token_claims(tok["id_token"])

    # Fall back to Graph /me if claims are sparse
    if not claims.get("email") or not claims.get("name"):
        async with httpx.AsyncClient(timeout=15) as cli:
            mr = await cli.get(GRAPH_ME,
                               headers={"Authorization": f"Bearer {tok['access_token']}"})
        if mr.status_code == 200:
            me = mr.json()
            claims.setdefault("email",
                              me.get("mail") or me.get("userPrincipalName"))
            claims.setdefault("name", me.get("displayName"))
            claims.setdefault("oid",  me.get("id"))

    subject = claims.get("sub") or claims.get("oid") or ""
    email   = (claims.get("email") or claims.get("preferred_username") or "").lower()
    name    = claims.get("name") or email.split("@")[0]
    if not subject or not email:
        raise OidcError("Could not extract user identity from token claims")
    return SsoUser(subject=subject, email=email, display_name=name, raw=claims)


def is_email_allowed(idp: IdentityProvider, email: str) -> bool:
    """If allowed_email_domains is set, the email's domain must be in it.
    Empty allow-list means accept anyone the IdP attests."""
    if not idp.allowed_email_domains:
        return True
    domain = email.lower().rsplit("@", 1)[-1]
    allowed = {d.strip().lower() for d in idp.allowed_email_domains.split(",") if d.strip()}
    return domain in allowed

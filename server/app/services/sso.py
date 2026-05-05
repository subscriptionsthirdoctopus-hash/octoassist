"""Microsoft Entra ID (formerly Azure AD) OpenID Connect client.

Implements the Authorization Code flow with id_token validation against
Microsoft's JWKS. No external OIDC library — kept transparent for audit.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient


@dataclass
class EntraConfig:
    entra_tenant_id: str
    client_id: str
    client_secret: str
    allowed_email_domains: list[str]


def parse_entra_config(raw: dict[str, Any]) -> EntraConfig:
    domains_raw = (raw.get("allowed_email_domains") or "").strip()
    domains = [d.strip().lower() for d in domains_raw.split(",") if d.strip()] if domains_raw else []
    return EntraConfig(
        entra_tenant_id=(raw.get("entra_tenant_id") or "").strip(),
        client_id=(raw.get("client_id") or "").strip(),
        client_secret=(raw.get("client_secret") or "").strip(),
        allowed_email_domains=domains,
    )


class EntraOidcError(RuntimeError):
    pass


class EntraOidc:
    """Lightweight per-IDP client. No global state."""

    def __init__(self, cfg: EntraConfig):
        if not cfg.entra_tenant_id or not cfg.client_id or not cfg.client_secret:
            raise EntraOidcError("entra_tenant_id, client_id, client_secret are all required")
        self.cfg = cfg
        self._discovery: dict | None = None
        self._discovery_at: float = 0.0

    @property
    def discovery_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.cfg.entra_tenant_id}/v2.0/.well-known/openid-configuration"

    async def discover(self) -> dict:
        # Cache for 1h
        if self._discovery and (time.time() - self._discovery_at) < 3600:
            return self._discovery
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(self.discovery_url)
            if r.status_code != 200:
                raise EntraOidcError(f"discovery {r.status_code}: {r.text[:200]}")
            self._discovery = r.json()
            self._discovery_at = time.time()
            return self._discovery

    async def authorize_url(self, *, redirect_uri: str, state: str, nonce: str) -> str:
        d = await self.discover()
        params = {
            "client_id": self.cfg.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": "openid profile email",
            "state": state,
            "nonce": nonce,
            "response_mode": "query",
        }
        return f"{d['authorization_endpoint']}?{urlencode(params)}"

    async def exchange_code(self, *, code: str, redirect_uri: str) -> dict:
        d = await self.discover()
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                d["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self.cfg.client_id,
                    "client_secret": self.cfg.client_secret,
                    "scope": "openid profile email",
                },
            )
            if r.status_code != 200:
                raise EntraOidcError(f"token exchange {r.status_code}: {r.text[:300]}")
            return r.json()

    async def validate_id_token(self, id_token: str, *, expected_nonce: str) -> dict:
        d = await self.discover()
        jwks = PyJWKClient(d["jwks_uri"])
        signing_key = jwks.get_signing_key_from_jwt(id_token)
        try:
            claims = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.cfg.client_id,
                issuer=d["issuer"],
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_aud": True,
                    "verify_iss": True,
                    "require": ["sub", "iss", "aud", "exp", "iat"],
                },
            )
        except jwt.PyJWTError as e:
            raise EntraOidcError(f"id_token invalid: {e}") from e
        if claims.get("nonce") != expected_nonce:
            raise EntraOidcError("id_token nonce mismatch")
        return claims


def new_state() -> str:
    return secrets.token_urlsafe(24)


def new_nonce() -> str:
    return secrets.token_urlsafe(16)

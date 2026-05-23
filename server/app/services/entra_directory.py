"""Microsoft Graph directory sync — pulls users from Entra ID into OctoAssist.

Uses the OAuth 2.0 client_credentials flow (app-only) against the same Entra
app registration that powers OIDC sign-in. Requires the additional Graph
application permission `User.Read.All` with admin consent granted.

Sync strategy:
- Match existing OctoAssist users by `entra_oid` first (immutable), then by
  email (case-insensitive). Renames in Entra (email change) are preserved.
- New users land with role=requester so they show up in Settings → Users for
  the admin to bulk-promote to agent/admin. They cannot do anything in the
  ITSM until promoted (requesters only see the end-user portal).
- We never overwrite role / is_active / password_hash from Entra — those are
  OctoAssist-side decisions an admin made; the sync is for *identity*, not
  *authorisation*.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from ..models import User, UserRole
from ..security import hash_password
from .sso import EntraConfig

log = logging.getLogger("octoassist.entra_directory")


class EntraDirectoryError(RuntimeError):
    pass


@dataclass
class SyncReport:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0  # rows with no email AND no oid — can't anchor
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []

    def summary(self) -> str:
        bits = [f"fetched={self.fetched}", f"created={self.created}",
                f"updated={self.updated}", f"skipped={self.skipped}"]
        if self.errors:
            bits.append(f"errors={len(self.errors)}")
        return ", ".join(bits)


async def _get_app_token(cfg: EntraConfig) -> str:
    """Client-credentials grant against Microsoft identity platform."""
    url = f"https://login.microsoftonline.com/{cfg.entra_tenant_id}/oauth2/v2.0/token"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, data={
            "grant_type":    "client_credentials",
            "client_id":     cfg.client_id,
            "client_secret": cfg.client_secret,
            "scope":         "https://graph.microsoft.com/.default",
        })
        if r.status_code != 200:
            raise EntraDirectoryError(f"token: {r.status_code} {r.text[:300]}")
        tok = r.json().get("access_token")
        if not tok:
            raise EntraDirectoryError("token response missing access_token")
        return tok


async def fetch_users(cfg: EntraConfig) -> list[dict[str, Any]]:
    """Pull every member user from the tenant. Filters out guests and disabled
    accounts. Handles @odata.nextLink pagination.
    """
    token = await _get_app_token(cfg)
    headers = {"Authorization": f"Bearer {token}",
               "ConsistencyLevel": "eventual"}
    # We $select to keep response size small + predictable.
    base = ("https://graph.microsoft.com/v1.0/users"
            "?$select=id,userPrincipalName,mail,displayName,givenName,surname,"
            "department,officeLocation,jobTitle,accountEnabled,userType"
            "&$top=999")
    out: list[dict] = []
    next_url: str | None = base
    async with httpx.AsyncClient(timeout=30) as c:
        while next_url:
            r = await c.get(next_url, headers=headers)
            if r.status_code != 200:
                raise EntraDirectoryError(f"users list: {r.status_code} {r.text[:400]}")
            payload = r.json()
            for u in (payload.get("value") or []):
                # Skip guests + disabled accounts — we don't want them as
                # OctoAssist users by default. Admin can still create
                # manually if needed.
                if (u.get("userType") or "").lower() == "guest":
                    continue
                if u.get("accountEnabled") is False:
                    continue
                out.append(u)
            next_url = payload.get("@odata.nextLink")
    return out


def upsert_user(
    db: Session, *, tenant_id: int, graph_user: dict[str, Any],
    allowed_domains: list[str],
) -> tuple[User | None, str]:
    """Insert or update one user from a Graph user record.

    Returns (user, status) where status is 'created' | 'updated' | 'skipped'.
    """
    import secrets as _s

    oid   = (graph_user.get("id") or "").strip()
    upn   = (graph_user.get("userPrincipalName") or "").strip()
    mail  = (graph_user.get("mail") or "").strip()
    email = (mail or upn).lower()
    if not email and not oid:
        return None, "skipped"

    # Domain allow-list (so a misconfigured tenant doesn't dump every guest in)
    if allowed_domains:
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        if domain and domain not in allowed_domains:
            return None, "skipped"

    full_name  = (graph_user.get("displayName") or "").strip()
    department = (graph_user.get("department") or "").strip() or None
    location   = (graph_user.get("officeLocation") or "").strip() or None
    job_title  = (graph_user.get("jobTitle") or "").strip() or None

    # Match by entra_oid first, then fall back to email.
    existing = None
    if oid:
        existing = db.query(User).filter(User.entra_oid == oid).first()
    if existing is None and email:
        existing = db.query(User).filter(User.email == email).first()

    now = datetime.now(timezone.utc)

    if existing is None:
        u = User(
            tenant_id=tenant_id,
            email=email,
            password_hash=hash_password(_s.token_urlsafe(32)),  # unusable — SSO-only
            full_name=full_name or email.split("@", 1)[0],
            role=UserRole.requester,   # admin promotes via bulk UI
            is_active=True,
            entra_oid=oid or None,
            location=location,
            department=department,
            job_title=job_title,
            synced_at=now,
        )
        db.add(u)
        return u, "created"

    # Update — preserve role, is_active, password_hash.
    existing.entra_oid  = oid or existing.entra_oid
    if email:
        existing.email = email
    if full_name:
        existing.full_name = full_name
    existing.location   = location
    existing.department = department
    existing.job_title  = job_title
    existing.synced_at  = now
    return existing, "updated"


async def sync_users(
    db: Session, *, tenant_id: int, cfg: EntraConfig,
) -> SyncReport:
    """End-to-end: pull from Graph, upsert each, commit, return a report."""
    report = SyncReport()
    try:
        users = await fetch_users(cfg)
    except EntraDirectoryError as e:
        report.errors.append(str(e))
        return report

    report.fetched = len(users)
    for u in users:
        try:
            _, status = upsert_user(
                db, tenant_id=tenant_id, graph_user=u,
                allowed_domains=cfg.allowed_email_domains,
            )
            if status == "created":
                report.created += 1
            elif status == "updated":
                report.updated += 1
            else:
                report.skipped += 1
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"{u.get('userPrincipalName','?')}: {e}")
    db.commit()
    log.info("entra sync done: %s", report.summary())
    return report

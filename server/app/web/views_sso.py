"""OIDC sign-in flow (start + callback)."""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import IdentityProvider, IdentityProviderKind, User, UserRole
from ..security import hash_password
from ..services.sso import (
    EntraOidc, EntraOidcError, parse_entra_config, new_nonce, new_state,
)

log = logging.getLogger("octoassist.sso")
router = APIRouter(tags=["sso"])


def _redirect_uri(request: Request, idp_id: int) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/auth/oidc/{idp_id}/callback"


@router.get("/auth/oidc/{idp_id}/start")
async def start(
    idp_id: int,
    request: Request,
    next: str | None = None,
    db: Session = Depends(get_db),
):
    idp = db.get(IdentityProvider, idp_id)
    if idp is None or not idp.is_enabled or idp.kind != IdentityProviderKind.entra:
        raise HTTPException(status_code=404, detail="Identity provider not available")

    cfg = parse_entra_config(idp.config or {})
    state = new_state()
    nonce = new_nonce()
    request.session["oidc_state"] = state
    request.session["oidc_nonce"] = nonce
    request.session["oidc_next"] = next or "/"
    request.session["oidc_idp"] = idp.id

    try:
        client = EntraOidc(cfg)
        url = await client.authorize_url(
            redirect_uri=_redirect_uri(request, idp.id),
            state=state,
            nonce=nonce,
        )
    except EntraOidcError as e:
        log.warning("oidc start failed for idp=%s: %s", idp.id, e)
        return RedirectResponse(url=f"/login?error={'SSO+misconfigured: '+str(e)[:100]}", status_code=303)
    return RedirectResponse(url=url, status_code=303)


@router.get("/auth/oidc/{idp_id}/callback")
async def callback(
    idp_id: int,
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
):
    idp = db.get(IdentityProvider, idp_id)
    if idp is None or not idp.is_enabled or idp.kind != IdentityProviderKind.entra:
        raise HTTPException(status_code=404)

    if error:
        log.warning("oidc callback error idp=%s: %s — %s", idp.id, error, error_description)
        return RedirectResponse(url=f"/login?error=SSO+error:+{error}", status_code=303)
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    expected_state = request.session.get("oidc_state")
    expected_nonce = request.session.get("oidc_nonce")
    next_url = request.session.get("oidc_next") or "/"
    if not expected_state or state != expected_state:
        raise HTTPException(status_code=400, detail="Invalid state")
    # Clear so the same code can't be replayed
    for k in ("oidc_state", "oidc_nonce", "oidc_next", "oidc_idp"):
        request.session.pop(k, None)

    cfg = parse_entra_config(idp.config or {})
    try:
        client = EntraOidc(cfg)
        tokens = await client.exchange_code(code=code, redirect_uri=_redirect_uri(request, idp.id))
        id_token = tokens.get("id_token")
        if not id_token:
            raise EntraOidcError("token response missing id_token")
        claims = await client.validate_id_token(id_token, expected_nonce=expected_nonce or "")
    except EntraOidcError as e:
        log.warning("oidc exchange/validation failed idp=%s: %s", idp.id, e)
        return RedirectResponse(url=f"/login?error=SSO+failed:+{str(e)[:100]}", status_code=303)

    email = (claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    full_name = (claims.get("name") or "").strip()
    if not email:
        return RedirectResponse(url="/login?error=SSO+account+has+no+email+claim", status_code=303)

    # Optional domain allow-list
    if cfg.allowed_email_domains:
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        if domain not in cfg.allowed_email_domains:
            return RedirectResponse(
                url=f"/login?error=Email+domain+{domain}+is+not+allowed+by+this+tenant",
                status_code=303,
            )

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        if not idp.auto_provision:
            return RedirectResponse(
                url=f"/login?error=No+account+for+{email}+(auto-provision+is+off)",
                status_code=303,
            )
        # Provision a new user. Random local password (unusable — they'll keep using SSO).
        import secrets as _s
        user = User(
            tenant_id=idp.tenant_id,
            email=email,
            password_hash=hash_password(_s.token_urlsafe(32)),
            full_name=full_name or email.split("@", 1)[0],
            role=idp.default_role,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        log.info("SSO auto-provisioned user email=%s role=%s", user.email, user.role.value)

    if not user.is_active:
        return RedirectResponse(url="/login?error=Account+is+deactivated", status_code=303)

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    request.session["user_id"] = user.id
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"
    return RedirectResponse(url=next_url, status_code=303)

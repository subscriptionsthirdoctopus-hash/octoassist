"""OIDC SSO routes — /auth/oidc/{idp_id}/start and /callback."""
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import IdentityProvider, User
from ..services import oidc

router = APIRouter(tags=["sso"])


def _redirect_uri(request: Request, idp_id: int) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/auth/oidc/{idp_id}/callback"


@router.get("/auth/oidc/{idp_id}/start")
def oidc_start(request: Request, idp_id: int,
               next: str = "/",
               db: Session = Depends(get_db)):
    idp = db.get(IdentityProvider, idp_id)
    if not idp or not idp.is_active:
        raise HTTPException(404, "IdP not found or inactive")

    state = oidc.random_state()
    nonce = oidc.random_state()
    request.session["oidc_state"] = state
    request.session["oidc_nonce"] = nonce
    request.session["oidc_next"]  = next or "/"
    request.session["oidc_idp"]   = idp_id

    try:
        url = oidc.build_authorize_url(idp, _redirect_uri(request, idp_id), state, nonce)
    except oidc.OidcError as e:
        return RedirectResponse(url=f"/login?error={quote(str(e))}", status_code=303)
    return RedirectResponse(url=url, status_code=303)


@router.get("/auth/oidc/{idp_id}/callback")
async def oidc_callback(request: Request, idp_id: int,
                        code: str | None = None, state: str | None = None,
                        error: str | None = None,
                        db: Session = Depends(get_db)):
    if error:
        return RedirectResponse(url=f"/login?error={quote(error)}", status_code=303)
    if not code:
        return RedirectResponse(url="/login?error=No+code+returned", status_code=303)

    expected = request.session.pop("oidc_state", None)
    if state != expected:
        return RedirectResponse(url="/login?error=State+mismatch", status_code=303)
    expected_idp = request.session.pop("oidc_idp", None)
    if expected_idp != idp_id:
        return RedirectResponse(url="/login?error=IdP+mismatch", status_code=303)

    idp = db.get(IdentityProvider, idp_id)
    if not idp or not idp.is_active:
        return RedirectResponse(url="/login?error=IdP+inactive", status_code=303)

    try:
        sso_user = await oidc.exchange_code_for_user(idp, code, _redirect_uri(request, idp_id))
    except oidc.OidcError as e:
        return RedirectResponse(url=f"/login?error={quote(str(e))}", status_code=303)

    # Optional email-domain allow-list
    if not oidc.is_email_allowed(idp, sso_user.email):
        return RedirectResponse(url=f"/login?error={quote('Email domain not allowed by this provider')}", status_code=303)

    # Find-or-create the user IN THE IdP's tenant (no cross-tenant write)
    user = db.query(User).filter(User.entra_subject == sso_user.subject).first()
    if user is None:
        # Try email-within-tenant
        user = (db.query(User)
                  .filter(User.tenant_id == idp.tenant_id,
                          User.email == sso_user.email)
                  .first())
    if user is None:
        # Auto-provision
        user = User(
            tenant_id=idp.tenant_id,
            email=sso_user.email,
            display_name=sso_user.display_name,
            role=idp.default_role,
            entra_subject=sso_user.subject,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        # Existing user — bind entra_subject if missing, refresh display_name
        if not user.entra_subject:
            user.entra_subject = sso_user.subject
        if sso_user.display_name and not user.display_name:
            user.display_name = sso_user.display_name
        # IMPORTANT: never cross-bind to a different tenant. If somehow the
        # subject is bound to another tenant, refuse.
        if user.tenant_id != idp.tenant_id:
            return RedirectResponse(
                url=f"/login?error={quote('This account is bound to a different OctoFlow tenant.')}",
                status_code=303)
        if not user.is_active:
            return RedirectResponse(url=f"/login?error={quote('Account is deactivated.')}", status_code=303)
        db.commit()

    request.session["user_id"] = user.id
    next_url = request.session.pop("oidc_next", "/") or "/"
    return RedirectResponse(url=next_url, status_code=303)

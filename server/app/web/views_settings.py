"""Admin: tenant settings — identity providers (SSO), tenant info."""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from ..jinja_filters import install_on
from sqlalchemy.orm import Session

import secrets

from ..auth import require_admin
from ..database import get_db
from ..models import (
    Agent, Category, CategoryRule, IdentityProvider, IdentityProviderKind, LocationRule, Tenant,
    TicketKind, TicketPriority, User, UserRole,
)
from ..services.sso import EntraConfig, EntraOidc, EntraOidcError, parse_entra_config

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
install_on(templates)

router = APIRouter(tags=["settings"])


def _redirect_uri(request: Request, idp_id: int) -> str:
    """Build the redirect_uri the admin should paste into Azure portal."""
    base = str(request.base_url).rstrip("/")
    return f"{base}/auth/oidc/{idp_id}/callback"


@router.get("/settings", response_class=HTMLResponse)
def settings_home(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    flash: str | None = None,
):
    """Tile-grid landing page. Counts shown on each tile for at-a-glance state."""
    tenant = db.query(Tenant).first()
    from sqlalchemy import func as _f
    users_count      = (db.query(_f.count(User.id))
                          .filter(User.tenant_id == user.tenant_id,
                                  User.is_active == True).scalar()) or 0  # noqa: E712
    idps_count       = (db.query(_f.count(IdentityProvider.id))
                          .filter(IdentityProvider.tenant_id == user.tenant_id).scalar()) or 0
    categories_count = (db.query(_f.count(Category.id))
                          .filter(Category.tenant_id == user.tenant_id,
                                  Category.is_active == True).scalar()) or 0  # noqa: E712
    location_rules_count = (db.query(_f.count(LocationRule.id))
                              .filter(LocationRule.tenant_id == user.tenant_id).scalar()) or 0
    category_rules_count = (db.query(_f.count(CategoryRule.id))
                              .filter(CategoryRule.tenant_id == user.tenant_id).scalar()) or 0
    return templates.TemplateResponse(
        request=request, name="settings.html",
        context={
            "current_user": user, "tenant": tenant,
            "users_count": int(users_count),
            "idps_count": int(idps_count),
            "categories_count": int(categories_count),
            "location_rules_count": int(location_rules_count),
            "category_rules_count": int(category_rules_count),
            "flash": flash,
        },
    )


# ---------------------------- Location routing rules ----------------------------

@router.get("/settings/locations", response_class=HTMLResponse)
def settings_locations(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    flash: str | None = None,
    error: str | None = None,
):
    """Per-location default assignee rules. New tickets stamped with `location`
    by services.location_routing get auto-assigned to the rule's user."""
    tenant = db.query(Tenant).first()
    rules = (db.query(LocationRule)
               .filter(LocationRule.tenant_id == user.tenant_id)
               .order_by(LocationRule.location).all())
    # Distinct locations OctoAssist already knows about (from users + agents) —
    # let the admin pick from a dropdown instead of typing free text.
    from sqlalchemy import distinct
    user_locs  = {loc for (loc,) in db.query(distinct(User.location))
                                       .filter(User.tenant_id == user.tenant_id,
                                               User.location.is_not(None)).all() if loc}
    agent_locs = {loc for (loc,) in db.query(distinct(Agent.location))
                                       .filter(Agent.tenant_id == user.tenant_id,
                                               Agent.location.is_not(None)).all() if loc}
    known_locations = sorted(user_locs | agent_locs)
    staff = (db.query(User)
               .filter(User.tenant_id == user.tenant_id,
                       User.is_active == True,  # noqa: E712
                       User.role.in_([UserRole.admin, UserRole.agent]))
               .order_by(User.full_name, User.email).all())
    return templates.TemplateResponse(
        request=request, name="settings_locations.html",
        context={
            "current_user": user, "tenant": tenant,
            "rules": rules, "known_locations": known_locations,
            "staff": staff, "flash": flash, "error": error,
        },
    )


@router.post("/settings/locations/new")
def create_location_rule(
    location: str = Form(...),
    default_assignee_id: int = Form(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from urllib.parse import quote
    loc = (location or "").strip()
    if not loc:
        return RedirectResponse(url="/settings/locations?error=Location+is+required", status_code=303)
    assignee = db.get(User, default_assignee_id)
    if assignee is None or assignee.tenant_id != user.tenant_id or not assignee.is_active:
        return RedirectResponse(url="/settings/locations?error=Invalid+assignee", status_code=303)
    if assignee.role not in (UserRole.admin, UserRole.agent):
        return RedirectResponse(
            url=f"/settings/locations?error={quote('Assignee must be admin or agent (not requester)')}",
            status_code=303,
        )
    # Reject duplicate location (case-insensitive)
    from sqlalchemy import func as _f
    existing = (db.query(LocationRule)
                  .filter(LocationRule.tenant_id == user.tenant_id,
                          _f.lower(LocationRule.location) == loc.lower())
                  .first())
    if existing:
        return RedirectResponse(
            url=f"/settings/locations?error={quote(f'A rule for location {loc!r} already exists — delete it first')}",
            status_code=303,
        )
    db.add(LocationRule(tenant_id=user.tenant_id, location=loc,
                        default_assignee_id=assignee.id))
    db.commit()
    return RedirectResponse(
        url=f"/settings/locations?flash={quote(f'Routing rule added: {loc} → {assignee.display_name}')}",
        status_code=303,
    )


@router.post("/settings/locations/{rule_id}/delete")
def delete_location_rule(
    rule_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from urllib.parse import quote
    r = db.get(LocationRule, rule_id)
    if r is None or r.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    loc = r.location
    db.delete(r); db.commit()
    return RedirectResponse(
        url=f"/settings/locations?flash={quote(f'Routing rule for {loc} deleted')}",
        status_code=303,
    )


# Dedicated sub-pages, opened from the tile grid
@router.get("/settings/identity", response_class=HTMLResponse)
def settings_identity(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    flash: str | None = None,
):
    tenant = db.query(Tenant).first()
    idps = (db.query(IdentityProvider)
              .filter(IdentityProvider.tenant_id == user.tenant_id)
              .order_by(IdentityProvider.created_at).all())
    return templates.TemplateResponse(
        request=request, name="settings_identity.html",
        context={"current_user": user, "tenant": tenant, "idps": idps,
                 "flash": flash,
                 "is_https": request.url.scheme == "https"},
    )


@router.get("/settings/notifications", response_class=HTMLResponse)
def settings_notifications(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    flash: str | None = None,
):
    tenant = db.query(Tenant).first()
    return templates.TemplateResponse(
        request=request, name="settings_notifications.html",
        context={"current_user": user, "tenant": tenant, "flash": flash},
    )


@router.get("/settings/tenant", response_class=HTMLResponse)
def settings_tenant(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    flash: str | None = None,
):
    tenant = db.query(Tenant).first()
    return templates.TemplateResponse(
        request=request, name="settings_tenant.html",
        context={"current_user": user, "tenant": tenant, "flash": flash},
    )


@router.get("/settings/roadmap", response_class=HTMLResponse)
def settings_roadmap(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    tenant = db.query(Tenant).first()
    return templates.TemplateResponse(
        request=request, name="settings_roadmap.html",
        context={"current_user": user, "tenant": tenant},
    )


@router.post("/settings/idp/entra/new")
def create_entra_idp(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a stub Entra ID provider so admin can fill in details on next page."""
    existing = (db.query(IdentityProvider)
                  .filter(IdentityProvider.tenant_id == user.tenant_id,
                          IdentityProvider.kind == IdentityProviderKind.entra)
                  .first())
    if existing:
        return RedirectResponse(url=f"/settings/idp/{existing.id}", status_code=303)

    idp = IdentityProvider(
        tenant_id=user.tenant_id,
        kind=IdentityProviderKind.entra,
        display_name="Microsoft Entra ID",
        is_enabled=False,
        auto_provision=True,
        default_role=UserRole.requester,
        config={"entra_tenant_id": "", "client_id": "", "client_secret": "", "allowed_email_domains": ""},
    )
    db.add(idp)
    db.commit()
    db.refresh(idp)
    return RedirectResponse(url=f"/settings/idp/{idp.id}", status_code=303)


@router.get("/settings/idp/{idp_id}", response_class=HTMLResponse)
def edit_idp(
    idp_id: int,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    flash: str | None = None,
    error: str | None = None,
):
    idp = db.get(IdentityProvider, idp_id)
    if idp is None or idp.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    tenant = db.query(Tenant).first()
    return templates.TemplateResponse(
        request=request, name="settings_idp_edit.html",
        context={
            "current_user": user, "tenant": tenant, "idp": idp,
            "redirect_uri": _redirect_uri(request, idp.id),
            "roles": [r.value for r in UserRole],
            "is_https": request.url.scheme == "https",
            "flash": flash, "error": error,
        },
    )


@router.post("/settings/idp/{idp_id}/save")
def save_idp(
    idp_id: int,
    request: Request,
    display_name: str = Form(""),
    entra_tenant_id: str = Form(""),
    client_id: str = Form(""),
    client_secret: str = Form(""),
    allowed_email_domains: str = Form(""),
    auto_provision: int = Form(0),
    default_role: str = Form("requester"),
    is_enabled: int = Form(0),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    idp = db.get(IdentityProvider, idp_id)
    if idp is None or idp.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)

    idp.display_name = display_name.strip() or "Microsoft Entra ID"
    idp.auto_provision = bool(auto_provision)
    try:
        idp.default_role = UserRole(default_role)
    except ValueError:
        idp.default_role = UserRole.requester

    new_secret = client_secret.strip()
    cfg = dict(idp.config or {})
    cfg["entra_tenant_id"] = entra_tenant_id.strip()
    cfg["client_id"] = client_id.strip()
    if new_secret:  # only overwrite if a new secret was typed
        cfg["client_secret"] = new_secret
    cfg["allowed_email_domains"] = allowed_email_domains.strip()
    idp.config = cfg
    idp.is_enabled = bool(is_enabled)
    idp.updated_at = datetime.now(timezone.utc)
    db.commit()

    return RedirectResponse(url=f"/settings/idp/{idp.id}?flash=Saved", status_code=303)


@router.post("/settings/idp/{idp_id}/test")
async def test_idp(
    idp_id: int,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    idp = db.get(IdentityProvider, idp_id)
    if idp is None or idp.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)

    cfg = parse_entra_config(idp.config or {})
    now = datetime.now(timezone.utc)
    idp.last_test_at = now
    try:
        client = EntraOidc(cfg)
        d = await client.discover()
        idp.last_test_ok = True
        idp.last_test_message = (
            f"OK — issuer={d.get('issuer','?')[:120]} authorization_endpoint={d.get('authorization_endpoint','?')[:120]}"
        )
        msg = "Discovery document fetched successfully."
    except EntraOidcError as e:
        idp.last_test_ok = False
        idp.last_test_message = str(e)[:1000]
        msg = f"Test failed: {e}"
    except Exception as e:  # noqa: BLE001
        idp.last_test_ok = False
        idp.last_test_message = f"unexpected: {e}"[:1000]
        msg = f"Test failed (unexpected): {e}"
    db.commit()
    return RedirectResponse(url=f"/settings/idp/{idp.id}?flash={msg}", status_code=303)


@router.post("/settings/idp/{idp_id}/delete")
def delete_idp(
    idp_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    idp = db.get(IdentityProvider, idp_id)
    if idp is None or idp.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    db.delete(idp)
    db.commit()
    return RedirectResponse(url="/settings?flash=Provider+removed", status_code=303)


@router.post("/settings/tenant/regenerate-enrolment-key")
def regenerate_enrolment_key(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Generate a fresh tenant enrolment key.

    Existing agents that have already registered are unaffected — they hold
    their own long-lived bearer tokens. Only future MSI deployments will need
    the new key.
    """
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if tenant is None:
        raise HTTPException(status_code=404)
    tenant.enrolment_key = secrets.token_urlsafe(32)
    db.commit()
    return RedirectResponse(
        url="/settings?flash=New+enrolment+key+generated.+Existing+agents+keep+working;+only+new+MSI+installs+need+the+new+key.",
        status_code=303,
    )


# Allowed domains for the notification email — Microsoft accounts only,
# per the spec ("considering its only Microsoft email id").
ALLOWED_NOTIFICATION_DOMAINS = (
    "thirdoctopus.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "msn.com",
)
ALLOWED_NOTIFICATION_DOMAIN_SUFFIXES = (
    ".onmicrosoft.com",
)


def _is_microsoft_email(email: str) -> bool:
    email = email.strip().lower()
    if "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1]
    if domain in ALLOWED_NOTIFICATION_DOMAINS:
        return True
    return any(domain.endswith(suf) for suf in ALLOWED_NOTIFICATION_DOMAIN_SUFFIXES)


@router.post("/settings/tenant/smtp")
def save_smtp_config(
    smtp_host: str = Form(""),
    smtp_port: str = Form("587"),
    smtp_username: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from: str = Form(""),
    smtp_use_tls: int = Form(1),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if tenant is None:
        raise HTTPException(status_code=404)
    tenant.smtp_host = (smtp_host or "").strip() or None
    try:
        tenant.smtp_port = int(smtp_port) if str(smtp_port).strip() else None
    except (TypeError, ValueError):
        tenant.smtp_port = None
    tenant.smtp_username = (smtp_username or "").strip() or None
    if smtp_password and smtp_password.strip():
        from ..crypto import encrypt
        tenant.smtp_password = encrypt(smtp_password)
    tenant.smtp_from = (smtp_from or "").strip() or None
    tenant.smtp_use_tls = bool(smtp_use_tls)
    db.commit()
    return RedirectResponse(url="/settings?flash=SMTP+settings+saved.", status_code=303)


@router.post("/settings/tenant/mail-provider")
def set_mail_provider(
    mail_provider: str = Form("smtp"),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if tenant is None:
        raise HTTPException(status_code=404)
    if mail_provider not in ("smtp", "graph"):
        raise HTTPException(status_code=400, detail="Invalid provider")
    tenant.mail_provider = mail_provider
    db.commit()
    return RedirectResponse(
        url=f"/settings?flash=Mail+provider+set+to+{mail_provider}.",
        status_code=303,
    )


@router.post("/settings/tenant/graph")
def save_graph_config(
    graph_tenant_id: str = Form(""),
    graph_client_id: str = Form(""),
    graph_client_secret: str = Form(""),
    graph_from: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from ..crypto import encrypt
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if tenant is None:
        raise HTTPException(status_code=404)
    tenant.graph_tenant_id = (graph_tenant_id or "").strip() or None
    tenant.graph_client_id = (graph_client_id or "").strip() or None
    if graph_client_secret and graph_client_secret.strip():
        tenant.graph_client_secret = encrypt(graph_client_secret)
    tenant.graph_from = (graph_from or "").strip() or None
    db.commit()
    return RedirectResponse(url="/settings?flash=Graph+settings+saved.", status_code=303)


@router.post("/settings/tenant/smtp/test")
def smtp_test(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from datetime import datetime as _dt, timezone as _tz
    from ..services.email import send_email, EmailError
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if tenant is None:
        raise HTTPException(status_code=404)
    if not tenant.notification_email:
        return RedirectResponse(
            url="/settings?flash=Set+a+Notification+email+first+(Microsoft+address+only).",
            status_code=303,
        )
    now = _dt.now(_tz.utc)
    tenant.smtp_last_test_at = now
    try:
        msg = send_email(
            tenant=tenant,
            to=tenant.notification_email,
            subject="OctoAssist — SMTP test",
            body_text=("This is a test email from OctoAssist confirming that "
                       "outbound SMTP is configured correctly for "
                       f"tenant '{tenant.name}'. Sent {now.isoformat()}."),
        )
        tenant.smtp_last_test_ok = True
        tenant.smtp_last_test_message = msg[:1000]
        flash = f"Test+email+sent+to+{tenant.notification_email}+OK"
    except EmailError as e:
        tenant.smtp_last_test_ok = False
        tenant.smtp_last_test_message = str(e)[:1000]
        flash = f"Test+failed:+{str(e)[:80]}"
    db.commit()
    return RedirectResponse(url=f"/settings?flash={flash}", status_code=303)


# ---------------------------------------------------------------------------
# SLA matrix — edit response + resolution targets per category
# ---------------------------------------------------------------------------

@router.get("/settings/sla", response_class=HTMLResponse)
def sla_matrix(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    flash: str | None = None,
):
    tenant = db.query(Tenant).first()
    cats = (db.query(Category)
              .filter(Category.tenant_id == user.tenant_id)
              .order_by(Category.kind, Category.name).all())
    return templates.TemplateResponse(
        request=request, name="settings_sla.html",
        context={
            "current_user": user, "tenant": tenant,
            "categories": cats,
            "priorities": [p.value for p in TicketPriority],
            "kinds": [k.value for k in TicketKind],
            "flash": flash,
        },
    )


@router.post("/settings/sla")
async def sla_save(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Bulk-save the SLA matrix. Form fields are namespaced:
        cat_{id}_priority      = "low" | "medium" | "high" | "critical"
        cat_{id}_response_min  = int
        cat_{id}_resolution_min = int
        cat_{id}_requires_approval = "1" or absent
        cat_{id}_is_active     = "1" or absent
    """
    form = await request.form()
    cats = (db.query(Category)
              .filter(Category.tenant_id == user.tenant_id).all())
    updated = 0
    for c in cats:
        pri_raw = form.get(f"cat_{c.id}_priority", c.default_priority.value)
        try:
            c.default_priority = TicketPriority(pri_raw)
        except ValueError:
            pass

        for attr, field in (("sla_response_minutes",   "response_min"),
                            ("sla_resolution_minutes", "resolution_min")):
            raw = form.get(f"cat_{c.id}_{field}")
            if raw is None or str(raw).strip() == "":
                continue
            try:
                v = int(raw)
                if v > 0:
                    setattr(c, attr, v)
            except (TypeError, ValueError):
                pass

        c.requires_approval = form.get(f"cat_{c.id}_requires_approval") == "1"
        c.is_active         = form.get(f"cat_{c.id}_is_active") == "1"
        updated += 1
    db.commit()
    return RedirectResponse(
        url=f"/settings/sla?flash=Saved+SLA+matrix+for+{updated}+categories.",
        status_code=303,
    )


@router.post("/settings/tenant/notification-email")
def set_notification_email(
    notification_email: str = Form(""),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if tenant is None:
        raise HTTPException(status_code=404)

    raw = (notification_email or "").strip()
    if not raw:
        # Empty = remove
        tenant.notification_email = None
        db.commit()
        return RedirectResponse(url="/settings?flash=Notification+email+cleared.", status_code=303)

    if not _is_microsoft_email(raw):
        msg = ("Only+Microsoft+email+addresses+are+accepted:+thirdoctopus.com,+"
               "outlook.com,+hotmail.com,+live.com,+msn.com,+or+*.onmicrosoft.com.")
        return RedirectResponse(url=f"/settings?flash={msg}", status_code=303)

    tenant.notification_email = raw.lower()
    db.commit()
    return RedirectResponse(
        url=f"/settings?flash=Notification+email+set+to+{tenant.notification_email}",
        status_code=303,
    )


# ---------------------------- Phase F: Category routing rules ----------------------------

@router.get("/settings/categories-routing", response_class=HTMLResponse)
def settings_category_rules(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    flash: str | None = None,
    error: str | None = None,
):
    tenant = db.query(Tenant).first()
    rules = (db.query(CategoryRule)
               .filter(CategoryRule.tenant_id == user.tenant_id)
               .all())
    # Categories without a rule yet — admin picks from this dropdown.
    used_cat_ids = {r.category_id for r in rules}
    available_cats = (db.query(Category)
                        .filter(Category.tenant_id == user.tenant_id,
                                Category.is_active == True,  # noqa: E712
                                ~Category.id.in_(used_cat_ids) if used_cat_ids else True)
                        .order_by(Category.kind, Category.name).all())
    staff = (db.query(User)
               .filter(User.tenant_id == user.tenant_id,
                       User.is_active == True,  # noqa: E712
                       User.role.in_([UserRole.admin, UserRole.agent]))
               .order_by(User.full_name, User.email).all())
    return templates.TemplateResponse(
        request=request, name="settings_category_rules.html",
        context={"current_user": user, "tenant": tenant,
                 "rules": rules, "available_cats": available_cats,
                 "staff": staff, "flash": flash, "error": error},
    )


@router.post("/settings/categories-routing/new")
def create_category_rule(
    category_id: int = Form(...),
    default_assignee_id: int = Form(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from urllib.parse import quote
    cat = db.get(Category, category_id)
    if cat is None or cat.tenant_id != user.tenant_id:
        return RedirectResponse(url="/settings/categories-routing?error=Invalid+category", status_code=303)
    assignee = db.get(User, default_assignee_id)
    if assignee is None or assignee.tenant_id != user.tenant_id or not assignee.is_active:
        return RedirectResponse(url="/settings/categories-routing?error=Invalid+assignee", status_code=303)
    if assignee.role not in (UserRole.admin, UserRole.agent):
        return RedirectResponse(
            url=f"/settings/categories-routing?error={quote('Assignee must be admin or agent')}",
            status_code=303,
        )
    existing = (db.query(CategoryRule)
                  .filter(CategoryRule.tenant_id == user.tenant_id,
                          CategoryRule.category_id == category_id).first())
    if existing:
        return RedirectResponse(
            url=f"/settings/categories-routing?error={quote('A rule for this category already exists')}",
            status_code=303,
        )
    db.add(CategoryRule(tenant_id=user.tenant_id, category_id=category_id,
                        default_assignee_id=assignee.id))
    db.commit()
    return RedirectResponse(
        url=f"/settings/categories-routing?flash={quote(f'Rule added: {cat.name} -> {assignee.display_name}')}",
        status_code=303,
    )


@router.post("/settings/categories-routing/{rule_id}/delete")
def delete_category_rule(
    rule_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from urllib.parse import quote
    r = db.get(CategoryRule, rule_id)
    if r is None or r.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    cat_name = r.category.name if r.category else f"#{r.category_id}"
    db.delete(r); db.commit()
    return RedirectResponse(
        url=f"/settings/categories-routing?flash={quote(f'Rule for {cat_name} deleted')}",
        status_code=303,
    )

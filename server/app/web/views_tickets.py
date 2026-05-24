"""Agent / admin ticket views: list, create, detail, comment, transition."""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from ..jinja_filters import install_on
from sqlalchemy.orm import Session

from ..auth import current_user, require_staff, require_admin
from ..database import get_db
from ..models import (
    Category, Tenant, Ticket, TicketAttachment, TicketEventKind,
    TicketKind, TicketPriority, TicketStatus, UploadedFile, User, UserRole,
)
from ..services import ticketing
from ..services.sla import time_to_breach

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
install_on(templates)

router = APIRouter(tags=["tickets"])


def _ctx(user: User, db: Session, **extra) -> dict:
    tenant = db.query(Tenant).first()
    return {"current_user": user, "tenant": tenant, "time_to_breach": time_to_breach,
            "format_event": _format_event, **extra}


def _format_event(e) -> str:
    """Render a TicketEvent's before/after JSON payload as a human sentence.
    The raw dicts make sense to a developer but read like a bug to anyone else."""
    k = e.kind
    a = e.after_value or {}
    b = e.before_value or {}
    if k == TicketEventKind.created:
        parts = [f"Created as <b>{a.get('kind','ticket').replace('_',' ')}</b>"]
        if a.get("category"):
            parts.append(f"in <b>{a['category']}</b>")
        if a.get("priority"):
            parts.append(f"priority <b>{a['priority']}</b>")
        if a.get("auto_assigned_to"):
            parts.append(f"· auto-assigned to <b>{a['auto_assigned_to']}</b>")
        return ", ".join(parts[:-1]) + (" " + parts[-1] if len(parts) > 1 else parts[0])
    if k == TicketEventKind.status_changed:
        return f"Status: <b>{b.get('status','—').replace('_',' ')}</b> → <b>{a.get('status','—').replace('_',' ')}</b>"
    if k == TicketEventKind.priority_changed:
        return f"Priority: <b>{b.get('priority','—')}</b> → <b>{a.get('priority','—')}</b>"
    if k == TicketEventKind.category_changed:
        return f"Category: <b>{b.get('category','—')}</b> → <b>{a.get('category','—')}</b>"
    if k == TicketEventKind.assigned:
        who = a.get("assignee_email") or a.get("assignee_name") or "someone"
        if b.get("assignee_id"):
            return f"Reassigned to <b>{who}</b>"
        return f"Assigned to <b>{who}</b>"
    if k == TicketEventKind.unassigned:
        return "Unassigned"
    if k == TicketEventKind.comment_added:
        if a.get("is_internal"):
            return f"Posted an internal note: <i>{(a.get('body_preview','') or '').strip()[:80]}…</i>"
        return f"Replied: <i>{(a.get('body_preview','') or '').strip()[:80]}…</i>"
    if k == TicketEventKind.title_changed:
        return f"Title: “{b.get('title','')[:40]}” → “{a.get('title','')[:40]}”"
    if k == TicketEventKind.description_changed:
        return "Edited description"
    return k.value.replace("_", " ")


@router.get("/tickets", response_class=HTMLResponse)
def list_tickets(
    request: Request,
    status: str | None = None,
    kind: str | None = None,
    mine: int = 0,
    location: str | None = None,
    q: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    from sqlalchemy import func as _f, or_
    qy = db.query(Ticket).filter(Ticket.tenant_id == user.tenant_id)
    needle = (q or "").strip()
    if needle:
        like = f"%{needle.lower()}%"
        qy = qy.filter(or_(
            _f.lower(Ticket.title).like(like),
            _f.lower(Ticket.description).like(like),
            _f.lower(Ticket.ticket_number).like(like),
        ))
    if status:
        try:
            qy = qy.filter(Ticket.status == TicketStatus(status))
        except ValueError:
            pass
    if kind:
        try:
            qy = qy.filter(Ticket.kind == TicketKind(kind))
        except ValueError:
            pass
    if mine:
        qy = qy.filter(Ticket.assignee_id == user.id)
    if location:
        qy = qy.filter(_f.lower(Ticket.location) == location.strip().lower())
    rows = qy.order_by(Ticket.created_at.desc()).limit(200).all()
    # Distinct locations available for the filter dropdown
    from sqlalchemy import distinct
    locations = sorted({
        loc for (loc,) in db.query(distinct(Ticket.location))
                            .filter(Ticket.tenant_id == user.tenant_id,
                                    Ticket.location.is_not(None)).all()
        if loc
    })
    # KPI counts — total scope of the tenant, not the current filter
    status_counts = dict(
        db.query(Ticket.status, _f.count(Ticket.id))
          .filter(Ticket.tenant_id == user.tenant_id)
          .group_by(Ticket.status).all()
    )
    kpi = {s.value: int(status_counts.get(s, 0)) for s in TicketStatus}
    kpi["total"] = sum(kpi.values())
    kpi["live"] = kpi.get("open", 0) + kpi.get("in_progress", 0) + kpi.get("on_hold", 0)
    return templates.TemplateResponse(
        request=request,
        name="tickets_list.html",
        context=_ctx(user, db,
                     rows=rows,
                     q=needle,
                     kpi=kpi,
                     filter_status=status or "",
                     filter_kind=kind or "",
                     filter_mine=bool(mine),
                     filter_location=location or "",
                     locations=locations,
                     statuses=[s.value for s in TicketStatus],
                     kinds=[k.value for k in TicketKind]),
    )


@router.get("/tickets/new", response_class=HTMLResponse)
def new_ticket_form(
    request: Request,
    kind: str = "incident",
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    try:
        ticket_kind = TicketKind(kind)
    except ValueError:
        ticket_kind = TicketKind.incident
    cats = (db.query(Category)
              .filter(Category.tenant_id == user.tenant_id, Category.kind == ticket_kind, Category.is_active == True)  # noqa: E712
              .order_by(Category.name).all())
    
    # Query active users in the tenant
    users = db.query(User).filter(User.tenant_id == user.tenant_id, User.is_active == True).order_by(User.full_name, User.email).all()
    
    # Extract distinct sorted locations from the users list
    loc_set = set(u.location for u in users if u.location)
    locations = sorted(list(loc_set))
    
    return templates.TemplateResponse(
        request=request,
        name="ticket_new.html",
        context=_ctx(user, db,
                     ticket_kind=ticket_kind.value,
                     categories=cats,
                     priorities=[p.value for p in TicketPriority],
                     users=users,
                     locations=locations,
                     portal=False),
    )


@router.post("/tickets/new")
async def new_ticket_submit(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    category_id: str = Form(...),
    priority: str = Form(...),
    reporter_id: int = Form(...),
    location: str = Form(""),
    kind: str = Form("incident"),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    # Resolve category, creating "Others" on the fly if selected
    try:
        cat_id = int(category_id)
        cat = db.get(Category, cat_id)
    except ValueError:
        try:
            t_kind = TicketKind(kind)
        except ValueError:
            t_kind = TicketKind.incident
        cat = db.query(Category).filter(Category.tenant_id == user.tenant_id, Category.name == "Others", Category.kind == t_kind).first()
        if not cat:
            cat = Category(
                tenant_id=user.tenant_id,
                name="Others",
                kind=t_kind,
                default_priority=TicketPriority.medium,
                requires_approval=False,
                is_active=True
            )
            db.add(cat)
            db.commit()
            db.refresh(cat)

    if cat is None or cat.tenant_id != user.tenant_id:
        raise HTTPException(status_code=400, detail="Invalid category")
    try:
        prio = TicketPriority(priority)
    except ValueError:
        prio = cat.default_priority

    # Log ticket on behalf of the selected requester user
    reporter = db.get(User, reporter_id)
    if not reporter or reporter.tenant_id != user.tenant_id:
        reporter = user

    ticket = ticketing.create_ticket(
        db, tenant_id=user.tenant_id, reporter=reporter, category=cat,
        title=title, description=description, priority=prio, location=location
    )

    # Process and save any uploaded files (screenshots, diagnostic logs, etc.)
    from .views_portal import _attach_uploaded_files
    await _attach_uploaded_files(db, request=request, ticket=ticket, user=user, field_name="attachments")

    return RedirectResponse(url=f"/tickets/{ticket.id}", status_code=303)


@router.get("/tickets/{ticket_id}", response_class=HTMLResponse)
def ticket_detail(
    ticket_id: int,
    request: Request,
    attach_error: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    t = db.get(Ticket, ticket_id)
    if t is None or t.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Ticket not found")
    staff = (db.query(User)
               .filter(User.tenant_id == user.tenant_id,
                       User.is_active == True,  # noqa: E712
                       User.role.in_([UserRole.admin, UserRole.agent]))
               .order_by(User.full_name, User.email).all())

    from ..models import ReplyTemplate
    reply_templates = (
        db.query(ReplyTemplate)
        .filter(ReplyTemplate.tenant_id == user.tenant_id)
        .order_by(ReplyTemplate.sort_order.asc(), ReplyTemplate.title.asc())
        .all()
    )

    # Attachments — same model the portal uses, just rendered for staff
    from ..api.uploads import _safe_ext
    attachments = (db.query(TicketAttachment)
                     .filter(TicketAttachment.ticket_id == t.id)
                     .order_by(TicketAttachment.created_at).all())
    att_rows = []
    for a in attachments:
        f = a.file
        if f is None:
            continue
        att_rows.append({
            "id": a.id,
            "filename": f.original_filename,
            "size_bytes": f.size_bytes,
            "uploader": a.uploaded_by.display_name if a.uploaded_by else "—",
            "created_at": a.created_at,
            "url": f"/files/{f.id}{_safe_ext(f.original_filename)}",
        })

    # Phase H-style KB hints — top 3 portal-visible articles matching category name
    from ..services.kb import search as kb_search
    kb_hints = []
    if t.category and t.category.name:
        kb_hints = kb_search(db, tenant_id=user.tenant_id, q=t.category.name,
                             portal_only=False, limit=3)

    # Fetch latest snapshot and pending patches for the linked asset
    from ..models import AssetSnapshot, PatchObservation
    asset_snapshot = None
    pending_patches_count = 0
    if t.asset_id:
        asset_snapshot = (db.query(AssetSnapshot)
                            .filter(AssetSnapshot.agent_id == t.asset_id)
                            .order_by(AssetSnapshot.snapshot_at.desc()).first())
        pending_patches_count = (db.query(PatchObservation)
                                   .filter(PatchObservation.agent_id == t.asset_id, PatchObservation.resolved_at.is_(None))
                                   .count())

    return templates.TemplateResponse(
        request=request,
        name="ticket_detail.html",
        context=_ctx(user, db,
                     ticket=t,
                     staff=staff,
                     attachments=att_rows,
                     kb_hints=kb_hints,
                     asset_snapshot=asset_snapshot,
                     pending_patches_count=pending_patches_count,
                     attach_error=attach_error,
                     statuses=[s.value for s in TicketStatus],
                     priorities=[p.value for p in TicketPriority],
                     reply_templates=reply_templates,
                     portal=False),
    )


@router.post("/tickets/{ticket_id}/comment")
async def post_comment(
    ticket_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    t = db.get(Ticket, ticket_id)
    if t is None or t.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    form = await request.form()
    body = (form.get("body") or "").strip()
    is_internal = bool(form.get("is_internal"))
    if body:
        ticketing.add_comment(db, ticket=t, author=user, body=body, is_internal=is_internal)
    # Reuse the portal's attachment pipeline so both surfaces store identically
    from .views_portal import _attach_uploaded_files
    _, rejected = await _attach_uploaded_files(db, request=request, ticket=t, user=user, field_name="attachments")
    url = f"/tickets/{ticket_id}"
    if rejected:
        from urllib.parse import quote
        url += f"?attach_error={quote('Too large (max 2 MB): ' + ', '.join(rejected))}"
    return RedirectResponse(url=url, status_code=303)


@router.post("/tickets/{ticket_id}/status")
def change_status(
    ticket_id: int,
    new_status: str = Form(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    t = db.get(Ticket, ticket_id)
    if t is None or t.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    try:
        ns = TicketStatus(new_status)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid status")
    ticketing.transition_status(db, ticket=t, actor=user, new_status=ns)
    return RedirectResponse(url=f"/tickets/{ticket_id}", status_code=303)


@router.post("/tickets/{ticket_id}/assign")
def assign(
    ticket_id: int,
    assignee_id: str = Form(""),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    t = db.get(Ticket, ticket_id)
    if t is None or t.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    target: User | None = None
    if assignee_id:
        target = db.get(User, int(assignee_id))
        if target is None or target.tenant_id != user.tenant_id:
            raise HTTPException(status_code=400, detail="Invalid assignee")
    ticketing.assign(db, ticket=t, actor=user, assignee=target)
    return RedirectResponse(url=f"/tickets/{ticket_id}", status_code=303)


@router.post("/tickets/{ticket_id}/priority")
def change_priority(
    ticket_id: int,
    new_priority: str = Form(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    t = db.get(Ticket, ticket_id)
    if t is None or t.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    try:
        np = TicketPriority(new_priority)
    except ValueError:
        raise HTTPException(status_code=400)
    ticketing.update_priority(db, ticket=t, actor=user, new_priority=np)
    return RedirectResponse(url=f"/tickets/{ticket_id}", status_code=303)


@router.get("/api/v1/kb/{article_id}")
def get_kb_article_json(
    article_id: int,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    from ..models import KbArticle
    article = db.get(KbArticle, article_id)
    if not article or article.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Article not found")
    return {
        "id": article.id,
        "title": article.title,
        "summary": article.summary,
        "body": article.body,
    }


@router.post("/tickets/{ticket_id}/delete")
def delete_ticket(
    ticket_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    t = db.get(Ticket, ticket_id)
    if t is None or t.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    db.delete(t)
    db.commit()
    return RedirectResponse(url="/tickets", status_code=303)

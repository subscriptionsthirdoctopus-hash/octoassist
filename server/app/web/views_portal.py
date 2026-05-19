"""End-user portal — log a ticket, see my tickets, comment + attach files."""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from ..jinja_filters import install_on
from sqlalchemy.orm import Session

from ..auth import current_user
from ..database import get_db
from ..models import (
    Category, Tenant, Ticket, TicketAttachment, TicketKind, TicketPriority,
    UploadedFile, User,
)
from ..services import ticketing
from ..services.sla import time_to_breach

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
install_on(templates)

router = APIRouter(tags=["portal"])

# Friendly icon per category name (case-insensitive substring match). Falls back
# to a kind-level default. Kept here so adding a new category in Settings → SLA
# doesn't break the portal — it just gets the default icon.
_CATEGORY_ICONS = {
    "network":     "🌐",
    "wifi":        "📶",
    "vpn":         "🔐",
    "email":       "✉️",
    "outlook":     "✉️",
    "printer":     "🖨️",
    "laptop":      "💻",
    "hardware":    "🔧",
    "software":    "📦",
    "install":     "📦",
    "password":    "🔑",
    "account":     "👤",
    "access":      "🚪",
    "phone":       "📞",
    "teams":       "💬",
    "security":    "🛡",
    "data":        "🗂",
    "report":      "📊",
}
_KIND_DEFAULT_ICON = {
    TicketKind.incident:        "⚠️",
    TicketKind.service_request: "✨",
}


def _icon_for(cat: Category) -> str:
    name = (cat.name or "").lower()
    for key, icon in _CATEGORY_ICONS.items():
        if key in name:
            return icon
    return _KIND_DEFAULT_ICON.get(cat.kind, "🎫")


def _status_steps(ticket: Ticket) -> list[dict]:
    """Visual timeline cells. Each step has label + state (done/current/pending)."""
    from ..models import TicketStatus
    order = [TicketStatus.open, TicketStatus.in_progress, TicketStatus.resolved, TicketStatus.closed]
    cur = ticket.status
    if cur == TicketStatus.cancelled:
        return [{"label": "Cancelled", "state": "cancelled"}]
    if cur == TicketStatus.on_hold:
        # Pull on_hold inline between in_progress and resolved
        order = [TicketStatus.open, TicketStatus.in_progress, TicketStatus.on_hold,
                 TicketStatus.resolved, TicketStatus.closed]
    cur_idx = order.index(cur) if cur in order else 0
    steps = []
    for i, s in enumerate(order):
        state = "done" if i < cur_idx else ("current" if i == cur_idx else "pending")
        steps.append({"label": s.value.replace("_", " ").title(), "state": state})
    return steps


def _ctx(user: User, db: Session, **extra) -> dict:
    tenant = db.query(Tenant).first()
    return {"current_user": user, "tenant": tenant,
            "time_to_breach": time_to_breach,
            "icon_for": _icon_for,
            "status_steps": _status_steps,
            **extra}


# ------- internal: persist any uploaded files + link to a ticket -------

async def _attach_uploaded_files(
    db: Session, *, request: Request, ticket: Ticket, user: User, field_name: str,
) -> int:
    """Read multipart files from `field_name`, save via the UploadedFile pipeline,
    create TicketAttachment rows. Returns count saved. Silent on file failures
    (the comment/ticket creation still succeeds)."""
    from ..api.uploads import UPLOAD_DIR, MAX_BYTES, _safe_ext
    import hashlib as _hl, uuid as _uuid
    form = await request.form()
    files = form.getlist(field_name) if hasattr(form, "getlist") else []
    saved = 0
    for f in files:
        if not hasattr(f, "filename") or not f.filename or not getattr(f, "size", 0):
            continue
        file_id = _uuid.uuid4().hex
        ext = _safe_ext(f.filename)
        target = UPLOAD_DIR / f"{file_id}{ext}"
        h = _hl.sha256(); written = 0
        try:
            with open(target, "wb") as out:
                while True:
                    chunk = await f.read(1024 * 256)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_BYTES:
                        out.close(); target.unlink(missing_ok=True)
                        break
                    h.update(chunk); out.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            continue
        db.add(UploadedFile(
            id=file_id, tenant_id=user.tenant_id,
            original_filename=f.filename[:255],
            content_type=(f.content_type or "application/octet-stream")[:120],
            size_bytes=written, sha256=h.hexdigest(),
            purpose="ticket-attachment", created_by_id=user.id,
        ))
        db.add(TicketAttachment(ticket_id=ticket.id, file_id=file_id, uploaded_by_id=user.id))
        saved += 1
    if saved:
        db.commit()
    return saved


# ============================ Routes ============================

@router.get("/portal", response_class=HTMLResponse)
def my_tickets(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    q: str = "",
    status: str = "",
):
    from sqlalchemy import func, or_
    from ..models import TicketStatus
    query = (db.query(Ticket)
               .filter(Ticket.tenant_id == user.tenant_id,
                       Ticket.reporter_id == user.id))
    needle = (q or "").strip()
    if needle:
        like = f"%{needle.lower()}%"
        query = query.filter(or_(
            func.lower(Ticket.title).like(like),
            func.lower(Ticket.description).like(like),
            func.lower(Ticket.ticket_number).like(like),
        ))
    if status:
        try:
            query = query.filter(Ticket.status == TicketStatus(status))
        except ValueError:
            pass
    rows = query.order_by(Ticket.created_at.desc()).limit(100).all()
    open_count = sum(1 for t in rows if t.status.value not in ("resolved", "closed", "cancelled"))
    return templates.TemplateResponse(
        request=request, name="portal_dashboard.html",
        context=_ctx(user, db, rows=rows, q=needle, filter_status=status,
                     open_count=open_count,
                     statuses=[s.value for s in __import__('app.models', fromlist=['TicketStatus']).TicketStatus]),
    )


@router.get("/portal/new", response_class=HTMLResponse)
def new_ticket_form(
    request: Request,
    kind: str = "incident",
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    try:
        ticket_kind = TicketKind(kind)
    except ValueError:
        ticket_kind = TicketKind.incident
    cats = (db.query(Category)
              .filter(Category.tenant_id == user.tenant_id,
                      Category.kind == ticket_kind,
                      Category.is_active == True)  # noqa: E712
              .order_by(Category.name).all())
    return templates.TemplateResponse(
        request=request, name="portal_ticket_new.html",
        context=_ctx(user, db,
                     ticket_kind=ticket_kind.value,
                     categories=cats),
    )


@router.post("/portal/new")
async def new_ticket_submit(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    form = await request.form()
    title = (form.get("title") or "").strip()
    description = (form.get("description") or "").strip()
    try:
        category_id = int(form.get("category_id") or 0)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid category")
    cat = db.get(Category, category_id)
    if cat is None or cat.tenant_id != user.tenant_id:
        raise HTTPException(status_code=400, detail="Invalid category")
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")
    ticket = ticketing.create_ticket(
        db, tenant_id=user.tenant_id, reporter=user, category=cat,
        title=title, description=description,
    )
    # Attach any uploaded files
    await _attach_uploaded_files(db, request=request, ticket=ticket, user=user, field_name="attachments")
    return RedirectResponse(url=f"/portal/ticket/{ticket.id}", status_code=303)


@router.get("/portal/ticket/{ticket_id}", response_class=HTMLResponse)
def view_ticket(
    ticket_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    t = db.get(Ticket, ticket_id)
    if t is None or t.tenant_id != user.tenant_id or t.reporter_id != user.id:
        raise HTTPException(status_code=404)
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
            "content_type": f.content_type,
            "uploader": a.uploaded_by.display_name if a.uploaded_by else "—",
            "created_at": a.created_at,
            "url": f"/files/{f.id}{_safe_ext(f.original_filename)}",
        })
    return templates.TemplateResponse(
        request=request, name="portal_ticket_detail.html",
        context=_ctx(user, db, ticket=t, attachments=att_rows),
    )


@router.post("/portal/ticket/{ticket_id}/comment")
async def post_comment(
    ticket_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    t = db.get(Ticket, ticket_id)
    if t is None or t.tenant_id != user.tenant_id or t.reporter_id != user.id:
        raise HTTPException(status_code=404)
    form = await request.form()
    body = (form.get("body") or "").strip()
    if body:
        ticketing.add_comment(db, ticket=t, author=user, body=body, is_internal=False)
    await _attach_uploaded_files(db, request=request, ticket=t, user=user, field_name="attachments")
    return RedirectResponse(url=f"/portal/ticket/{ticket_id}", status_code=303)

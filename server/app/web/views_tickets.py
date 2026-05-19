"""Agent / admin ticket views: list, create, detail, comment, transition."""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from ..jinja_filters import install_on
from sqlalchemy.orm import Session

from ..auth import current_user, require_staff
from ..database import get_db
from ..models import (
    Category, Tenant, Ticket, TicketKind, TicketPriority, TicketStatus, User, UserRole,
)
from ..services import ticketing
from ..services.sla import time_to_breach

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
install_on(templates)

router = APIRouter(tags=["tickets"])


def _ctx(user: User, db: Session, **extra) -> dict:
    tenant = db.query(Tenant).first()
    return {"current_user": user, "tenant": tenant, "time_to_breach": time_to_breach, **extra}


@router.get("/tickets", response_class=HTMLResponse)
def list_tickets(
    request: Request,
    status: str | None = None,
    kind: str | None = None,
    mine: int = 0,
    location: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    q = db.query(Ticket).filter(Ticket.tenant_id == user.tenant_id)
    if status:
        try:
            q = q.filter(Ticket.status == TicketStatus(status))
        except ValueError:
            pass
    if kind:
        try:
            q = q.filter(Ticket.kind == TicketKind(kind))
        except ValueError:
            pass
    if mine:
        q = q.filter(Ticket.assignee_id == user.id)
    if location:
        from sqlalchemy import func as _f
        q = q.filter(_f.lower(Ticket.location) == location.strip().lower())
    rows = q.order_by(Ticket.created_at.desc()).limit(200).all()
    # Distinct locations available for the filter dropdown
    from sqlalchemy import distinct
    locations = sorted({
        loc for (loc,) in db.query(distinct(Ticket.location))
                            .filter(Ticket.tenant_id == user.tenant_id,
                                    Ticket.location.is_not(None)).all()
        if loc
    })
    return templates.TemplateResponse(
        request=request,
        name="tickets_list.html",
        context=_ctx(user, db,
                     rows=rows,
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
    return templates.TemplateResponse(
        request=request,
        name="ticket_new.html",
        context=_ctx(user, db,
                     ticket_kind=ticket_kind.value,
                     categories=cats,
                     priorities=[p.value for p in TicketPriority],
                     portal=False),
    )


@router.post("/tickets/new")
def new_ticket_submit(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    category_id: int = Form(...),
    priority: str = Form(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    cat = db.get(Category, category_id)
    if cat is None or cat.tenant_id != user.tenant_id:
        raise HTTPException(status_code=400, detail="Invalid category")
    try:
        prio = TicketPriority(priority)
    except ValueError:
        prio = cat.default_priority
    ticket = ticketing.create_ticket(
        db, tenant_id=user.tenant_id, reporter=user, category=cat,
        title=title, description=description, priority=prio,
    )
    return RedirectResponse(url=f"/tickets/{ticket.id}", status_code=303)


@router.get("/tickets/{ticket_id}", response_class=HTMLResponse)
def ticket_detail(
    ticket_id: int,
    request: Request,
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
    return templates.TemplateResponse(
        request=request,
        name="ticket_detail.html",
        context=_ctx(user, db,
                     ticket=t,
                     staff=staff,
                     statuses=[s.value for s in TicketStatus],
                     priorities=[p.value for p in TicketPriority],
                     portal=False),
    )


@router.post("/tickets/{ticket_id}/comment")
def post_comment(
    ticket_id: int,
    body: str = Form(...),
    is_internal: int = Form(0),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    t = db.get(Ticket, ticket_id)
    if t is None or t.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    if body.strip():
        ticketing.add_comment(db, ticket=t, author=user, body=body, is_internal=bool(is_internal))
    return RedirectResponse(url=f"/tickets/{ticket_id}", status_code=303)


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

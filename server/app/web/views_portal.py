"""End-user portal — log a ticket, see my tickets, comment on my own."""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from ..jinja_filters import install_on
from sqlalchemy.orm import Session

from ..auth import current_user
from ..database import get_db
from ..models import Category, Tenant, Ticket, TicketKind, TicketPriority, User
from ..services import ticketing
from ..services.sla import time_to_breach

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
install_on(templates)

router = APIRouter(tags=["portal"])


def _ctx(user: User, db: Session, **extra) -> dict:
    tenant = db.query(Tenant).first()
    return {"current_user": user, "tenant": tenant, "time_to_breach": time_to_breach, **extra}


@router.get("/portal", response_class=HTMLResponse)
def my_tickets(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rows = (db.query(Ticket)
              .filter(Ticket.tenant_id == user.tenant_id, Ticket.reporter_id == user.id)
              .order_by(Ticket.created_at.desc()).limit(100).all())
    return templates.TemplateResponse(
        request=request,
        name="portal_dashboard.html",
        context=_ctx(user, db, rows=rows),
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
        request=request,
        name="portal_ticket_new.html",
        context=_ctx(user, db,
                     ticket_kind=ticket_kind.value,
                     categories=cats),
    )


@router.post("/portal/new")
def new_ticket_submit(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    category_id: int = Form(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    cat = db.get(Category, category_id)
    if cat is None or cat.tenant_id != user.tenant_id:
        raise HTTPException(status_code=400, detail="Invalid category")
    ticket = ticketing.create_ticket(
        db, tenant_id=user.tenant_id, reporter=user, category=cat,
        title=title, description=description,
    )
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
    return templates.TemplateResponse(
        request=request,
        name="portal_ticket_detail.html",
        context=_ctx(user, db, ticket=t),
    )


@router.post("/portal/ticket/{ticket_id}/comment")
def post_comment(
    ticket_id: int,
    body: str = Form(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    t = db.get(Ticket, ticket_id)
    if t is None or t.tenant_id != user.tenant_id or t.reporter_id != user.id:
        raise HTTPException(status_code=404)
    if body.strip():
        ticketing.add_comment(db, ticket=t, author=user, body=body, is_internal=False)
    return RedirectResponse(url=f"/portal/ticket/{ticket_id}", status_code=303)

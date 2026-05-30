"""Problem Management views — staff only."""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from ..jinja_filters import install_on
from sqlalchemy.orm import Session

from ..auth import require_staff, require_admin
from ..database import get_db
from ..models import (
    Problem, ProblemStatus, ProblemTicketLink, Tenant, Ticket, TicketPriority,
    User, UserRole,
)
from ..services import problem as problem_svc

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
install_on(templates)

router = APIRouter(tags=["problems"])


def _ctx(user: User, db: Session, **extra) -> dict:
    return {"current_user": user, "tenant": db.query(Tenant).first(), **extra}


@router.get("/problems", response_class=HTMLResponse)
def list_problems(
    request: Request,
    status: str | None = None,
    q: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    from sqlalchemy import func as _f, or_
    qy = db.query(Problem).filter(Problem.tenant_id == user.tenant_id)
    needle = (q or "").strip()
    if needle:
        like = f"%{needle.lower()}%"
        qy = qy.filter(or_(
            _f.lower(Problem.title).like(like),
            _f.lower(Problem.description).like(like),
            _f.lower(Problem.problem_number).like(like),
        ))
    if status:
        try:
            qy = qy.filter(Problem.status == ProblemStatus(status))
        except ValueError:
            pass
    rows = qy.order_by(Problem.created_at.desc()).limit(200).all()
    status_counts = dict(
        db.query(Problem.status, _f.count(Problem.id))
          .filter(Problem.tenant_id == user.tenant_id)
          .group_by(Problem.status).all()
    )
    kpi = {s.value: int(status_counts.get(s, 0)) for s in ProblemStatus}
    kpi["total"] = sum(kpi.values())
    return templates.TemplateResponse(
        request=request, name="problems_list.html",
        context=_ctx(user, db, rows=rows, kpi=kpi, q=needle,
                     statuses=[s.value for s in ProblemStatus],
                     filter_status=status or ""),
    )


@router.get("/problems/new", response_class=HTMLResponse)
def new_problem_form(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        request=request, name="problem_new.html",
        context=_ctx(user, db, priorities=[p.value for p in TicketPriority]),
    )


@router.post("/problems/new")
def new_problem_submit(
    title: str = Form(...),
    description: str = Form(""),
    priority: str = Form("medium"),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    try:
        prio = TicketPriority(priority)
    except ValueError:
        prio = TicketPriority.medium
    p = problem_svc.create_problem(
        db, tenant_id=user.tenant_id, reporter=user,
        title=title, description=description, priority=prio,
    )
    return RedirectResponse(url=f"/problems/{p.id}", status_code=303)


@router.get("/problems/{problem_id}", response_class=HTMLResponse)
def problem_detail(
    problem_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    p = db.get(Problem, problem_id)
    if p is None or p.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    open_tickets = (db.query(Ticket)
                      .filter(Ticket.tenant_id == user.tenant_id)
                      .order_by(Ticket.created_at.desc()).limit(50).all())
    staff = (db.query(User)
               .filter(User.tenant_id == user.tenant_id, User.is_active == True,  # noqa: E712
                       User.role.in_([UserRole.admin, UserRole.agent]))
               .order_by(User.full_name, User.email).all())
    return templates.TemplateResponse(
        request=request, name="problem_detail.html",
        context=_ctx(user, db, problem=p, open_tickets=open_tickets, staff=staff,
                     statuses=[s.value for s in ProblemStatus],
                     priorities=[p.value for p in TicketPriority]),
    )


@router.post("/problems/{problem_id}/edit")
def problem_edit(
    problem_id: int,
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    root_cause: str = Form(""),
    workaround: str = Form(""),
    priority: str = Form("medium"),
    assignee_id: str = Form(""),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    p = db.get(Problem, problem_id)
    if p is None or p.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    p.title = title.strip()[:255]
    p.description = description.strip()
    p.root_cause = root_cause.strip()
    p.workaround = workaround.strip()
    try:
        p.priority = TicketPriority(priority)
    except ValueError:
        pass
    p.assignee_id = int(assignee_id) if assignee_id.strip() else None
    db.commit()

    # Audit log
    from ..services.audit import log_admin_action
    try:
        log_admin_action(db, tenant_id=p.tenant_id, user=user, action="edit_problem",
                         details=f"Edited Problem {p.problem_number}. Priority: {p.priority.value}. Assignee ID: {p.assignee_id}",
                         ip_address=request.client.host if request.client else None)
    except Exception:
        pass

    return RedirectResponse(url=f"/problems/{problem_id}", status_code=303)


@router.post("/problems/{problem_id}/status")
def problem_status(
    problem_id: int,
    request: Request,
    new_status: str = Form(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    p = db.get(Problem, problem_id)
    if p is None or p.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    try:
        ns = ProblemStatus(new_status)
    except ValueError:
        raise HTTPException(status_code=400)
    old = p.status.value
    problem_svc.transition_status(db, problem=p, new_status=ns)

    # Audit log
    from ..services.audit import log_admin_action
    try:
        log_admin_action(db, tenant_id=p.tenant_id, user=user, action="transition_problem_status",
                         details=f"Transitioned Problem {p.problem_number} status: {old} -> {ns.value}",
                         ip_address=request.client.host if request.client else None)
    except Exception:
        pass

    return RedirectResponse(url=f"/problems/{problem_id}", status_code=303)


@router.post("/problems/{problem_id}/link")
def problem_link_ticket(
    problem_id: int,
    request: Request,
    ticket_id: int = Form(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    p = db.get(Problem, problem_id)
    t = db.get(Ticket, ticket_id)
    if p is None or t is None or p.tenant_id != user.tenant_id or t.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    problem_svc.link_ticket(db, problem=p, ticket=t, by=user)

    # Audit log
    from ..services.audit import log_admin_action
    try:
        log_admin_action(db, tenant_id=p.tenant_id, user=user, action="link_problem_ticket",
                         details=f"Linked Ticket {t.ticket_number} to Problem {p.problem_number}",
                         ip_address=request.client.host if request.client else None)
    except Exception:
        pass

    return RedirectResponse(url=f"/problems/{problem_id}", status_code=303)


@router.post("/problems/{problem_id}/unlink/{ticket_id}")
def problem_unlink(
    problem_id: int,
    ticket_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    p = db.get(Problem, problem_id)
    if p is None or p.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    problem_svc.unlink_ticket(db, problem_id=problem_id, ticket_id=ticket_id)

    # Audit log
    from ..services.audit import log_admin_action
    try:
        log_admin_action(db, tenant_id=p.tenant_id, user=user, action="unlink_problem_ticket",
                         details=f"Unlinked Ticket ID: {ticket_id} from Problem {p.problem_number}",
                         ip_address=request.client.host if request.client else None)
    except Exception:
        pass

    return RedirectResponse(url=f"/problems/{problem_id}", status_code=303)


@router.post("/problems/{problem_id}/delete")
def delete_problem(
    problem_id: int,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    p = db.get(Problem, problem_id)
    if p is None or p.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    p_num = p.problem_number
    p_title = p.title
    db.delete(p)
    db.commit()

    # Audit log
    from ..services.audit import log_admin_action
    try:
        log_admin_action(db, tenant_id=user.tenant_id, user=user, action="delete_problem",
                         details=f"Permanently deleted Problem {p_num} ('{p_title}')",
                         ip_address=request.client.host if request.client else None)
    except Exception:
        pass

    return RedirectResponse(url="/problems", status_code=303)

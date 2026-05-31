"""Expenses — out-of-pocket reimbursement workflow.

/expenses                    list + add
/expenses/{id}               read-only detail (anyone with can_view)
/expenses/{id}/edit          edit form (owner, draft/rejected)
/expenses/{id}/edit          POST  save
/expenses/{id}/submit        POST  draft → submitted
/expenses/{id}/recall        POST  submitted → draft
/expenses/{id}/delete        POST  delete
/expenses/{id}/receipt       POST  upload (multipart/form-data)
/expenses/{id}/receipt/file  GET   download (auth-checked)

Approvals (separate from timesheet approvals):
/expenses/approvals          inbox
/expenses/approvals/{id}     review
/expenses/{id}/approve       POST
/expenses/{id}/reject        POST (reason required)
/expenses/{id}/reimburse     POST (admin only)
"""
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import require_user
from ..database import get_db
from ..models import Engagement, Expense, ExpenseCategory, ExpenseStatus, User, UserRole
from ..services import expenses as exp_svc

router = APIRouter(tags=["expenses"])
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


# ─── Helpers ─────────────────────────────────────────────────────────

def _categories(db: Session, tenant_id: int) -> list[ExpenseCategory]:
    return (db.query(ExpenseCategory)
              .filter(ExpenseCategory.tenant_id == tenant_id,
                      ExpenseCategory.is_active.is_(True))
              .order_by(ExpenseCategory.name).all())


def _engagements(db: Session, viewer: User) -> list[Engagement]:
    """Engagements the user may bill an expense to. Mirrors the timesheet
    rule: admins see everything; others see only their assignments."""
    q = db.query(Engagement).filter(Engagement.tenant_id == viewer.tenant_id)
    if viewer.role != UserRole.admin:
        q = q.join(Engagement.assignments).filter_by(user_id=viewer.id)
    return q.order_by(Engagement.name).all()


# ─── /expenses (my expenses) ─────────────────────────────────────────

@router.get("/expenses", response_class=HTMLResponse)
def expenses_list(request: Request, flash: str | None = None,
                  start: str | None = None, end: str | None = None,
                  status: str | None = None, category_id: str | None = None,
                  user: User = Depends(require_user), db: Session = Depends(get_db)):
    # Parse filters
    sd = ed = None
    if start:
        try: sd = date.fromisoformat(start)
        except ValueError: pass
    if end:
        try: ed = date.fromisoformat(end)
        except ValueError: pass
    status_enum = None
    if status:
        try: status_enum = ExpenseStatus(status)
        except ValueError: pass
    cat_id = int(category_id) if category_id and category_id.isdigit() else None

    q = db.query(Expense).filter(Expense.user_id == user.id)
    if sd:           q = q.filter(Expense.expense_date >= sd)
    if ed:           q = q.filter(Expense.expense_date <= ed)
    if status_enum:  q = q.filter(Expense.status == status_enum)
    if cat_id:       q = q.filter(Expense.category_id == cat_id)
    mine = q.order_by(Expense.status, Expense.expense_date.desc(),
                      Expense.created_at.desc()).all()

    by_status = {"draft": 0, "submitted": 0, "approved": 0, "rejected": 0, "reimbursed": 0}
    for e in mine:
        by_status[e.status.value] = by_status.get(e.status.value, 0) + 1

    return templates.TemplateResponse(
        request=request, name="expenses_list.html",
        context={
            "current_user": user, "rows": mine, "by_status": by_status,
            "categories":  _categories(db, user.tenant_id),
            "engagements": _engagements(db, user),
            "today": date.today(), "flash": flash,
            "filter": {
                "start": sd, "end": ed, "status": status, "category_id": cat_id,
                "any":   bool(sd or ed or status or cat_id),
            },
            "expense_statuses": [s.value for s in ExpenseStatus],
        },
    )


@router.post("/expenses/new")
def expenses_new(
    expense_date: str = Form(...),
    amount: str = Form(...),
    currency: str = Form("INR"),
    category_id: str = Form(""),
    engagement_id: str = Form(""),
    is_billable: str = Form(""),
    description: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    try:
        d = date.fromisoformat(expense_date)
    except ValueError:
        return RedirectResponse(url=f"/expenses?flash={quote('Invalid date')}", status_code=303)
    try:
        amt = float(amount)
        if amt <= 0: raise ValueError
    except ValueError:
        return RedirectResponse(url=f"/expenses?flash={quote('Amount must be a positive number')}", status_code=303)
    cat_id = int(category_id) if category_id and category_id.isdigit() else None
    eng_id = int(engagement_id) if engagement_id and engagement_id.isdigit() else None

    exp = Expense(
        tenant_id    = user.tenant_id, user_id = user.id,
        expense_date = d, amount = amt, currency = currency.strip().upper()[:3] or "INR",
        category_id  = cat_id, engagement_id = eng_id,
        description  = description.strip(),
        is_billable  = bool(is_billable),
        status       = ExpenseStatus.draft,
    )
    db.add(exp); db.flush()
    exp_svc._log(db, user, "expense.create", exp)
    db.commit()
    return RedirectResponse(url=f"/expenses/{exp.id}?flash={quote('Saved as draft — attach a receipt next')}", status_code=303)


# ─── Approvals queue (declared BEFORE /expenses/{eid} to avoid path
#     collision — FastAPI matches routes in declaration order and
#     "approvals" would otherwise be parsed as an int eid and 422.) ────

@router.get("/expenses/approvals", response_class=HTMLResponse)
def expenses_approvals(request: Request, flash: str | None = None,
                       user: User = Depends(require_user), db: Session = Depends(get_db)):
    pending     = exp_svc.pending_for_approver(db, user)
    to_reimb    = exp_svc.awaiting_reimbursement(db, user) if user.role == UserRole.admin else []
    return templates.TemplateResponse(
        request=request, name="expenses_approvals.html",
        context={"current_user": user, "pending": pending,
                 "to_reimb": to_reimb, "flash": flash},
    )


# ─── /expenses/{id} (detail) ─────────────────────────────────────────

@router.get("/expenses/{eid}", response_class=HTMLResponse)
def expenses_detail(request: Request, eid: int, flash: str | None = None,
                    user: User = Depends(require_user), db: Session = Depends(get_db)):
    exp = db.get(Expense, eid)
    if not exp or not exp_svc.can_view(user, exp):
        raise HTTPException(404)
    return templates.TemplateResponse(
        request=request, name="expense_detail.html",
        context={
            "current_user": user, "exp": exp,
            "categories":  _categories(db, user.tenant_id),
            "engagements": _engagements(db, user),
            "can_edit":           exp_svc.can_edit(user, exp),
            "can_submit":         exp_svc.can_submit(user, exp),
            "can_recall":         exp_svc.can_recall(user, exp),
            "can_delete":         exp_svc.can_delete(user, exp),
            "can_approve":        exp_svc.can_approve(user, exp) and exp.status == ExpenseStatus.submitted,
            "can_mark_reimbursed":exp_svc.can_mark_reimbursed(user, exp) and exp.status == ExpenseStatus.approved,
            "flash": flash,
        },
    )


@router.post("/expenses/{eid}/edit")
def expenses_edit(
    eid: int,
    expense_date: str = Form(...),
    amount: str = Form(...),
    currency: str = Form("INR"),
    category_id: str = Form(""),
    engagement_id: str = Form(""),
    is_billable: str = Form(""),
    description: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    exp = db.get(Expense, eid)
    if not exp or not exp_svc.can_edit(user, exp):
        raise HTTPException(403)
    try:
        exp.expense_date = date.fromisoformat(expense_date)
        exp.amount       = float(amount)
    except ValueError:
        return RedirectResponse(url=f"/expenses/{eid}?flash={quote('Invalid date or amount')}", status_code=303)
    exp.currency      = currency.strip().upper()[:3] or "INR"
    exp.category_id   = int(category_id) if category_id and category_id.isdigit() else None
    exp.engagement_id = int(engagement_id) if engagement_id and engagement_id.isdigit() else None
    exp.is_billable   = bool(is_billable)
    exp.description   = description.strip()
    exp_svc._log(db, user, "expense.update", exp)
    db.commit()
    return RedirectResponse(url=f"/expenses/{eid}?flash={quote('Saved')}", status_code=303)


@router.post("/expenses/{eid}/submit")
def expenses_submit(eid: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    exp = db.get(Expense, eid)
    if not exp or not exp_svc.can_submit(user, exp):
        raise HTTPException(403)
    if not exp.receipt_path:
        return RedirectResponse(url=f"/expenses/{eid}?flash={quote('Please attach a receipt before submitting')}", status_code=303)
    exp_svc.submit(db, exp, user)
    return RedirectResponse(url=f"/expenses/{eid}?flash={quote('Submitted for approval')}", status_code=303)


@router.post("/expenses/{eid}/recall")
def expenses_recall(eid: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    exp = db.get(Expense, eid)
    if not exp or not exp_svc.can_recall(user, exp):
        raise HTTPException(403)
    exp_svc.recall(db, exp, user)
    return RedirectResponse(url=f"/expenses/{eid}?flash={quote('Recalled to draft')}", status_code=303)


@router.post("/expenses/{eid}/delete")
def expenses_delete(eid: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    exp = db.get(Expense, eid)
    if not exp or not exp_svc.can_delete(user, exp):
        raise HTTPException(403)
    # Remove receipt if present
    if exp.receipt_path:
        abs_path = exp_svc.receipt_absolute(exp)
        if abs_path:
            try: abs_path.unlink()
            except FileNotFoundError: pass
    exp_svc._log(db, user, "expense.delete", exp)
    db.delete(exp)
    db.commit()
    return RedirectResponse(url=f"/expenses?flash={quote('Expense deleted')}", status_code=303)


# ─── Receipt upload + download ───────────────────────────────────────

@router.post("/expenses/{eid}/receipt")
async def expenses_receipt_upload(
    eid: int,
    receipt: UploadFile = File(...),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    exp = db.get(Expense, eid)
    if not exp or not exp_svc.can_edit(user, exp):
        raise HTTPException(403)
    content = await receipt.read()
    try:
        exp_svc.save_receipt(exp, content, receipt.content_type or "", receipt.filename or "receipt")
    except ValueError as e:
        return RedirectResponse(url=f"/expenses/{eid}?flash={quote(str(e))}", status_code=303)
    exp_svc._log(db, user, "expense.receipt_upload", exp,
                 detail=f"{receipt.filename} · {len(content)} bytes")
    db.commit()
    return RedirectResponse(url=f"/expenses/{eid}?flash={quote('Receipt attached')}", status_code=303)


@router.get("/expenses/{eid}/receipt/file")
def expenses_receipt_download(eid: int,
                              user: User = Depends(require_user), db: Session = Depends(get_db)):
    exp = db.get(Expense, eid)
    if not exp or not exp_svc.can_view(user, exp):
        raise HTTPException(404)
    abs_path = exp_svc.receipt_absolute(exp)
    if not abs_path or not abs_path.exists():
        raise HTTPException(404, "No receipt attached")
    return FileResponse(
        path=str(abs_path),
        media_type=exp.receipt_mime or "application/octet-stream",
        filename=exp.receipt_filename or abs_path.name,
    )


# ─── Approval POST actions ─────────────────────────────────────────
# (The GET /expenses/approvals queue lives near the top of the file,
#  above /expenses/{eid}, to win the route match.)

@router.post("/expenses/{eid}/approve")
def expenses_approve(eid: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    exp = db.get(Expense, eid)
    if not exp or not exp_svc.can_approve(user, exp):
        raise HTTPException(403)
    if exp.status != ExpenseStatus.submitted:
        return RedirectResponse(url=f"/expenses/approvals?flash={quote('Expense is no longer pending')}", status_code=303)
    exp_svc.approve(db, exp, user)
    return RedirectResponse(
        url=f"/expenses/approvals?flash={quote(f'Approved · {exp.user.display_name} · {exp.currency} {exp.amount:.2f}')}",
        status_code=303,
    )


@router.post("/expenses/{eid}/reject")
def expenses_reject(eid: int, reason: str = Form(""),
                    user: User = Depends(require_user), db: Session = Depends(get_db)):
    exp = db.get(Expense, eid)
    if not exp or not exp_svc.can_approve(user, exp):
        raise HTTPException(403)
    reason = (reason or "").strip()
    if not reason:
        return RedirectResponse(url=f"/expenses/{eid}?flash={quote('A reason is required to reject')}", status_code=303)
    exp_svc.reject(db, exp, user, reason)
    return RedirectResponse(url=f"/expenses/approvals?flash={quote('Rejected — reason recorded')}", status_code=303)


@router.post("/expenses/{eid}/reimburse")
def expenses_reimburse(eid: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    exp = db.get(Expense, eid)
    if not exp or not exp_svc.can_mark_reimbursed(user, exp):
        raise HTTPException(403)
    if exp.status != ExpenseStatus.approved:
        return RedirectResponse(url=f"/expenses/approvals?flash={quote('Only approved expenses can be marked reimbursed')}", status_code=303)
    exp_svc.mark_reimbursed(db, exp, user)
    return RedirectResponse(
        url=f"/expenses/approvals?flash={quote(f'Marked reimbursed · {exp.user.display_name} · {exp.currency} {exp.amount:.2f}')}",
        status_code=303,
    )

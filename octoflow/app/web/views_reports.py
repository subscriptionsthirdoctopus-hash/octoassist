"""Reports — TS-132 through TS-138.

Every page renders an HTML table by default; passing ?format=csv on the
same URL streams the same data as text/csv (TS-138). All reports are
tenant-scoped; the viewer can never see another tenant's data.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import require_user
from ..database import get_db
from ..models import Tenant, User
from ..services import reports as rep

router = APIRouter(tags=["reports"])

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

VALID_DAYS = (7, 15, 30, 90, 180)


def _days(d: int | None) -> int:
    return d if d in VALID_DAYS else 30


def _csv_response(filename: str, rows: list[dict], columns: list[str]) -> Response:
    body = rep.csv_stream(rows, columns)
    return Response(
        content=body, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─────────────────────────────── Landing ─────────────────────────────

@router.get("/reports", response_class=HTMLResponse)
def reports_home(request: Request, user: User = Depends(require_user),
                 db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request=request, name="reports_home.html",
        context={"current_user": user},
    )


# ─────────────────────────────── TS-132 · my history ─────────────────

@router.get("/reports/my-history", response_class=HTMLResponse)
def my_history(request: Request, format: str = "html",
               user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = rep.my_history(db, user)
    cols = ["period_start","period_end","status","entries",
            "total_hours","billable_hours","submitted_at","approved_at","approver"]
    if format == "csv":
        return _csv_response(f"my_history_{user.id}.csv", rows, cols)
    return templates.TemplateResponse(
        request=request, name="report_my_history.html",
        context={"current_user": user, "rows": rows},
    )


# ─────────────────────────────── TS-133 · compliance ─────────────────

@router.get("/reports/compliance", response_class=HTMLResponse)
def compliance(request: Request, format: str = "html",
               user: User = Depends(require_user), db: Session = Depends(get_db)):
    period, rows = rep.compliance(db, user.tenant_id)
    cols = ["user","email","practice","manager","status","entries","hours"]
    if format == "csv":
        return _csv_response(
            f"submission_compliance_{period.start_date.isoformat() if period else 'na'}.csv",
            rows, cols,
        )
    counts = {"not_started": 0, "draft": 0, "submitted": 0, "approved": 0, "rejected": 0}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return templates.TemplateResponse(
        request=request, name="report_compliance.html",
        context={"current_user": user, "rows": rows, "period": period, "counts": counts},
    )


# ─────────────────────────────── TS-134 · hours by project ───────────

@router.get("/reports/hours-by-project", response_class=HTMLResponse)
def hours_by_project(request: Request, days: int = 30, format: str = "html",
                     user: User = Depends(require_user), db: Session = Depends(get_db)):
    d = _days(days)
    rows = rep.hours_by_project(db, user.tenant_id, d)
    cols = ["client","client_code","project","project_code",
            "total_hours","billable_hours","billable_pct"]
    if format == "csv":
        return _csv_response(f"hours_by_project_{d}d.csv", rows, cols)
    return templates.TemplateResponse(
        request=request, name="report_hours_by_project.html",
        context={"current_user": user, "rows": rows, "days": d,
                 "valid_days": VALID_DAYS,
                 "total_hours": sum(r["total_hours"] for r in rows)},
    )


# ─────────────────────────────── TS-135 · hours by client ────────────

@router.get("/reports/hours-by-client", response_class=HTMLResponse)
def hours_by_client(request: Request, days: int = 30, format: str = "html",
                    user: User = Depends(require_user), db: Session = Depends(get_db)):
    d = _days(days)
    rows = rep.hours_by_client(db, user.tenant_id, d)
    cols = ["client","client_code","engagements","total_hours","billable_hours","billable_pct"]
    if format == "csv":
        return _csv_response(f"hours_by_client_{d}d.csv", rows, cols)
    return templates.TemplateResponse(
        request=request, name="report_hours_by_client.html",
        context={"current_user": user, "rows": rows, "days": d,
                 "valid_days": VALID_DAYS,
                 "total_hours": sum(r["total_hours"] for r in rows)},
    )


# ─────────────────────────────── TS-136 · billable split ─────────────

@router.get("/reports/billable-split", response_class=HTMLResponse)
def billable_split(request: Request, days: int = 30, format: str = "html",
                   user: User = Depends(require_user), db: Session = Depends(get_db)):
    d = _days(days)
    rows = rep.billable_split(db, user.tenant_id, d)
    cols = ["user","email","practice","total_hours","billable_hours","non_billable","billable_pct"]
    if format == "csv":
        return _csv_response(f"billable_split_{d}d.csv", rows, cols)
    return templates.TemplateResponse(
        request=request, name="report_billable_split.html",
        context={"current_user": user, "rows": rows, "days": d,
                 "valid_days": VALID_DAYS},
    )


# ─────────────────────────────── TS-137 · utilisation ────────────────

@router.get("/reports/utilisation", response_class=HTMLResponse)
def utilisation(request: Request, days: int = 30, format: str = "html",
                user: User = Depends(require_user), db: Session = Depends(get_db)):
    d = _days(days)
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    daily = float(tenant.daily_hours) if tenant else 8.0
    rows = rep.utilisation(db, user.tenant_id, d, daily)
    cols = ["user","email","practice","billable_hours","available_hours",
            "utilisation_pct","total_hours"]
    if format == "csv":
        return _csv_response(f"utilisation_{d}d.csv", rows, cols)
    return templates.TemplateResponse(
        request=request, name="report_utilisation.html",
        context={"current_user": user, "rows": rows, "days": d,
                 "valid_days": VALID_DAYS, "daily_hours": daily,
                 "available": rows[0]["available_hours"] if rows else 0},
    )

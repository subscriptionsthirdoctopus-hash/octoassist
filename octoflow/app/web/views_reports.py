"""Reports — TS-132 through TS-138.

Every page renders an HTML table by default; passing ?format=csv on the
same URL streams the same data as text/csv (TS-138). All reports are
tenant-scoped; the viewer can never see another tenant's data.

Each windowed report accepts:
  · ?days=N             — quick preset (7 / 15 / 30 / 90 / 180)
  · ?start=YYYY-MM-DD   — custom range start (overrides days)
  · ?end=YYYY-MM-DD     — custom range end   (overrides days)
  · ?client_id=N        — filter to one client (where applicable)
  · ?user_id=N          — filter to one consultant (where applicable)
"""
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import require_user
from ..database import get_db
from ..models import Client, Tenant, User, UserRole
from ..services import reports as rep

router = APIRouter(tags=["reports"])

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

VALID_DAYS = (7, 15, 30, 90, 180)


def _days(d: int | None) -> int:
    return d if d in VALID_DAYS else 30


def _parse_date(v: str | None) -> date | None:
    if not v: return None
    try: return date.fromisoformat(v)
    except ValueError: return None


def _id(v: str | None) -> int | None:
    if not v: return None
    try: return int(v)
    except ValueError: return None


def _filter_context(db: Session, tenant_id: int, days: int,
                    start: date | None, end: date | None,
                    client_id: int | None = None,
                    user_id: int | None = None,
                    *, show_client: bool = False,
                    show_user: bool = False,
                    base_path: str) -> dict:
    """Common context passed to _report_filters.html — current selections,
    dropdown options, and a helper closure to build hrefs that preserve all
    other filters when changing one of them."""
    s, e = rep.resolve_window(days, start, end)
    selected = {
        "days":      days,
        "start":     start,
        "end":       end,
        "client_id": client_id,
        "user_id":   user_id,
        "effective_start": s,
        "effective_end":   e,
    }
    clients = (db.query(Client)
                 .filter(Client.tenant_id == tenant_id, Client.is_active.is_(True))
                 .order_by(Client.name).all()) if show_client else []
    users = (db.query(User)
               .filter(User.tenant_id == tenant_id, User.is_active.is_(True),
                       User.role != UserRole.admin)
               .order_by(User.display_name).all()) if show_user else []

    def csv_href(**overrides):
        params = {}
        if start: params["start"] = start.isoformat()
        if end:   params["end"]   = end.isoformat()
        if not (start or end): params["days"] = days
        if client_id: params["client_id"] = client_id
        if user_id:   params["user_id"]   = user_id
        params["format"] = "csv"
        params.update({k: v for k, v in overrides.items() if v is not None})
        return f"{base_path}?{urlencode(params)}"

    def days_href(d):
        return f"{base_path}?{urlencode({'days': d})}"

    return {
        "selected": selected,
        "clients":  clients,
        "users":    users,
        "valid_days": VALID_DAYS,
        "csv_href": csv_href(),
        "days_href": days_href,
        "base_path": base_path,
        "show_client": show_client,
        "show_user":   show_user,
    }


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
                     start: str | None = None, end: str | None = None,
                     client_id: str | None = None,
                     user: User = Depends(require_user), db: Session = Depends(get_db)):
    d   = _days(days)
    sd  = _parse_date(start); ed = _parse_date(end)
    cid = _id(client_id)
    rows = rep.hours_by_project(db, user.tenant_id, d, start=sd, end=ed, client_id=cid)
    cols = ["client","client_code","project","project_code",
            "total_hours","billable_hours","billable_pct"]
    if format == "csv":
        return _csv_response("hours_by_project.csv", rows, cols)
    ctx = _filter_context(db, user.tenant_id, d, sd, ed, client_id=cid,
                          show_client=True, base_path="/reports/hours-by-project")
    return templates.TemplateResponse(
        request=request, name="report_hours_by_project.html",
        context={"current_user": user, "rows": rows, "days": d,
                 "valid_days": VALID_DAYS,
                 "total_hours": sum(r["total_hours"] for r in rows),
                 **ctx},
    )


# ─────────────────────────────── TS-135 · hours by client ────────────

@router.get("/reports/hours-by-client", response_class=HTMLResponse)
def hours_by_client(request: Request, days: int = 30, format: str = "html",
                    start: str | None = None, end: str | None = None,
                    user: User = Depends(require_user), db: Session = Depends(get_db)):
    d  = _days(days)
    sd = _parse_date(start); ed = _parse_date(end)
    rows = rep.hours_by_client(db, user.tenant_id, d, start=sd, end=ed)
    cols = ["client","client_code","engagements","total_hours","billable_hours","billable_pct"]
    if format == "csv":
        return _csv_response("hours_by_client.csv", rows, cols)
    ctx = _filter_context(db, user.tenant_id, d, sd, ed,
                          base_path="/reports/hours-by-client")
    return templates.TemplateResponse(
        request=request, name="report_hours_by_client.html",
        context={"current_user": user, "rows": rows, "days": d,
                 "valid_days": VALID_DAYS,
                 "total_hours": sum(r["total_hours"] for r in rows),
                 **ctx},
    )


# ─────────────────────────────── TS-136 · billable split ─────────────

@router.get("/reports/billable-split", response_class=HTMLResponse)
def billable_split(request: Request, days: int = 30, format: str = "html",
                   start: str | None = None, end: str | None = None,
                   user_id: str | None = None,
                   user: User = Depends(require_user), db: Session = Depends(get_db)):
    d  = _days(days)
    sd = _parse_date(start); ed = _parse_date(end)
    uid = _id(user_id)
    rows = rep.billable_split(db, user.tenant_id, d, start=sd, end=ed, user_id=uid)
    cols = ["user","email","practice","total_hours","billable_hours","non_billable","billable_pct"]
    if format == "csv":
        return _csv_response("billable_split.csv", rows, cols)
    ctx = _filter_context(db, user.tenant_id, d, sd, ed, user_id=uid,
                          show_user=True, base_path="/reports/billable-split")
    return templates.TemplateResponse(
        request=request, name="report_billable_split.html",
        context={"current_user": user, "rows": rows, "days": d,
                 "valid_days": VALID_DAYS, **ctx},
    )


# ─────────────────────────────── TS-137 · utilisation ────────────────

@router.get("/reports/utilisation", response_class=HTMLResponse)
def utilisation(request: Request, days: int = 30, format: str = "html",
                start: str | None = None, end: str | None = None,
                user_id: str | None = None,
                user: User = Depends(require_user), db: Session = Depends(get_db)):
    d  = _days(days)
    sd = _parse_date(start); ed = _parse_date(end)
    uid = _id(user_id)
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    daily = float(tenant.daily_hours) if tenant else 8.0
    rows = rep.utilisation(db, user.tenant_id, d, daily,
                           start=sd, end=ed, user_id=uid)
    cols = ["user","email","practice","billable_hours","available_hours",
            "utilisation_pct","total_hours"]
    if format == "csv":
        return _csv_response("utilisation.csv", rows, cols)
    ctx = _filter_context(db, user.tenant_id, d, sd, ed, user_id=uid,
                          show_user=True, base_path="/reports/utilisation")
    return templates.TemplateResponse(
        request=request, name="report_utilisation.html",
        context={"current_user": user, "rows": rows, "days": d,
                 "valid_days": VALID_DAYS, "daily_hours": daily,
                 "available": rows[0]["available_hours"] if rows else 0,
                 **ctx},
    )

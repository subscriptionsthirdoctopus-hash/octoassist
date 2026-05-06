"""Patch Management views — fleet, aging, deployment windows."""
import csv
import io
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import require_staff
from ..database import get_db
from ..models import (
    Agent, PatchObservation, PatchSeverity, PatchWindow, PatchWindowStatus,
    PatchWindowTarget, PatchWindowTargetStatus, Tenant, User,
)
from ..services import charts, patches as patches_svc

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["patches"])


def _ctx(user: User, db: Session, **extra) -> dict:
    return {"current_user": user, "tenant": db.query(Tenant).first(), **extra}


def _parse_dt(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


# ---------- Fleet view ----------

@router.get("/patches", response_class=HTMLResponse)
def patches_home(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    tid = user.tenant_id
    rows = patches_svc.fleet_patch_summary(db, tid)
    sev = patches_svc.severity_breakdown(db, tid)
    top = patches_svc.top_missing_packages(db, tid, top=15)
    kpi = patches_svc.patch_kpis(db, tid)
    return templates.TemplateResponse(
        request=request, name="patches_list.html",
        context=_ctx(user, db,
                     rows=rows, kpi=kpi,
                     chart_severity=charts.donut(sev, size=200),
                     chart_top=charts.bars_h(top, width=540, label_w=240)),
    )


# IMPORTANT: the literal paths below MUST be declared before /patches/{agent_id}
# so they aren't matched as int route params.

@router.get("/patches/aging", response_class=HTMLResponse)
def patches_aging(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    tid = user.tenant_id
    aging = patches_svc.aging_buckets(db, tid)
    resolved = patches_svc.recently_resolved(db, tid, days=30, limit=50)
    bucket_chart = [(k, v["total"]) for k, v in aging["buckets"].items()]
    return templates.TemplateResponse(
        request=request, name="patches_aging.html",
        context=_ctx(user, db,
                     aging=aging, recently_resolved=resolved,
                     chart_age=charts.bars_h(bucket_chart, width=540, label_w=120)),
    )


@router.get("/patches/windows", response_class=HTMLResponse)
def windows_list(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = patches_svc.list_windows(db, user.tenant_id)
    return templates.TemplateResponse(
        request=request, name="patches_windows_list.html",
        context=_ctx(user, db, rows=rows,
                     window_progress=patches_svc.window_progress),
    )


@router.get("/patches/windows/new", response_class=HTMLResponse)
def window_new_form(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        request=request, name="patches_window_new.html",
        context=_ctx(user, db,
                     severities=[s.value for s in PatchSeverity]),
    )


@router.post("/patches/windows/new")
def window_create(
    name: str = Form(...),
    description: str = Form(""),
    severity_filter: str = Form(""),
    hostname_pattern: str = Form("%"),
    scheduled_for: str = Form(""),
    notes: str = Form(""),
    auto_execute: int = Form(0),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    sev = None
    if severity_filter.strip():
        try:
            sev = PatchSeverity(severity_filter.strip())
        except ValueError:
            sev = None
    win = patches_svc.create_window(
        db,
        tenant_id=user.tenant_id, creator=user,
        name=name, description=description,
        severity_filter=sev,
        hostname_pattern=hostname_pattern or "%",
        scheduled_for=_parse_dt(scheduled_for),
        notes=notes,
    )
    win.auto_execute = bool(auto_execute)
    db.commit()
    return RedirectResponse(url=f"/patches/windows/{win.id}/edit", status_code=303)


@router.get("/patches/windows/{window_id}/edit", response_class=HTMLResponse)
def window_edit(
    window_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    win = db.get(PatchWindow, window_id)
    if win is None or win.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    # Compute candidate packages = currently-missing patches across this window's
    # targets, optionally filtered by the window's severity_filter.
    candidate_packages = patches_svc.window_candidate_packages(db, win)
    return templates.TemplateResponse(
        request=request, name="patches_window_edit.html",
        context=_ctx(user, db,
                     window=win,
                     candidate_packages=candidate_packages),
    )


@router.post("/patches/windows/{window_id}/packages")
def window_save_packages(
    window_id: int,
    auto_execute: int = Form(0),
    package: list[str] = Form(default=[]),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    win = db.get(PatchWindow, window_id)
    if win is None or win.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    # Sanitise: only accept package names that are in the candidate set
    candidates = {row["name"] for row in patches_svc.window_candidate_packages(db, win)}
    chosen = sorted({p for p in (package or []) if p in candidates})
    win.selected_packages = chosen if chosen else None
    win.auto_execute = bool(auto_execute)
    db.commit()
    return RedirectResponse(url=f"/patches/windows/{window_id}", status_code=303)


@router.get("/patches/windows/{window_id}", response_class=HTMLResponse)
def window_detail(
    window_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    win = db.get(PatchWindow, window_id)
    if win is None or win.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    progress = patches_svc.window_progress(win)
    return templates.TemplateResponse(
        request=request, name="patches_window_detail.html",
        context=_ctx(user, db,
                     window=win, progress=progress,
                     statuses=[s.value for s in PatchWindowStatus],
                     target_statuses=[s.value for s in PatchWindowTargetStatus]),
    )


@router.post("/patches/windows/{window_id}/transition")
def window_transition(
    window_id: int,
    new_status: str = Form(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    win = db.get(PatchWindow, window_id)
    if win is None or win.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    try:
        ns = PatchWindowStatus(new_status)
    except ValueError:
        raise HTTPException(status_code=400)
    patches_svc.transition_window(db, window=win, new_status=ns)
    return RedirectResponse(url=f"/patches/windows/{window_id}", status_code=303)


@router.post("/patches/windows/{window_id}/targets/{target_id}")
def window_target_update(
    window_id: int,
    target_id: int,
    new_status: str = Form(...),
    note: str = Form(""),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    target = db.get(PatchWindowTarget, target_id)
    if target is None or target.window_id != window_id:
        raise HTTPException(status_code=404)
    win = db.get(PatchWindow, window_id)
    if win is None or win.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    try:
        ns = PatchWindowTargetStatus(new_status)
    except ValueError:
        raise HTTPException(status_code=400)
    patches_svc.update_target_status(db, target=target, new_status=ns, actor=user, note=note)
    return RedirectResponse(url=f"/patches/windows/{window_id}", status_code=303)


@router.get("/patches/export.csv")
def patches_export_csv(user: User = Depends(require_staff), db: Session = Depends(get_db)):
    rows = (db.query(Agent.hostname, PatchObservation.package_name, PatchObservation.current_version,
                     PatchObservation.available_version, PatchObservation.severity, PatchObservation.source,
                     PatchObservation.first_seen_at, PatchObservation.last_seen_at, PatchObservation.resolved_at)
              .join(Agent, Agent.id == PatchObservation.agent_id)
              .filter(Agent.tenant_id == user.tenant_id)
              .order_by(Agent.hostname, PatchObservation.severity, PatchObservation.package_name)
              .all())
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["hostname", "package", "current_version", "available_version",
                "severity", "source", "first_seen_at", "last_seen_at", "resolved_at"])
    for r in rows:
        w.writerow([r[0], r[1], r[2] or "", r[3] or "", r[4].value, r[5],
                    r[6].isoformat() if r[6] else "",
                    r[7].isoformat() if r[7] else "",
                    r[8].isoformat() if r[8] else ""])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.read()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="octoassist-patches.csv"'},
    )


# Numeric agent id — DEFINED LAST so the literal paths above match first.
@router.get("/patches/{agent_id}", response_class=HTMLResponse)
def patches_for(
    agent_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    agent = db.get(Agent, agent_id)
    if agent is None or agent.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    patches = patches_svc.patches_for_agent(db, agent_id)
    counts = {"critical": 0, "important": 0, "moderate": 0, "low": 0, "unknown": 0}
    for p in patches:
        counts[p.severity.value] = counts.get(p.severity.value, 0) + 1
    return templates.TemplateResponse(
        request=request, name="patches_detail.html",
        context=_ctx(user, db, agent=agent, patches=patches, counts=counts),
    )

"""Patch Management views — fleet, aging, deployment windows."""
import csv
import io
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from ..jinja_filters import install_on
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
install_on(templates)

router = APIRouter(tags=["patches"])


def _ctx(user: User, db: Session, **extra) -> dict:
    return {"current_user": user, "tenant": db.query(Tenant).first(), **extra}


def _parse_dt(s: str) -> datetime | None:
    """Parse a `<input type="datetime-local">` value submitted by the browser.

    The user types wall-clock IST; we store UTC in the DB. Delegated to the
    shared helper in jinja_filters so display + parse stay symmetric.
    """
    from ..jinja_filters import parse_ist_input
    return parse_ist_input(s)


# ---------- Fleet view ----------

@router.get("/patches", response_class=HTMLResponse)
def patches_home(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    tid = user.tenant_id
    # Windows-only per-endpoint dashboard (Linux + Mac filtered out)
    win_dash = patches_svc.windows_endpoint_dashboard(db, tid)
    sev = patches_svc.severity_breakdown(db, tid)
    top = patches_svc.top_missing_packages(db, tid, top=15)
    kpi = patches_svc.patch_kpis(db, tid)
    vendors = patches_svc.fleet_vendor_breakdown(db, tid)
    vendor_chart_data = [(v["vendor"], v["total"]) for v in vendors[:10]]

    # Build the one-click quick-deploy cards. Severity-based cards only —
    # vendor cards intentionally dropped (Linux distro card was misleading).
    sev_counts = {k: v for k, v in sev}
    all_count = sum(sev_counts.values())
    quick_deploy_options = [
        {"title": "Critical patches", "severity": "critical", "vendor": None, "all": False,
         "count": sev_counts.get("critical", 0),
         "color": "#dc2626", "pill_class": "critical",
         "subtitle": "Outstanding critical Windows patches — security fixes that should ship now."},
        {"title": "Important patches", "severity": "important", "vendor": None, "all": False,
         "count": sev_counts.get("important", 0),
         "color": "#d97706", "pill_class": "high",
         "subtitle": "Important Windows patches — feature + non-critical security fixes."},
        {"title": "Moderate patches", "severity": "moderate", "vendor": None, "all": False,
         "count": sev_counts.get("moderate", 0),
         "color": "#0891b2", "pill_class": "medium",
         "subtitle": "Drivers, .NET, OEM hardware, AI components — non-security but recommended."},
        {"title": "All pending patches", "severity": None, "vendor": None, "all": True,
         "count": all_count,
         "color": "#7c3aed", "pill_class": "low",
         "subtitle": "Push everything outstanding across all severities. Equivalent to clicking 'Download & install all' in Windows Update."},
    ]

    return templates.TemplateResponse(
        request=request, name="patches_list.html",
        context=_ctx(user, db,
                     win_dash=win_dash, kpi=kpi,
                     vendors=vendors,
                     quick_deploy_options=quick_deploy_options,
                     chart_severity=charts.donut(sev, size=200),
                     chart_top=charts.bars_h(top, width=540, label_w=240),
                     chart_vendors=charts.bars_h(vendor_chart_data, width=540, label_w=200)),
    )


# IMPORTANT: the literal paths below MUST be declared before /patches/{agent_id}
# so they aren't matched as int route params.

@router.post("/patches/quick-deploy")
def quick_deploy(
    severity: str = Form(""),
    vendor: str = Form(""),
    all_severities: int = Form(0, alias="all"),
    scheduled_for: str = Form(""),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """One-click "Deploy this category now/later" — Intune / Zoho style.

    severity        → push everything of that severity
    vendor          → push everything from that vendor
    all=1           → push every pending patch regardless of severity
    scheduled_for   → ISO datetime (IST, from <input type=datetime-local>);
                      if in the future, window is parked in 'scheduled'
                      state and auto-promoted by the scheduler.
    """
    sev_enum: PatchSeverity | None = None
    if severity.strip():
        try:
            sev_enum = PatchSeverity(severity.strip())
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid severity")
    vendor = (vendor or "").strip() or None
    if sev_enum is None and vendor is None and not all_severities:
        raise HTTPException(status_code=400, detail="Pick a severity, vendor, or all")
    when = _parse_dt(scheduled_for)  # already IST-aware via parse_ist_input
    win = patches_svc.quick_deploy_category(
        db, tenant_id=user.tenant_id, creator=user,
        severity=sev_enum, vendor=vendor, all_severities=bool(all_severities),
        scheduled_for=when,
    )
    return RedirectResponse(url=f"/patches/windows/{win.id}", status_code=303)


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


@router.post("/patches/windows/{window_id}/delete")
def window_delete(
    window_id: int,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """Hard-delete a patch window + its targets + attempts. Used when an
    admin wants to clean up old / cancelled / mistaken deployments. Will
    not delete windows whose targets are currently in_progress on an
    endpoint (would corrupt the agent's view) — admin must cancel first.
    """
    from ..models import PatchWindowTargetStatus
    win = db.get(PatchWindow, window_id)
    if win is None or win.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    if any(t.status == PatchWindowTargetStatus.in_progress for t in win.targets):
        raise HTTPException(
            status_code=409,
            detail="Cannot delete — at least one target is in_progress on its endpoint. Cancel the window first.",
        )
    db.delete(win)  # cascades to targets + attempts
    db.commit()
    return RedirectResponse(url="/patches/windows?flash=Window+deleted", status_code=303)


@router.post("/patches/windows/{window_id}/retry-failed")
def window_retry_failed(
    window_id: int,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """Flip every failed/skipped target back to planned so the agent retries
    on its next check-in. Useful when a transient timeout (slow Windows
    Update download, server busy, etc.) killed the original attempt."""
    from ..models import PatchWindowTargetStatus
    win = db.get(PatchWindow, window_id)
    if win is None or win.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404)
    if win.status != PatchWindowStatus.in_progress:
        raise HTTPException(status_code=409, detail="Window must be in_progress")
    retried = 0
    for t in win.targets:
        if t.status in (PatchWindowTargetStatus.failed, PatchWindowTargetStatus.skipped):
            t.status = PatchWindowTargetStatus.planned
            t.note = ((t.note or "") + " · retry queued").strip(" ·")[:1000]
            retried += 1
    db.commit()
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


@router.post("/patches/{agent_id}/deploy")
def patches_deploy_to_agent(
    agent_id: int,
    package: list[str] = Form(default=[]),
    scheduled_for: str = Form(""),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """Push admin-selected packages to one specific endpoint (now or scheduled)."""
    if not package:
        raise HTTPException(status_code=400, detail="No packages selected")
    when = _parse_dt(scheduled_for)
    try:
        win = patches_svc.deploy_to_single_endpoint(
            db, tenant_id=user.tenant_id, creator=user,
            agent_id=agent_id, package_names=package,
            scheduled_for=when,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(url=f"/patches/windows/{win.id}", status_code=303)


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

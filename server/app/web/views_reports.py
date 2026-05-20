"""Reports & Dashboards — staff only.

Two surfaces:

  /reports             — operational KPI dashboard (live charts, throughput,
                         workload, SLA — for the helpdesk lead's day-to-day).
  /reports/iso27001    — ISO 27001:2022 control-family reports (audit-ready
                         downloads with date filters, cited Annex A controls,
                         signed off as evidence for the next external audit).

Both share the same data services (services/reporting.py + services/charts.py
+ services/patches.py); the difference is framing, filtering, and download.
"""
import csv
import io
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from ..jinja_filters import install_on
from sqlalchemy.orm import Session

from ..auth import require_staff
from ..database import get_db
from ..models import (
    Agent, AssetSnapshot, Change, ChangeEvent, KbArticle, PatchObservation,
    Problem, ProblemStatus, Tenant, Ticket, TicketEvent, TicketStatus, User,
)
from ..services import charts, patches as patches_svc, reporting

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
install_on(templates)

router = APIRouter(tags=["reports"])


def _ctx(user: User, db: Session, **extra) -> dict:
    return {"current_user": user, "tenant": db.query(Tenant).first(), **extra}


# ----------  Dashboard ----------

@router.get("/reports", response_class=HTMLResponse)
def reports_home(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    tenant_id = user.tenant_id
    kpi = reporting.kpi_summary(db, tenant_id)
    by_status = reporting.tickets_by_status(db, tenant_id)
    by_priority = reporting.tickets_by_priority(db, tenant_id, only_open=True)
    per_day = reporting.tickets_per_day(db, tenant_id, days=30)
    by_category = reporting.tickets_by_category(db, tenant_id, days=30, top=8)
    sla = reporting.sla_compliance(db, tenant_id, days=30)
    workload = reporting.workload_by_assignee(db, tenant_id, top=8)
    patch_kpi = patches_svc.patch_kpis(db, tenant_id)

    sparkline_values = [v for _, v in per_day]

    return templates.TemplateResponse(
        request=request, name="reports_home.html",
        context=_ctx(user, db,
                     kpi=kpi,
                     patch_kpi=patch_kpi,
                     spark_tickets=charts.sparkline(sparkline_values, width=160, height=40),
                     chart_status=charts.donut(by_status, size=200),
                     chart_priority=charts.donut(by_priority, size=200),
                     chart_per_day=charts.line_series(per_day, height=220),
                     chart_category=charts.bars_h(by_category, width=520, label_w=180),
                     chart_workload=charts.bars_h(workload, width=520, label_w=200),
                     sla=sla),
    )


# ----------  Tickets report ----------

@router.get("/reports/tickets", response_class=HTMLResponse)
def report_tickets(
    request: Request,
    days: int = 30,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    tenant_id = user.tenant_id
    by_status = reporting.tickets_by_status(db, tenant_id)
    by_priority_open = reporting.tickets_by_priority(db, tenant_id, only_open=True)
    by_priority_all = reporting.tickets_by_priority(db, tenant_id, only_open=False)
    by_kind = reporting.tickets_by_kind(db, tenant_id)
    per_day = reporting.tickets_per_day(db, tenant_id, days=days)
    by_category = reporting.tickets_by_category(db, tenant_id, days=days, top=10)
    workload = reporting.workload_by_assignee(db, tenant_id, top=10)
    top_reps = reporting.top_reporters(db, tenant_id, days=days, top=10)

    return templates.TemplateResponse(
        request=request, name="report_tickets.html",
        context=_ctx(user, db,
                     days=days,
                     chart_status=charts.donut(by_status, size=210),
                     chart_priority_open=charts.donut(by_priority_open, size=210),
                     chart_priority_all=charts.donut(by_priority_all, size=210),
                     chart_kind=charts.donut(by_kind, size=210),
                     chart_per_day=charts.line_series(per_day, height=220),
                     chart_category=charts.bars_h(by_category, width=560, label_w=200),
                     chart_workload=charts.bars_h(workload, width=560, label_w=220),
                     chart_top_reps=charts.bars_h(top_reps, width=560, label_w=220)),
    )


# ----------  SLA report ----------

@router.get("/reports/sla", response_class=HTMLResponse)
def report_sla(
    request: Request,
    days: int = 30,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    tenant_id = user.tenant_id
    overall = reporting.sla_compliance(db, tenant_id, days=days)
    by_priority = reporting.sla_compliance_by_priority(db, tenant_id, days=days)
    breaches = reporting.sla_breaches(db, tenant_id, limit=100)

    return templates.TemplateResponse(
        request=request, name="report_sla.html",
        context=_ctx(user, db,
                     days=days,
                     overall=overall,
                     by_priority=by_priority,
                     breaches=breaches,
                     chart_compliance=charts.bars_h(
                         [(p["priority"], p["pct"]) for p in by_priority],
                         width=560, label_w=140,
                     )),
    )


# ----------  Assets report ----------

@router.get("/reports/assets", response_class=HTMLResponse)
def report_assets(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    tenant_id = user.tenant_id
    os_breakdown = reporting.asset_os_breakdown(db, tenant_id)
    sw_top = reporting.asset_software_top(db, tenant_id, top=20)
    ram = reporting.asset_ram_distribution(db, tenant_id)
    total = (db.query(Agent).filter(Agent.tenant_id == tenant_id).count())

    return templates.TemplateResponse(
        request=request, name="report_assets.html",
        context=_ctx(user, db,
                     total=total,
                     os_breakdown=os_breakdown,
                     ram=ram,
                     sw_top=sw_top,
                     chart_os=charts.donut(os_breakdown, size=210),
                     chart_ram=charts.donut(ram, size=210),
                     chart_sw=charts.bars_h(sw_top, width=560, label_w=280)),
    )


# ----------  Changes report ----------

@router.get("/reports/changes", response_class=HTMLResponse)
def report_changes(
    request: Request,
    days: int = 30,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    tenant_id = user.tenant_id
    by_status = reporting.changes_by_status(db, tenant_id)
    by_type = reporting.changes_by_type(db, tenant_id)
    upcoming = reporting.upcoming_changes(db, tenant_id, limit=20)
    throughput = reporting.change_throughput(db, tenant_id, days=days)

    return templates.TemplateResponse(
        request=request, name="report_changes.html",
        context=_ctx(user, db,
                     days=days,
                     by_status=by_status,
                     by_type=by_type,
                     upcoming=upcoming,
                     throughput=throughput,
                     chart_status=charts.donut(by_status, size=210),
                     chart_type=charts.donut(by_type, size=210)),
    )


# ----------  CSV exports ----------

def _stream_csv(rows_iter, headers: list[str], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for row in rows_iter:
        w.writerow(row)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.read()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports/export/tickets.csv")
def export_tickets_csv(user: User = Depends(require_staff), db: Session = Depends(get_db)):
    rows = (db.query(Ticket)
              .filter(Ticket.tenant_id == user.tenant_id)
              .order_by(Ticket.created_at.desc()).all())
    def gen():
        for t in rows:
            yield [
                t.ticket_number,
                t.kind.value,
                (t.category.name if t.category else ""),
                t.title,
                t.priority.value,
                t.status.value,
                (t.reporter.email if t.reporter else ""),
                (t.assignee.email if t.assignee else ""),
                t.created_at.astimezone(timezone.utc).isoformat() if t.created_at else "",
                t.resolved_at.astimezone(timezone.utc).isoformat() if t.resolved_at else "",
                t.due_resolution_at.astimezone(timezone.utc).isoformat() if t.due_resolution_at else "",
            ]
    return _stream_csv(
        gen(),
        ["ticket_number", "kind", "category", "title", "priority", "status",
         "reporter", "assignee", "created_at", "resolved_at", "due_resolution_at"],
        filename="octoassist-tickets.csv",
    )


@router.get("/reports/export/assets.csv")
def export_assets_csv(user: User = Depends(require_staff), db: Session = Depends(get_db)):
    """Export the latest snapshot for each registered agent."""
    agents = (db.query(Agent)
                .filter(Agent.tenant_id == user.tenant_id)
                .order_by(Agent.hostname).all())
    def gen():
        for a in agents:
            snap = (db.query(AssetSnapshot)
                      .filter(AssetSnapshot.agent_id == a.id)
                      .order_by(AssetSnapshot.snapshot_at.desc()).first())
            payload = snap.payload if snap else {}
            os_caption = (payload.get("os") or {}).get("caption", "")
            cpu = (payload.get("cpu") or {}).get("name", "")
            ram = (payload.get("memory") or {}).get("total_gb", "")
            disks = payload.get("disks") or []
            disk_total = sum((d.get("size_gb") or 0) for d in disks) if disks else ""
            disk_free = sum((d.get("free_gb") or 0) for d in disks) if disks else ""
            user_logged = payload.get("logged_in_user", "")
            sw_count = len(payload.get("software") or [])
            yield [
                a.hostname, a.machine_id, os_caption, cpu, ram,
                disk_total, disk_free, user_logged, sw_count,
                a.last_seen_at.astimezone(timezone.utc).isoformat() if a.last_seen_at else "",
                a.registered_at.astimezone(timezone.utc).isoformat() if a.registered_at else "",
            ]
    return _stream_csv(
        gen(),
        ["hostname", "machine_id", "os", "cpu", "ram_gb",
         "disk_total_gb", "disk_free_gb", "logged_in_user", "software_count",
         "last_seen_at", "registered_at"],
        filename="octoassist-assets.csv",
    )


@router.get("/reports/export/changes.csv")
def export_changes_csv(user: User = Depends(require_staff), db: Session = Depends(get_db)):
    rows = (db.query(Change)
              .filter(Change.tenant_id == user.tenant_id)
              .order_by(Change.created_at.desc()).all())
    def gen():
        for c in rows:
            yield [
                c.change_number, c.title,
                c.change_type.value, c.risk.value, c.status.value,
                (c.requester.email if c.requester else ""),
                (c.implementer.email if c.implementer else ""),
                (c.cab_approver.email if c.cab_approver else ""),
                c.planned_start.astimezone(timezone.utc).isoformat() if c.planned_start else "",
                c.planned_end.astimezone(timezone.utc).isoformat() if c.planned_end else "",
                c.actual_start.astimezone(timezone.utc).isoformat() if c.actual_start else "",
                c.actual_end.astimezone(timezone.utc).isoformat() if c.actual_end else "",
                c.created_at.astimezone(timezone.utc).isoformat() if c.created_at else "",
            ]
    return _stream_csv(
        gen(),
        ["change_number", "title", "type", "risk", "status",
         "requester", "implementer", "cab_approver",
         "planned_start", "planned_end", "actual_start", "actual_end", "created_at"],
        filename="octoassist-changes.csv",
    )


# ===========================================================================
# ISO 27001:2022 — Annex A control-family reports
#
# Aligned with ISO/IEC 27001:2022 Annex A. Each report names the control(s)
# it satisfies. Reports are runnable as HTML preview AND downloadable as CSV
# with a date filter (and where relevant, status filter). The goal is that
# an external auditor sees the control reference on the report title and
# the CSV is the evidence pack.
# ===========================================================================

# --- ISO control catalog used by the landing page ---
# (key, title, control_ref, what_it_evidences, endpoint, csv_endpoint, group)
ISO_REPORTS = [
    # A.5 Organizational controls
    {
        "key": "incident-management",
        "group": "A.5 Organizational controls",
        "title": "Incident management",
        "controls": "A.5.24 · A.5.25 · A.5.26",
        "summary": "All incidents (tickets of kind = incident) — created, in flight, resolved, SLA breach status. Evidences planning, decision, and response to information security events.",
        "preview": "/reports/iso27001/incidents",
        "csv": "/reports/iso27001/incidents.csv",
    },
    {
        "key": "problem-management",
        "group": "A.5 Organizational controls",
        "title": "Lessons learned (Problem records)",
        "controls": "A.5.27",
        "summary": "Problems opened with root-cause findings + Known-Error entries. Evidences how the organisation learns from incidents to reduce recurrence.",
        "preview": "/reports/iso27001/problems",
        "csv": "/reports/iso27001/problems.csv",
    },
    {
        "key": "evidence-trail",
        "group": "A.5 Organizational controls",
        "title": "Audit evidence trail",
        "controls": "A.5.28",
        "summary": "Append-only event log from tickets + changes (status transitions, approvals, edits) within a date window. Evidences collection of evidence per A.5.28.",
        "preview": "/reports/iso27001/audit-trail",
        "csv": "/reports/iso27001/audit-trail.csv",
    },
    {
        "key": "access-control",
        "group": "A.5 Organizational controls",
        "title": "Access control register",
        "controls": "A.5.15 · A.5.16 · A.5.18",
        "summary": "Current user roster — role, status, last sign-in, Entra OID. Evidences identity management and access provisioning/de-provisioning posture.",
        "preview": "/reports/iso27001/access",
        "csv": "/reports/iso27001/access.csv",
    },
    {
        "key": "asset-inventory",
        "group": "A.5 Organizational controls",
        "title": "Asset inventory",
        "controls": "A.5.9 · A.5.10",
        "summary": "All registered endpoints with OS, hardware, last-seen, software count, logged-in user. Evidences the asset inventory required by A.5.9.",
        "preview": "/reports/iso27001/assets",
        "csv": "/reports/iso27001/assets.csv",
    },
    {
        "key": "kb-procedures",
        "group": "A.5 Organizational controls",
        "title": "Documented procedures (Knowledge Base)",
        "controls": "A.5.37",
        "summary": "Published knowledge-base articles — title, audience, author, last update. Evidences that operating procedures are documented and accessible.",
        "preview": "/reports/iso27001/kb",
        "csv": "/reports/iso27001/kb.csv",
    },
    # A.8 Technological controls
    {
        "key": "change-management",
        "group": "A.8 Technological controls",
        "title": "Change management",
        "controls": "A.8.32",
        "summary": "All change records with type, risk, CAB approver, planned vs actual window, outcome. The audit's primary evidence pack for change control.",
        "preview": "/reports/iso27001/changes",
        "csv": "/reports/iso27001/changes.csv",
    },
    {
        "key": "patch-compliance",
        "group": "A.8 Technological controls",
        "title": "Vulnerability & patch compliance",
        "controls": "A.8.8",
        "summary": "Patches reported missing on endpoints — severity, days outstanding, install status. Evidences management of technical vulnerabilities.",
        "preview": "/reports/iso27001/patches",
        "csv": "/reports/iso27001/patches.csv",
    },
]


def _parse_date(s: str | None, default_days_ago: int | None = None):
    """Accept 'YYYY-MM-DD' (treated as IST start-of-day) and return a UTC datetime.
    Returns None if blank. If default_days_ago is given, falls back to that."""
    if s and s.strip():
        try:
            d = datetime.strptime(s.strip(), "%Y-%m-%d")
            # Treat as IST start-of-day; IST is UTC+5:30 with no DST
            return d.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
        except ValueError:
            pass
    if default_days_ago is not None:
        return datetime.now(timezone.utc) - timedelta(days=default_days_ago)
    return None


def _common_filter_ctx(date_from: str | None, date_to: str | None):
    """Default date window for the UI = last 30 days IST."""
    today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
    return {
        "date_from": date_from or (today - timedelta(days=30)).isoformat(),
        "date_to":   date_to   or today.isoformat(),
    }


# --------- Landing page ---------

@router.get("/reports/iso27001", response_class=HTMLResponse)
def iso27001_home(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        request=request, name="reports_iso27001.html",
        context=_ctx(user, db, iso_reports=ISO_REPORTS),
    )


# --------- A.5.24-A.5.26: Incidents ---------

def _incidents_query(db, tenant_id, df, dt, status):
    qy = db.query(Ticket).filter(Ticket.tenant_id == tenant_id)
    from ..models import TicketKind
    qy = qy.filter(Ticket.kind == TicketKind.incident)
    if df: qy = qy.filter(Ticket.created_at >= df)
    if dt: qy = qy.filter(Ticket.created_at <= dt)
    if status:
        try:
            qy = qy.filter(Ticket.status == TicketStatus(status))
        except ValueError:
            pass
    return qy.order_by(Ticket.created_at.desc())


@router.get("/reports/iso27001/incidents", response_class=HTMLResponse)
def iso_incidents(
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    df = _parse_date(date_from, default_days_ago=30)
    dt = _parse_date(date_to)
    rows = _incidents_query(db, user.tenant_id, df, dt, status).limit(500).all()
    return templates.TemplateResponse(
        request=request, name="reports_iso27001_run.html",
        context=_ctx(user, db,
            report_key="incident-management",
            controls="A.5.24 · A.5.25 · A.5.26 — Information security incident management",
            title="Incident management",
            rows=rows, row_type="ticket",
            status_options=[s.value for s in TicketStatus],
            **_common_filter_ctx(date_from, date_to),
            status=status or "",
            csv_url=f"/reports/iso27001/incidents.csv?date_from={date_from or ''}&date_to={date_to or ''}&status={status or ''}",
        ),
    )


@router.get("/reports/iso27001/incidents.csv")
def iso_incidents_csv(
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    df = _parse_date(date_from, default_days_ago=30)
    dt = _parse_date(date_to)
    rows = _incidents_query(db, user.tenant_id, df, dt, status).all()
    def gen():
        for t in rows:
            resolved = t.resolved_at.astimezone(timezone.utc).isoformat() if t.resolved_at else ""
            due = t.due_resolution_at.astimezone(timezone.utc).isoformat() if t.due_resolution_at else ""
            sla_state = ""
            if t.resolved_at and t.due_resolution_at:
                sla_state = "within" if t.resolved_at <= t.due_resolution_at else "breached"
            elif t.due_resolution_at and not t.resolved_at:
                sla_state = "open-past-due" if datetime.now(timezone.utc) > t.due_resolution_at else "open"
            yield [
                t.ticket_number,
                (t.category.name if t.category else ""),
                t.title,
                t.priority.value, t.status.value,
                (t.reporter.email if t.reporter else ""),
                (t.assignee.email if t.assignee else ""),
                (t.location or ""),
                t.created_at.astimezone(timezone.utc).isoformat() if t.created_at else "",
                t.first_response_at.astimezone(timezone.utc).isoformat() if t.first_response_at else "",
                resolved, due, sla_state,
            ]
    return _stream_csv(
        gen(),
        ["ticket_number","category","title","priority","status",
         "reporter","assignee","location",
         "created_at_utc","first_response_at_utc","resolved_at_utc","due_resolution_at_utc","sla_state"],
        filename=f"iso27001-A.5.24-incidents-{datetime.utcnow():%Y%m%d}.csv",
    )


# --------- A.5.27: Problems / Lessons learned ---------

@router.get("/reports/iso27001/problems", response_class=HTMLResponse)
def iso_problems(
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    df = _parse_date(date_from, default_days_ago=90)
    dt = _parse_date(date_to)
    qy = db.query(Problem).filter(Problem.tenant_id == user.tenant_id)
    if df: qy = qy.filter(Problem.created_at >= df)
    if dt: qy = qy.filter(Problem.created_at <= dt)
    if status:
        try: qy = qy.filter(Problem.status == ProblemStatus(status))
        except ValueError: pass
    rows = qy.order_by(Problem.created_at.desc()).limit(500).all()
    return templates.TemplateResponse(
        request=request, name="reports_iso27001_run.html",
        context=_ctx(user, db,
            report_key="problem-management",
            controls="A.5.27 — Learning from information security incidents",
            title="Lessons learned (Problem records)",
            rows=rows, row_type="problem",
            status_options=[s.value for s in ProblemStatus],
            **_common_filter_ctx(date_from, date_to),
            status=status or "",
            csv_url=f"/reports/iso27001/problems.csv?date_from={date_from or ''}&date_to={date_to or ''}&status={status or ''}",
        ),
    )


@router.get("/reports/iso27001/problems.csv")
def iso_problems_csv(
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    df = _parse_date(date_from, default_days_ago=90)
    dt = _parse_date(date_to)
    qy = db.query(Problem).filter(Problem.tenant_id == user.tenant_id)
    if df: qy = qy.filter(Problem.created_at >= df)
    if dt: qy = qy.filter(Problem.created_at <= dt)
    if status:
        try: qy = qy.filter(Problem.status == ProblemStatus(status))
        except ValueError: pass
    rows = qy.order_by(Problem.created_at.desc()).all()
    def gen():
        for p in rows:
            yield [
                p.problem_number, p.title, p.priority.value, p.status.value,
                (p.reporter.email if p.reporter else ""),
                (p.assignee.email if p.assignee else ""),
                len(p.linked_tickets),
                (p.workaround or "")[:1000].replace("\n", " ↵ "),
                (p.root_cause or "")[:2000].replace("\n", " ↵ "),
                p.created_at.astimezone(timezone.utc).isoformat() if p.created_at else "",
                p.updated_at.astimezone(timezone.utc).isoformat() if p.updated_at else "",
            ]
    return _stream_csv(
        gen(),
        ["problem_number","title","priority","status","reporter","assignee",
         "linked_ticket_count","workaround","root_cause","created_at_utc","updated_at_utc"],
        filename=f"iso27001-A.5.27-problems-{datetime.utcnow():%Y%m%d}.csv",
    )


# --------- A.5.28: Audit evidence trail ---------

@router.get("/reports/iso27001/audit-trail", response_class=HTMLResponse)
def iso_audit_trail(
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    df = _parse_date(date_from, default_days_ago=30)
    dt = _parse_date(date_to)
    # Get ticket events + change events, merged by created_at
    tq = db.query(TicketEvent).join(Ticket).filter(Ticket.tenant_id == user.tenant_id)
    cq = db.query(ChangeEvent).join(Change).filter(Change.tenant_id == user.tenant_id)
    if df: tq = tq.filter(TicketEvent.created_at >= df); cq = cq.filter(ChangeEvent.created_at >= df)
    if dt: tq = tq.filter(TicketEvent.created_at <= dt); cq = cq.filter(ChangeEvent.created_at <= dt)
    ticket_events = tq.order_by(TicketEvent.created_at.desc()).limit(300).all()
    change_events = cq.order_by(ChangeEvent.created_at.desc()).limit(200).all()
    events = [("ticket", e) for e in ticket_events] + [("change", e) for e in change_events]
    events.sort(key=lambda x: x[1].created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return templates.TemplateResponse(
        request=request, name="reports_iso27001_run.html",
        context=_ctx(user, db,
            report_key="evidence-trail",
            controls="A.5.28 — Collection of evidence",
            title="Audit evidence trail",
            rows=events[:300], row_type="event",
            status_options=[],
            **_common_filter_ctx(date_from, date_to),
            status="",
            csv_url=f"/reports/iso27001/audit-trail.csv?date_from={date_from or ''}&date_to={date_to or ''}",
        ),
    )


@router.get("/reports/iso27001/audit-trail.csv")
def iso_audit_trail_csv(
    date_from: str | None = None,
    date_to: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    df = _parse_date(date_from, default_days_ago=30)
    dt = _parse_date(date_to)
    tq = db.query(TicketEvent).join(Ticket).filter(Ticket.tenant_id == user.tenant_id)
    cq = db.query(ChangeEvent).join(Change).filter(Change.tenant_id == user.tenant_id)
    if df: tq = tq.filter(TicketEvent.created_at >= df); cq = cq.filter(ChangeEvent.created_at >= df)
    if dt: tq = tq.filter(TicketEvent.created_at <= dt); cq = cq.filter(ChangeEvent.created_at <= dt)
    te = tq.order_by(TicketEvent.created_at.desc()).all()
    ce = cq.order_by(ChangeEvent.created_at.desc()).all()
    events = [("ticket", e) for e in te] + [("change", e) for e in ce]
    events.sort(key=lambda x: x[1].created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    def gen():
        import json as _json
        for source, e in events:
            ref = e.ticket.ticket_number if source == "ticket" else e.change.change_number
            yield [
                e.created_at.astimezone(timezone.utc).isoformat() if e.created_at else "",
                source, ref, e.kind.value,
                (e.actor.email if e.actor else "system"),
                (e.note or ""),
                _json.dumps(e.before_value or {}, default=str),
                _json.dumps(e.after_value or {}, default=str),
            ]
    return _stream_csv(
        gen(),
        ["when_utc","source","record","event","actor","note","before_json","after_json"],
        filename=f"iso27001-A.5.28-audit-trail-{datetime.utcnow():%Y%m%d}.csv",
    )


# --------- A.5.15-A.5.18: Access control register ---------

@router.get("/reports/iso27001/access", response_class=HTMLResponse)
def iso_access(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(User)
              .filter(User.tenant_id == user.tenant_id)
              .order_by(User.is_active.desc(), User.role, User.email).all())
    return templates.TemplateResponse(
        request=request, name="reports_iso27001_run.html",
        context=_ctx(user, db,
            report_key="access-control",
            controls="A.5.15 · A.5.16 · A.5.18 — Access control, identity management, access rights",
            title="Access control register",
            rows=rows, row_type="user",
            status_options=[],
            date_from="", date_to="", status="",
            csv_url="/reports/iso27001/access.csv",
        ),
    )


@router.get("/reports/iso27001/access.csv")
def iso_access_csv(
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(User)
              .filter(User.tenant_id == user.tenant_id)
              .order_by(User.is_active.desc(), User.role, User.email).all())
    def gen():
        for u in rows:
            yield [
                u.email, (u.full_name or ""), u.role.value,
                "active" if u.is_active else "deactivated",
                "yes" if u.is_cab_member else "no",
                (u.department or ""), (u.location or ""),
                (u.entra_oid or ""),  # presence = federated, absence = local-only
                u.created_at.astimezone(timezone.utc).isoformat() if u.created_at else "",
                u.last_login_at.astimezone(timezone.utc).isoformat() if u.last_login_at else "",
                u.synced_at.astimezone(timezone.utc).isoformat() if u.synced_at else "",
            ]
    return _stream_csv(
        gen(),
        ["email","name","role","status","is_cab","department","location",
         "entra_oid","created_at_utc","last_login_utc","synced_from_entra_utc"],
        filename=f"iso27001-A.5.16-access-register-{datetime.utcnow():%Y%m%d}.csv",
    )


# --------- A.5.9-A.5.10: Asset inventory ---------

@router.get("/reports/iso27001/assets", response_class=HTMLResponse)
def iso_assets(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(Agent)
              .filter(Agent.tenant_id == user.tenant_id)
              .order_by(Agent.hostname).all())
    return templates.TemplateResponse(
        request=request, name="reports_iso27001_run.html",
        context=_ctx(user, db,
            report_key="asset-inventory",
            controls="A.5.9 · A.5.10 — Inventory of information and other associated assets · Acceptable use",
            title="Asset inventory",
            rows=rows, row_type="agent",
            status_options=[],
            date_from="", date_to="", status="",
            csv_url="/reports/iso27001/assets.csv",
        ),
    )


@router.get("/reports/iso27001/assets.csv")
def iso_assets_csv(
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """Reuses the existing export_assets_csv logic but with ISO-friendly filename."""
    agents = (db.query(Agent)
                .filter(Agent.tenant_id == user.tenant_id)
                .order_by(Agent.hostname).all())
    def gen():
        for a in agents:
            snap = (db.query(AssetSnapshot)
                      .filter(AssetSnapshot.agent_id == a.id)
                      .order_by(AssetSnapshot.snapshot_at.desc()).first())
            payload = snap.payload if snap else {}
            os_caption = (payload.get("os") or {}).get("caption", "")
            cpu = (payload.get("cpu") or {}).get("name", "")
            ram = (payload.get("memory") or {}).get("total_gb", "")
            disks = payload.get("disks") or []
            disk_total = sum((d.get("size_gb") or 0) for d in disks) if disks else ""
            user_logged = payload.get("logged_in_user", "")
            sw_count = len(payload.get("software") or [])
            yield [
                a.hostname, a.machine_id, os_caption, cpu, ram,
                disk_total, user_logged, sw_count,
                a.last_seen_at.astimezone(timezone.utc).isoformat() if a.last_seen_at else "",
                a.registered_at.astimezone(timezone.utc).isoformat() if a.registered_at else "",
            ]
    return _stream_csv(
        gen(),
        ["hostname","machine_id","os","cpu","ram_gb","disk_total_gb",
         "logged_in_user","software_count","last_seen_utc","registered_at_utc"],
        filename=f"iso27001-A.5.9-asset-inventory-{datetime.utcnow():%Y%m%d}.csv",
    )


# --------- A.5.37: Documented operating procedures (KB) ---------

@router.get("/reports/iso27001/kb", response_class=HTMLResponse)
def iso_kb(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(KbArticle)
              .filter(KbArticle.tenant_id == user.tenant_id)
              .order_by(KbArticle.updated_at.desc()).all())
    return templates.TemplateResponse(
        request=request, name="reports_iso27001_run.html",
        context=_ctx(user, db,
            report_key="kb-procedures",
            controls="A.5.37 — Documented operating procedures",
            title="Documented operating procedures (Knowledge Base)",
            rows=rows, row_type="kb",
            status_options=[],
            date_from="", date_to="", status="",
            csv_url="/reports/iso27001/kb.csv",
        ),
    )


@router.get("/reports/iso27001/kb.csv")
def iso_kb_csv(
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(KbArticle)
              .filter(KbArticle.tenant_id == user.tenant_id)
              .order_by(KbArticle.updated_at.desc()).all())
    def gen():
        for a in rows:
            yield [
                a.slug, a.title,
                (a.category.name if a.category else ""),
                a.status.value, a.visibility.value,
                (a.author.email if a.author else ""), a.view_count,
                a.created_at.astimezone(timezone.utc).isoformat() if a.created_at else "",
                a.updated_at.astimezone(timezone.utc).isoformat() if a.updated_at else "",
                a.published_at.astimezone(timezone.utc).isoformat() if a.published_at else "",
            ]
    return _stream_csv(
        gen(),
        ["slug","title","category","status","audience","author","view_count",
         "created_at_utc","updated_at_utc","published_at_utc"],
        filename=f"iso27001-A.5.37-kb-procedures-{datetime.utcnow():%Y%m%d}.csv",
    )


# --------- A.8.32: Change management (filtered) ---------

@router.get("/reports/iso27001/changes", response_class=HTMLResponse)
def iso_changes(
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    from ..models import ChangeStatus
    df = _parse_date(date_from, default_days_ago=90)
    dt = _parse_date(date_to)
    qy = db.query(Change).filter(Change.tenant_id == user.tenant_id)
    if df: qy = qy.filter(Change.created_at >= df)
    if dt: qy = qy.filter(Change.created_at <= dt)
    if status:
        try: qy = qy.filter(Change.status == ChangeStatus(status))
        except ValueError: pass
    rows = qy.order_by(Change.created_at.desc()).limit(500).all()
    return templates.TemplateResponse(
        request=request, name="reports_iso27001_run.html",
        context=_ctx(user, db,
            report_key="change-management",
            controls="A.8.32 — Change management",
            title="Change management",
            rows=rows, row_type="change",
            status_options=[s.value for s in ChangeStatus],
            **_common_filter_ctx(date_from, date_to),
            status=status or "",
            csv_url=f"/reports/iso27001/changes.csv?date_from={date_from or ''}&date_to={date_to or ''}&status={status or ''}",
        ),
    )


@router.get("/reports/iso27001/changes.csv")
def iso_changes_csv(
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    from ..models import ChangeStatus
    df = _parse_date(date_from, default_days_ago=90)
    dt = _parse_date(date_to)
    qy = db.query(Change).filter(Change.tenant_id == user.tenant_id)
    if df: qy = qy.filter(Change.created_at >= df)
    if dt: qy = qy.filter(Change.created_at <= dt)
    if status:
        try: qy = qy.filter(Change.status == ChangeStatus(status))
        except ValueError: pass
    rows = qy.order_by(Change.created_at.desc()).all()
    def gen():
        for c in rows:
            yield [
                c.change_number, c.title, c.change_type.value, c.risk.value, c.status.value,
                (c.requester.email if c.requester else ""),
                (c.implementer.email if c.implementer else ""),
                (c.cab_approver.email if c.cab_approver else ""),
                c.cab_decision_at.astimezone(timezone.utc).isoformat() if c.cab_decision_at else "",
                (c.cab_decision_note or "")[:500].replace("\n", " ↵ "),
                c.planned_start.astimezone(timezone.utc).isoformat() if c.planned_start else "",
                c.planned_end.astimezone(timezone.utc).isoformat() if c.planned_end else "",
                c.actual_start.astimezone(timezone.utc).isoformat() if c.actual_start else "",
                c.actual_end.astimezone(timezone.utc).isoformat() if c.actual_end else "",
                (c.rollback_plan or "")[:1000].replace("\n", " ↵ "),
                c.created_at.astimezone(timezone.utc).isoformat() if c.created_at else "",
            ]
    return _stream_csv(
        gen(),
        ["change_number","title","type","risk","status","requester","implementer",
         "cab_approver","cab_decision_at_utc","cab_decision_note",
         "planned_start_utc","planned_end_utc","actual_start_utc","actual_end_utc",
         "rollback_plan","created_at_utc"],
        filename=f"iso27001-A.8.32-changes-{datetime.utcnow():%Y%m%d}.csv",
    )


# --------- A.8.8: Vulnerability / patch compliance ---------

@router.get("/reports/iso27001/patches", response_class=HTMLResponse)
def iso_patches(
    request: Request,
    severity: str | None = None,
    only_open: int = 1,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    from ..models import PatchSeverity
    qy = (db.query(PatchObservation).join(Agent)
            .filter(Agent.tenant_id == user.tenant_id))
    if only_open:
        qy = qy.filter(PatchObservation.resolved_at.is_(None))
    if severity:
        try: qy = qy.filter(PatchObservation.severity == PatchSeverity(severity))
        except ValueError: pass
    rows = qy.order_by(PatchObservation.severity.desc(), PatchObservation.first_seen_at).limit(500).all()
    return templates.TemplateResponse(
        request=request, name="reports_iso27001_run.html",
        context=_ctx(user, db,
            report_key="patch-compliance",
            controls="A.8.8 — Management of technical vulnerabilities",
            title="Vulnerability & patch compliance",
            rows=rows, row_type="patch",
            status_options=[s.value for s in PatchSeverity],
            now=datetime.now(timezone.utc),
            date_from="", date_to="",
            status=severity or "",
            only_open=only_open,
            csv_url=f"/reports/iso27001/patches.csv?severity={severity or ''}&only_open={only_open}",
        ),
    )


@router.get("/reports/iso27001/patches.csv")
def iso_patches_csv(
    severity: str | None = None,
    only_open: int = 1,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    from ..models import PatchSeverity
    qy = (db.query(PatchObservation).join(Agent)
            .filter(Agent.tenant_id == user.tenant_id))
    if only_open:
        qy = qy.filter(PatchObservation.resolved_at.is_(None))
    if severity:
        try: qy = qy.filter(PatchObservation.severity == PatchSeverity(severity))
        except ValueError: pass
    rows = qy.order_by(PatchObservation.severity.desc(), PatchObservation.first_seen_at).all()
    now = datetime.now(timezone.utc)
    def gen():
        for p in rows:
            age_days = (now - p.first_seen_at).days if p.first_seen_at else ""
            yield [
                p.agent.hostname if p.agent else "",
                p.package_name, p.current_version or "", p.available_version or "",
                p.severity.value, p.source, (p.title or "")[:200],
                age_days,
                p.first_seen_at.astimezone(timezone.utc).isoformat() if p.first_seen_at else "",
                p.last_seen_at.astimezone(timezone.utc).isoformat() if p.last_seen_at else "",
                p.resolved_at.astimezone(timezone.utc).isoformat() if p.resolved_at else "",
            ]
    return _stream_csv(
        gen(),
        ["hostname","package","current_version","available_version","severity",
         "source","title","days_outstanding","first_seen_utc","last_seen_utc","resolved_at_utc"],
        filename=f"iso27001-A.8.8-patch-compliance-{datetime.utcnow():%Y%m%d}.csv",
    )

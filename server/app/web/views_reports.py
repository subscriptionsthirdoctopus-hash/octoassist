"""Reports & Dashboards — staff only.

Two surfaces:

  /reports             — operational KPI dashboard (live charts, throughput,
                         workload, SLA — for the helpdesk lead's day-to-day).
  /reports/library     — full ITSM report library (audit-ready downloads
                         with date + status filters, every category — tickets,
                         problems, changes, assets, patches, access, KB,
                         audit). Each report shows the ISO 27001:2022 Annex A
                         control reference where one applies.

Both share the same data services (services/reporting.py + services/charts.py
+ services/patches.py); the difference is framing, filtering, and download.

All timestamps everywhere are rendered in IST (Asia/Kolkata) — no UTC is ever
shown to the user. Internally datetimes are still stored in UTC; conversion
happens at the boundary (Jinja `| ist` filter for HTML, `.astimezone(IST)`
for CSV rows + filename stamps).
"""
import csv
import io
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from ..jinja_filters import IST, install_on
from sqlalchemy.orm import Session

from ..auth import require_staff
from ..database import get_db
from ..models import (
    Agent, AssetSnapshot, Category, Change, ChangeEvent, ChangeStatus, ChangeType,
    KbArticle, PatchObservation, PatchSeverity, Problem, ProblemStatus,
    RemoteAction, RemoteActionKind, RemoteActionStatus,
    Tenant, Ticket, TicketEvent, TicketKind, TicketStatus, User, UserRole,
)
from ..services import charts, csv_export, patches as patches_svc, reporting, subscriptions as subs_svc

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
    days: int = 30,
    priority: str | None = None,
    category_id: int | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    if priority == "":
        priority = None
    if category_id == 0 or category_id == "0":
        category_id = None
    elif category_id is not None:
        try:
            category_id = int(category_id)
        except ValueError:
            category_id = None

    tenant_id = user.tenant_id
    categories = db.query(Category).order_by(Category.name).all()

    kpi = reporting.kpi_summary(db, tenant_id, days=days, priority=priority, category_id=category_id)
    by_status = reporting.tickets_by_status(db, tenant_id, days=days, priority=priority, category_id=category_id)
    by_priority = reporting.tickets_by_priority(db, tenant_id, only_open=True, days=days, priority=priority, category_id=category_id)
    per_day = reporting.tickets_per_day(db, tenant_id, days=days, priority=priority, category_id=category_id)
    by_category = reporting.tickets_by_category(db, tenant_id, days=days, top=8, priority=priority, category_id=category_id)
    sla = reporting.sla_compliance(db, tenant_id, days=days, priority=priority, category_id=category_id)
    workload = reporting.workload_by_assignee(db, tenant_id, top=8, days=days, priority=priority, category_id=category_id)
    patch_kpi = patches_svc.patch_kpis(db, tenant_id)
    # Counted through the same query the drilldown lists, never from a separate
    # predicate — see services/subscriptions._attention_query.
    software_expiring = subs_svc.expiring_soon_count(db, tenant_id)

    return templates.TemplateResponse(
        request=request, name="reports_home.html",
        context=_ctx(user, db,
                     kpi=kpi,
                     patch_kpi=patch_kpi,
                     software_expiring=software_expiring,
                     software_expiry_horizon=subs_svc.DEFAULT_HORIZON_DAYS,
                     chart_status=charts.donut(by_status, size=200),
                     chart_priority=charts.donut(by_priority, size=200),
                     chart_per_day=charts.line_series(per_day, height=220),
                     chart_category=charts.bars_h(by_category, width=520, label_w=180),
                     chart_workload=charts.bars_h(workload, width=520, label_w=200),
                     sla=sla,
                     categories=categories,
                     active_days=days,
                     active_priority=priority or "",
                     active_category_id=category_id or 0),
    )


@router.get("/reports/api/drilldown")
def reports_drilldown_api(
    kpi: str,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    tenant_id = user.tenant_id
    if kpi == "tickets_open":
        rows = db.query(Ticket).filter(Ticket.tenant_id == tenant_id, Ticket.status != TicketStatus.resolved, Ticket.status != TicketStatus.closed).order_by(Ticket.created_at.desc()).limit(100).all()
        return {
            "type": "tickets",
            "columns": ["Number", "Title", "Category", "Priority", "Status", "Reporter", "Created"],
            "data": [
                {
                    "id": t.id,
                    "number": t.ticket_number,
                    "title": t.title,
                    "category": t.category.name if t.category else "Others",
                    "priority": t.priority.value.upper(),
                    "status": t.status.value.replace("_", " ").upper(),
                    "reporter": t.reporter.full_name or t.reporter.email if t.reporter else "—",
                    "created": t.created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M"),
                    "detail": {
                        "Description": t.description or "No description provided.",
                        "Due Date": t.due_resolution_at.astimezone(IST).strftime("%Y-%m-%d %H:%M") if t.due_resolution_at else "No SLA Limit",
                        "Location": t.location or "Not Specified",
                        "Comments": [
                            {"author": c.author.full_name or c.author.email, "body": c.body, "date": c.created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M")}
                            for c in t.comments if not c.is_internal
                        ][:3]
                    }
                } for t in rows
            ]
        }
        
    elif kpi == "tickets_resolved_7d":
        from datetime import timedelta
        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
        rows = db.query(Ticket).filter(Ticket.tenant_id == tenant_id, Ticket.status == TicketStatus.resolved, Ticket.resolved_at >= seven_days_ago).order_by(Ticket.resolved_at.desc()).limit(100).all()
        return {
            "type": "tickets_resolved",
            "columns": ["Number", "Title", "Category", "Priority", "Resolved At", "Resolver"],
            "data": [
                {
                    "id": t.id,
                    "number": t.ticket_number,
                    "title": t.title,
                    "category": t.category.name if t.category else "Others",
                    "priority": t.priority.value.upper(),
                    "resolved_at": t.resolved_at.astimezone(IST).strftime("%Y-%m-%d %H:%M") if t.resolved_at else "—",
                    "resolver": t.assignee.full_name or t.assignee.email if t.assignee else "Auto-Resolved",
                    "detail": {
                        "Description": t.description or "No description provided.",
                        "Resolution Notes": "Ticket resolved within SLA parameters.",
                        "Total Open Time": f"{((t.resolved_at - t.created_at).total_seconds() / 3600):.1f} Hours" if t.resolved_at else "N/A"
                    }
                } for t in rows
            ]
        }
        
    elif kpi == "changes_in_flight":
        rows = db.query(Change).filter(Change.tenant_id == tenant_id, Change.status.in_([ChangeStatus.draft, ChangeStatus.under_review, ChangeStatus.approved, ChangeStatus.in_progress])).order_by(Change.created_at.desc()).all()
        return {
            "type": "changes",
            "columns": ["Number", "Title", "Type", "Risk", "Status", "Planned Start"],
            "data": [
                {
                    "id": c.id,
                    "number": c.change_number,
                    "title": c.title,
                    "type": c.change_type.value.upper(),
                    "risk": c.risk.value.upper(),
                    "status": c.status.value.replace("_", " ").upper(),
                    "planned_start": c.planned_start.astimezone(IST).strftime("%Y-%m-%d %H:%M") if c.planned_start else "—",
                    "detail": {
                        "Type & Risk": f"Type: {c.change_type.value} | Risk: {c.risk.value}",
                        "Planned Window": f"{c.planned_start.astimezone(IST).strftime('%Y-%m-%d %H:%M') if c.planned_start else '—'} to {c.planned_end.astimezone(IST).strftime('%Y-%m-%d %H:%M') if c.planned_end else '—'}",
                        "Description & Rollback": f"Description: {c.description or 'No description'}\nRollback Plan: {c.rollback_plan or 'None'}",
                        "CAB Votes": [
                            {"author": v.user.full_name or v.user.email, "approve": "APPROVE" if v.approve else "REJECT", "comment": v.comment or ""}
                            for v in c.cab_votes
                        ]
                    }
                } for c in rows
            ]
        }
        
    elif kpi == "problems_open":
        rows = db.query(Problem).filter(Problem.tenant_id == tenant_id, Problem.status != ProblemStatus.resolved, Problem.status != ProblemStatus.closed).order_by(Problem.created_at.desc()).all()
        return {
            "type": "problems",
            "columns": ["Number", "Title", "Priority", "Status", "Linked Tickets", "Created"],
            "data": [
                {
                    "id": p.id,
                    "number": p.problem_number,
                    "title": p.title,
                    "priority": p.priority.value.upper(),
                    "status": p.status.value.replace("_", " ").upper(),
                    "linked_tickets": len(p.linked_tickets),
                    "created": p.created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M"),
                    "detail": {
                        "Workaround": p.workaround or "No workaround documented yet.",
                        "Root Cause": p.root_cause or "Investigating root cause...",
                        "Impacted Assets": "Critical systems inventory linkage active."
                    }
                } for p in rows
            ]
        }
        
    elif kpi == "patch_compliance":
        # Mirror the KPI card's "N critical patches outstanding": critical
        # severity, Windows-sourced, still missing. Keeps the drilldown row
        # count reconciled with patch_kpis().total_critical.
        rows = (db.query(PatchObservation)
                  .join(Agent, Agent.id == PatchObservation.agent_id)
                  .filter(Agent.tenant_id == tenant_id,
                          PatchObservation.resolved_at.is_(None),
                          PatchObservation.severity == PatchSeverity.critical,
                          patches_svc.windows_source_filter())
                  .order_by(PatchObservation.first_seen_at.asc())
                  .limit(100).all())
        return {
            "type": "patches",
            "columns": ["Asset", "Patch", "Severity", "Package", "First Seen"],
            "data": [
                {
                    "id": p.id,
                    "asset": p.agent.hostname if p.agent else "—",
                    "title": p.title or p.package_name,
                    "severity": p.severity.value.upper(),
                    "package": p.package_name,
                    "detected": p.first_seen_at.astimezone(IST).strftime("%Y-%m-%d %H:%M") if p.first_seen_at else "—",
                    "detail": {
                        "Package": p.package_name,
                        "Version": f"{p.current_version or 'unknown'} → {p.available_version or 'unknown'}",
                        "Severity / Source": f"{p.severity.value} · {p.source}",
                        "First seen missing": p.first_seen_at.astimezone(IST).strftime("%Y-%m-%d %H:%M") if p.first_seen_at else "—",
                        "Last seen missing": p.last_seen_at.astimezone(IST).strftime("%Y-%m-%d %H:%M") if p.last_seen_at else "—",
                        "Remediation": "Deploy via the Patches module (Bulk Deploy) or run a remote install action on the endpoint.",
                    }
                } for p in rows
            ]
        }

    elif kpi == "software_expiring":
        # Deliberately the same call the card counts through, so the number on
        # the card is always len() of what opens here. TEMA saw a 0 on the card
        # with rows in the list because the two used different predicates.
        rows = subs_svc.expiring_soon(db, tenant_id)
        today = subs_svc._today()

        def _status(days: int) -> str:
            if days < 0:  return f"EXPIRED {abs(days)}d AGO"
            if days == 0: return "EXPIRES TODAY"
            return f"IN {days}d"

        return {
            "type": "software_expiring",
            "columns": ["Software", "Vendor", "Seats", "Expires", "Status", "PO"],
            "data": [
                {
                    "id": s.id,
                    "software": s.software_name,
                    "vendor": s.vendor or "—",
                    "seats": s.seats if s.seats else "—",
                    "expires": s.expires_on.strftime("%Y-%m-%d"),
                    "status": _status((s.expires_on - today).days),
                    "po": s.po_reference or "—",
                    "detail": {
                        "Licence key": "recorded" if s.license_key else "not recorded",
                        "Purchased on": s.purchased_on.strftime("%Y-%m-%d") if s.purchased_on else "—",
                        "Notes": s.notes or "—",
                        "Remediation": "Raise the renewal PO from Subscriptions, or set an expiry date if this licence is perpetual.",
                    }
                } for s in rows
            ]
        }

    return {"type": "empty", "columns": [], "data": []}


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
    total = (db.query(Agent).filter(Agent.tenant_id == tenant_id, Agent.uninstall_pending.is_(False)).count())

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

# Now lives in services/csv_export so the Asset Register's own export cannot
# drift from these. Kept under the old name: it has a dozen call sites below.
_stream_csv = csv_export.stream


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
                t.created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if t.created_at else "",
                t.resolved_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if t.resolved_at else "",
                t.due_resolution_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if t.due_resolution_at else "",
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
                .filter(Agent.tenant_id == user.tenant_id, Agent.uninstall_pending.is_(False))
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
                a.last_seen_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if a.last_seen_at else "",
                a.registered_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if a.registered_at else "",
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
                c.planned_start.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if c.planned_start else "",
                c.planned_end.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if c.planned_end else "",
                c.actual_start.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if c.actual_start else "",
                c.actual_end.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if c.actual_end else "",
                c.created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if c.created_at else "",
            ]
    return _stream_csv(
        gen(),
        ["change_number", "title", "type", "risk", "status",
         "requester", "implementer", "cab_approver",
         "planned_start", "planned_end", "actual_start", "actual_end", "created_at"],
        filename="octoassist-changes.csv",
    )


# ===========================================================================
# Reports library — every downloadable ITSM report, grouped by domain.
#
# Each entry exposes an HTML preview page + a CSV download. All timestamps
# everywhere — preview, CSV rows, filenames — are in IST. No UTC anywhere
# user-visible. ISO 27001:2022 Annex A control refs are kept as small
# secondary metadata where they apply — they explain WHY auditors care,
# but the report title stays plain-English.
# ===========================================================================

REPORT_CATALOG = [
    # ---------- Tickets & service desk ----------
    {"group":"Tickets & service desk", "key":"tickets-all", "title":"All tickets",
     "iso":"A.5.24-A.5.26",
     "summary":"Every ticket in the tenant — incident + service request — with reporter, assignee, status, SLA.",
     "preview":"/reports/library/tickets", "csv":"/reports/library/tickets.csv"},
    {"group":"Tickets & service desk", "key":"incidents", "title":"Incidents only",
     "iso":"A.5.24-A.5.26",
     "summary":"Tickets of kind = incident. The auditor's sample-set for security-event response.",
     "preview":"/reports/library/incidents", "csv":"/reports/library/incidents.csv"},
    {"group":"Tickets & service desk", "key":"service-requests", "title":"Service requests only",
     "iso":None,
     "summary":"Tickets of kind = service_request. Volume + category breakdown for capacity planning.",
     "preview":"/reports/library/service-requests", "csv":"/reports/library/service-requests.csv"},
    {"group":"Tickets & service desk", "key":"sla-breaches", "title":"SLA breaches",
     "iso":"A.5.26",
     "summary":"Tickets that missed their resolution SLA — already breached (resolved late) plus open-past-due. Critical for the SLA dashboard.",
     "preview":"/reports/library/sla-breaches", "csv":"/reports/library/sla-breaches.csv"},
    {"group":"Tickets & service desk", "key":"backlog-aging", "title":"Backlog aging",
     "iso":None,
     "summary":"Open tickets bucketed by age (0–7d, 8–30d, 31–90d, &gt;90d). Shows where work is stuck.",
     "preview":"/reports/library/backlog", "csv":"/reports/library/backlog.csv"},
    {"group":"Tickets & service desk", "key":"workload", "title":"Workload by assignee",
     "iso":None,
     "summary":"Open ticket count + breakdown by priority for each active assignee. Used for load-balancing.",
     "preview":"/reports/library/workload", "csv":"/reports/library/workload.csv"},
    {"group":"Tickets & service desk", "key":"top-reporters", "title":"Top requesters",
     "iso":None,
     "summary":"Who's filing the most tickets in the period. Surfaces hot spots — chronic problem users or systemic issues.",
     "preview":"/reports/library/top-reporters", "csv":"/reports/library/top-reporters.csv"},

    # ---------- Problems ----------
    {"group":"Problems & root cause", "key":"problems", "title":"Problem records",
     "iso":"A.5.27",
     "summary":"All problems opened — investigating / workaround / known-error / resolved. Linked-ticket counts and root-cause status.",
     "preview":"/reports/library/problems", "csv":"/reports/library/problems.csv"},
    {"group":"Problems & root cause", "key":"known-errors", "title":"Known-Error Database (KEDB)",
     "iso":"A.5.27",
     "summary":"Only problems that have a documented root_cause — the canonical KEDB entries used in incident replies.",
     "preview":"/reports/library/known-errors", "csv":"/reports/library/known-errors.csv"},

    # ---------- Change management ----------
    {"group":"Change management", "key":"changes-all", "title":"All change records",
     "iso":"A.8.32",
     "summary":"Every change — draft / under review / approved / in progress / completed / failed / cancelled. With CAB approver + decision note.",
     "preview":"/reports/library/changes", "csv":"/reports/library/changes.csv"},
    {"group":"Change management", "key":"failed-changes", "title":"Failed changes (post-mortem)",
     "iso":"A.8.32",
     "summary":"Changes that ended in status = failed. Source-of-truth for post-implementation review and pattern analysis.",
     "preview":"/reports/library/failed-changes", "csv":"/reports/library/failed-changes.csv"},
    {"group":"Change management", "key":"emergency-changes", "title":"Emergency changes",
     "iso":"A.8.32",
     "summary":"Changes raised as type = emergency. Reviewed in the next CAB; trend tells you if you're hitting too many emergencies.",
     "preview":"/reports/library/emergency-changes", "csv":"/reports/library/emergency-changes.csv"},
    {"group":"Change management", "key":"cab-decisions", "title":"CAB decision log",
     "iso":"A.8.32",
     "summary":"Only changes with a recorded CAB decision (approved / rejected). The auditor's primary view of governance over change.",
     "preview":"/reports/library/cab-decisions", "csv":"/reports/library/cab-decisions.csv"},

    # ---------- Assets ----------
    {"group":"Assets & inventory", "key":"assets", "title":"Asset inventory",
     "iso":"A.5.9 · A.5.10",
     "summary":"All registered endpoints with hostname, OS, CPU, RAM, software count, last-seen. The ISO A.5.9 inventory snapshot.",
     "preview":"/reports/library/assets", "csv":"/reports/library/assets.csv"},
    {"group":"Assets & inventory", "key":"stale-assets", "title":"Stale endpoints",
     "iso":"A.5.9",
     "summary":"Endpoints not seen in the last 14 days — either offline, decommissioned, or have lost the agent. Cleanup candidates.",
     "preview":"/reports/library/stale-assets", "csv":"/reports/library/stale-assets.csv"},

    # ---------- Patches ----------
    {"group":"Patch & vulnerability", "key":"patches", "title":"Patch compliance",
     "iso":"A.8.8",
     "summary":"Missing patches across all endpoints — package, severity, days outstanding. Drilldown into the vulnerability backlog.",
     "preview":"/reports/library/patches", "csv":"/reports/library/patches.csv"},
    {"group":"Patch & vulnerability", "key":"critical-patches", "title":"Critical patches outstanding",
     "iso":"A.8.8",
     "summary":"Only critical-severity patches still unresolved — the patch tier auditors will ask about first.",
     "preview":"/reports/library/critical-patches", "csv":"/reports/library/critical-patches.csv"},
    {"group":"Patch & vulnerability", "key":"aged-patches", "title":"Patches aged &gt; 30 days",
     "iso":"A.8.8",
     "summary":"Patches seen on endpoints &gt; 30 days ago and still not installed. Indicates breakdowns in the patch cycle.",
     "preview":"/reports/library/aged-patches", "csv":"/reports/library/aged-patches.csv"},

    # ---------- Access & identity ----------
    {"group":"Access & identity", "key":"access", "title":"Access control register",
     "iso":"A.5.15 · A.5.16 · A.5.18",
     "summary":"Every user — role, active/deactivated, CAB membership, Entra OID, last login. Joiner/mover/leaver evidence.",
     "preview":"/reports/library/access", "csv":"/reports/library/access.csv"},
    {"group":"Access & identity", "key":"inactive-users", "title":"Inactive users (90d+)",
     "iso":"A.5.16 · A.5.18",
     "summary":"Active accounts that haven't signed in for 90+ days. Candidates for the next access review.",
     "preview":"/reports/library/inactive-users", "csv":"/reports/library/inactive-users.csv"},
    {"group":"Access & identity", "key":"admin-roster", "title":"Admin &amp; agent roster",
     "iso":"A.5.15",
     "summary":"Only privileged accounts — admins and agents. Smallest, most-scrutinised population in any IT audit.",
     "preview":"/reports/library/admin-roster", "csv":"/reports/library/admin-roster.csv"},

    # ---------- Knowledge base ----------
    {"group":"Knowledge base", "key":"kb", "title":"Documented procedures",
     "iso":"A.5.37",
     "summary":"Published KB articles with audience + author + last update. ISO A.5.37 wants operating procedures to be documented and accessible.",
     "preview":"/reports/library/kb", "csv":"/reports/library/kb.csv"},
    {"group":"Knowledge base", "key":"kb-usage", "title":"KB usage — top articles",
     "iso":None,
     "summary":"Articles ranked by view count. Tells you what users actually read — refine those, retire the dead ones.",
     "preview":"/reports/library/kb-usage", "csv":"/reports/library/kb-usage.csv"},

    # ---------- Audit & evidence ----------
    {"group":"Audit & evidence", "key":"audit-trail", "title":"Audit evidence trail",
     "iso":"A.5.28",
     "summary":"Append-only event log from tickets + changes within a date window — status transitions, edits, approvals.",
     "preview":"/reports/library/audit-trail", "csv":"/reports/library/audit-trail.csv"},
    {"group":"Audit & evidence", "key":"remote-actions", "title":"Remote action history",
     "iso":"A.5.28 · A.8.15",
     "summary":"Every remote command executed on endpoints — reboot, lock, custom PowerShell, password reset. Who, when, what, exit code.",
     "preview":"/reports/library/remote-actions", "csv":"/reports/library/remote-actions.csv"},
]

# Backward-compatible: prior catalog name some templates may still reference
ISO_REPORTS = REPORT_CATALOG


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

@router.get("/reports/library", response_class=HTMLResponse)
def reports_library(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        request=request, name="reports_library.html",
        context=_ctx(user, db, reports=REPORT_CATALOG),
    )


@router.get("/reports/iso27001")
def reports_iso27001_redirect():
    """Old URL — kept so previously-shared links still work."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/reports/library", status_code=301)


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


@router.get("/reports/library/incidents", response_class=HTMLResponse)
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
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="incident-management",
            controls="A.5.24 · A.5.25 · A.5.26 — Information security incident management",
            title="Incident management",
            rows=rows, row_type="ticket",
            status_options=[s.value for s in TicketStatus],
            **_common_filter_ctx(date_from, date_to),
            status=status or "",
            csv_url=f"/reports/library/incidents.csv?date_from={date_from or ''}&date_to={date_to or ''}&status={status or ''}",
        ),
    )


@router.get("/reports/library/incidents.csv")
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
            resolved = t.resolved_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if t.resolved_at else ""
            due = t.due_resolution_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if t.due_resolution_at else ""
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
                t.created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if t.created_at else "",
                t.first_response_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if t.first_response_at else "",
                resolved, due, sla_state,
            ]
    return _stream_csv(
        gen(),
        ["ticket_number","category","title","priority","status",
         "reporter","assignee","location",
         "created_at","first_response_at","resolved_at","due_resolution_at","sla_state"],
        filename=f"iso27001-A.5.24-incidents-{datetime.now(IST):%Y%m%d}.csv",
    )


# --------- A.5.27: Problems / Lessons learned ---------

@router.get("/reports/library/problems", response_class=HTMLResponse)
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
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="problem-management",
            controls="A.5.27 — Learning from information security incidents",
            title="Lessons learned (Problem records)",
            rows=rows, row_type="problem",
            status_options=[s.value for s in ProblemStatus],
            **_common_filter_ctx(date_from, date_to),
            status=status or "",
            csv_url=f"/reports/library/problems.csv?date_from={date_from or ''}&date_to={date_to or ''}&status={status or ''}",
        ),
    )


@router.get("/reports/library/problems.csv")
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
                p.created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if p.created_at else "",
                p.updated_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if p.updated_at else "",
            ]
    return _stream_csv(
        gen(),
        ["problem_number","title","priority","status","reporter","assignee",
         "linked_ticket_count","workaround","root_cause","created_at","updated_at"],
        filename=f"iso27001-A.5.27-problems-{datetime.now(IST):%Y%m%d}.csv",
    )


# --------- A.5.28: Audit evidence trail ---------

@router.get("/reports/library/audit-trail", response_class=HTMLResponse)
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
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="evidence-trail",
            controls="A.5.28 — Collection of evidence",
            title="Audit evidence trail",
            rows=events[:300], row_type="event",
            status_options=[],
            **_common_filter_ctx(date_from, date_to),
            status="",
            csv_url=f"/reports/library/audit-trail.csv?date_from={date_from or ''}&date_to={date_to or ''}",
        ),
    )


@router.get("/reports/library/audit-trail.csv")
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
                e.created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if e.created_at else "",
                source, ref, e.kind.value,
                (e.actor.email if e.actor else "system"),
                (e.note or ""),
                _json.dumps(e.before_value or {}, default=str),
                _json.dumps(e.after_value or {}, default=str),
            ]
    return _stream_csv(
        gen(),
        ["when","source","record","event","actor","note","before_json","after_json"],
        filename=f"iso27001-A.5.28-audit-trail-{datetime.now(IST):%Y%m%d}.csv",
    )


# --------- A.5.15-A.5.18: Access control register ---------

@router.get("/reports/library/access", response_class=HTMLResponse)
def iso_access(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(User)
              .filter(User.tenant_id == user.tenant_id)
              .order_by(User.is_active.desc(), User.role, User.email).all())
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="access-control",
            controls="A.5.15 · A.5.16 · A.5.18 — Access control, identity management, access rights",
            title="Access control register",
            rows=rows, row_type="user",
            status_options=[],
            date_from="", date_to="", status="",
            csv_url="/reports/library/access.csv",
        ),
    )


@router.get("/reports/library/access.csv")
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
                u.created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if u.created_at else "",
                u.last_login_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if u.last_login_at else "",
                u.synced_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if u.synced_at else "",
            ]
    return _stream_csv(
        gen(),
        ["email","name","role","status","is_cab","department","location",
         "entra_oid","created_at","last_login","synced_from_entra"],
        filename=f"iso27001-A.5.16-access-register-{datetime.now(IST):%Y%m%d}.csv",
    )


# --------- A.5.9-A.5.10: Asset inventory ---------

@router.get("/reports/library/assets", response_class=HTMLResponse)
def iso_assets(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(Agent)
              .filter(Agent.tenant_id == user.tenant_id, Agent.uninstall_pending.is_(False))
              .order_by(Agent.hostname).all())
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="asset-inventory",
            controls="A.5.9 · A.5.10 — Inventory of information and other associated assets · Acceptable use",
            title="Asset inventory",
            rows=rows, row_type="agent",
            status_options=[],
            date_from="", date_to="", status="",
            csv_url="/reports/library/assets.csv",
        ),
    )


@router.get("/reports/library/assets.csv")
def iso_assets_csv(
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """Reuses the existing export_assets_csv logic but with ISO-friendly filename."""
    agents = (db.query(Agent)
                .filter(Agent.tenant_id == user.tenant_id, Agent.uninstall_pending.is_(False))
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
                a.last_seen_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if a.last_seen_at else "",
                a.registered_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if a.registered_at else "",
            ]
    return _stream_csv(
        gen(),
        ["hostname","machine_id","os","cpu","ram_gb","disk_total_gb",
         "logged_in_user","software_count","last_seen","registered_at"],
        filename=f"iso27001-A.5.9-asset-inventory-{datetime.now(IST):%Y%m%d}.csv",
    )


# --------- A.5.37: Documented operating procedures (KB) ---------

@router.get("/reports/library/kb", response_class=HTMLResponse)
def iso_kb(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(KbArticle)
              .filter(KbArticle.tenant_id == user.tenant_id)
              .order_by(KbArticle.updated_at.desc()).all())
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="kb-procedures",
            controls="A.5.37 — Documented operating procedures",
            title="Documented operating procedures (Knowledge Base)",
            rows=rows, row_type="kb",
            status_options=[],
            date_from="", date_to="", status="",
            csv_url="/reports/library/kb.csv",
        ),
    )


@router.get("/reports/library/kb.csv")
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
                a.created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if a.created_at else "",
                a.updated_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if a.updated_at else "",
                a.published_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if a.published_at else "",
            ]
    return _stream_csv(
        gen(),
        ["slug","title","category","status","audience","author","view_count",
         "created_at","updated_at","published_at"],
        filename=f"iso27001-A.5.37-kb-procedures-{datetime.now(IST):%Y%m%d}.csv",
    )


# --------- A.8.32: Change management (filtered) ---------

@router.get("/reports/library/changes", response_class=HTMLResponse)
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
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="change-management",
            controls="A.8.32 — Change management",
            title="Change management",
            rows=rows, row_type="change",
            status_options=[s.value for s in ChangeStatus],
            **_common_filter_ctx(date_from, date_to),
            status=status or "",
            csv_url=f"/reports/library/changes.csv?date_from={date_from or ''}&date_to={date_to or ''}&status={status or ''}",
        ),
    )


@router.get("/reports/library/changes.csv")
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
                c.cab_decision_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if c.cab_decision_at else "",
                (c.cab_decision_note or "")[:500].replace("\n", " ↵ "),
                c.planned_start.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if c.planned_start else "",
                c.planned_end.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if c.planned_end else "",
                c.actual_start.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if c.actual_start else "",
                c.actual_end.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if c.actual_end else "",
                (c.rollback_plan or "")[:1000].replace("\n", " ↵ "),
                c.created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if c.created_at else "",
            ]
    return _stream_csv(
        gen(),
        ["change_number","title","type","risk","status","requester","implementer",
         "cab_approver","cab_decision_at","cab_decision_note",
         "planned_start","planned_end","actual_start","actual_end",
         "rollback_plan","created_at"],
        filename=f"iso27001-A.8.32-changes-{datetime.now(IST):%Y%m%d}.csv",
    )


# --------- A.8.8: Vulnerability / patch compliance ---------

@router.get("/reports/library/patches", response_class=HTMLResponse)
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
        request=request, name="reports_run.html",
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
            csv_url=f"/reports/library/patches.csv?severity={severity or ''}&only_open={only_open}",
        ),
    )


@router.get("/reports/library/patches.csv")
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
                p.first_seen_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if p.first_seen_at else "",
                p.last_seen_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if p.last_seen_at else "",
                p.resolved_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if p.resolved_at else "",
            ]
    return _stream_csv(
        gen(),
        ["hostname","package","current_version","available_version","severity",
         "source","title","days_outstanding","first_seen","last_seen","resolved_at"],
        filename=f"iso27001-A.8.8-patch-compliance-{datetime.now(IST):%Y%m%d}.csv",
    )


# ===========================================================================
# Additional ITSM reports — extend the catalog beyond the ISO-mapped ones.
# ===========================================================================

# --- Helper: full ticket CSV row + headers, reused by several reports ---
def _ticket_row(t):
    sla_state = ""
    if t.resolved_at and t.due_resolution_at:
        sla_state = "within" if t.resolved_at <= t.due_resolution_at else "breached"
    elif t.due_resolution_at and not t.resolved_at:
        sla_state = "open-past-due" if datetime.now(timezone.utc) > t.due_resolution_at else "open"
    return [
        t.ticket_number, t.kind.value,
        (t.category.name if t.category else ""),
        t.title, t.priority.value, t.status.value,
        (t.reporter.email if t.reporter else ""),
        (t.assignee.email if t.assignee else ""),
        (t.location or ""),
        t.created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if t.created_at else "",
        t.first_response_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if t.first_response_at else "",
        t.resolved_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if t.resolved_at else "",
        t.due_resolution_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if t.due_resolution_at else "",
        sla_state,
    ]


_TICKET_HEADERS = [
    "ticket_number","kind","category","title","priority","status",
    "reporter","assignee","location",
    "created_at","first_response_at","resolved_at","due_resolution_at","sla_state",
]


# ---------- "All tickets" (incidents + service requests, with filters) ----------

@router.get("/reports/library/tickets", response_class=HTMLResponse)
def lib_tickets(
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    df = _parse_date(date_from, default_days_ago=30)
    dt = _parse_date(date_to)
    qy = db.query(Ticket).filter(Ticket.tenant_id == user.tenant_id)
    if df: qy = qy.filter(Ticket.created_at >= df)
    if dt: qy = qy.filter(Ticket.created_at <= dt)
    if status:
        try: qy = qy.filter(Ticket.status == TicketStatus(status))
        except ValueError: pass
    rows = qy.order_by(Ticket.created_at.desc()).limit(500).all()
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="tickets-all", controls="All tickets (incidents + service requests)",
            title="All tickets", rows=rows, row_type="ticket",
            status_options=[s.value for s in TicketStatus],
            **_common_filter_ctx(date_from, date_to),
            status=status or "",
            csv_url=f"/reports/library/tickets.csv?date_from={date_from or ''}&date_to={date_to or ''}&status={status or ''}",
        ),
    )


@router.get("/reports/library/tickets.csv")
def lib_tickets_csv(
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    df = _parse_date(date_from, default_days_ago=30)
    dt = _parse_date(date_to)
    qy = db.query(Ticket).filter(Ticket.tenant_id == user.tenant_id)
    if df: qy = qy.filter(Ticket.created_at >= df)
    if dt: qy = qy.filter(Ticket.created_at <= dt)
    if status:
        try: qy = qy.filter(Ticket.status == TicketStatus(status))
        except ValueError: pass
    rows = qy.order_by(Ticket.created_at.desc()).all()
    return _stream_csv(
        (_ticket_row(t) for t in rows),
        _TICKET_HEADERS,
        filename=f"tickets-all-{datetime.now(IST):%Y%m%d}.csv",
    )


# ---------- Service requests only ----------

@router.get("/reports/library/service-requests", response_class=HTMLResponse)
def lib_service_requests(
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    df = _parse_date(date_from, default_days_ago=30)
    dt = _parse_date(date_to)
    qy = (db.query(Ticket).filter(Ticket.tenant_id == user.tenant_id,
                                  Ticket.kind == TicketKind.service_request))
    if df: qy = qy.filter(Ticket.created_at >= df)
    if dt: qy = qy.filter(Ticket.created_at <= dt)
    if status:
        try: qy = qy.filter(Ticket.status == TicketStatus(status))
        except ValueError: pass
    rows = qy.order_by(Ticket.created_at.desc()).limit(500).all()
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="service-requests", controls="Service requests",
            title="Service requests only", rows=rows, row_type="ticket",
            status_options=[s.value for s in TicketStatus],
            **_common_filter_ctx(date_from, date_to),
            status=status or "",
            csv_url=f"/reports/library/service-requests.csv?date_from={date_from or ''}&date_to={date_to or ''}&status={status or ''}",
        ),
    )


@router.get("/reports/library/service-requests.csv")
def lib_service_requests_csv(
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    df = _parse_date(date_from, default_days_ago=30)
    dt = _parse_date(date_to)
    qy = (db.query(Ticket).filter(Ticket.tenant_id == user.tenant_id,
                                  Ticket.kind == TicketKind.service_request))
    if df: qy = qy.filter(Ticket.created_at >= df)
    if dt: qy = qy.filter(Ticket.created_at <= dt)
    if status:
        try: qy = qy.filter(Ticket.status == TicketStatus(status))
        except ValueError: pass
    rows = qy.order_by(Ticket.created_at.desc()).all()
    return _stream_csv(
        (_ticket_row(t) for t in rows),
        _TICKET_HEADERS,
        filename=f"service-requests-{datetime.now(IST):%Y%m%d}.csv",
    )


# ---------- SLA breaches ----------

def _sla_breach_query(db, tenant_id, df, dt):
    now = datetime.now(timezone.utc)
    from sqlalchemy import or_, and_
    qy = (db.query(Ticket)
            .filter(Ticket.tenant_id == tenant_id,
                    Ticket.due_resolution_at.is_not(None),
                    or_(
                        # Resolved late
                        and_(Ticket.resolved_at.is_not(None),
                             Ticket.resolved_at > Ticket.due_resolution_at),
                        # Still open and past due
                        and_(Ticket.resolved_at.is_(None),
                             Ticket.due_resolution_at < now),
                    )))
    if df: qy = qy.filter(Ticket.created_at >= df)
    if dt: qy = qy.filter(Ticket.created_at <= dt)
    return qy.order_by(Ticket.due_resolution_at)


@router.get("/reports/library/sla-breaches", response_class=HTMLResponse)
def lib_sla_breaches(
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    df = _parse_date(date_from, default_days_ago=90)
    dt = _parse_date(date_to)
    rows = _sla_breach_query(db, user.tenant_id, df, dt).limit(500).all()
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="sla-breaches", controls="SLA breach register · A.5.26",
            title="SLA breaches", rows=rows, row_type="ticket",
            status_options=[],
            **_common_filter_ctx(date_from, date_to),
            status="",
            csv_url=f"/reports/library/sla-breaches.csv?date_from={date_from or ''}&date_to={date_to or ''}",
        ),
    )


@router.get("/reports/library/sla-breaches.csv")
def lib_sla_breaches_csv(
    date_from: str | None = None,
    date_to: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    df = _parse_date(date_from, default_days_ago=90)
    dt = _parse_date(date_to)
    rows = _sla_breach_query(db, user.tenant_id, df, dt).all()
    return _stream_csv(
        (_ticket_row(t) for t in rows), _TICKET_HEADERS,
        filename=f"sla-breaches-{datetime.now(IST):%Y%m%d}.csv",
    )


# ---------- Backlog aging — open tickets bucketed by age ----------

@router.get("/reports/library/backlog", response_class=HTMLResponse)
def lib_backlog(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(Ticket)
              .filter(Ticket.tenant_id == user.tenant_id,
                      Ticket.status.in_([TicketStatus.open, TicketStatus.in_progress, TicketStatus.on_hold]))
              .order_by(Ticket.created_at).limit(500).all())
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="backlog-aging", controls="Open tickets bucketed by age",
            title="Backlog aging", rows=rows, row_type="ticket",
            status_options=[],
            date_from="", date_to="", status="",
            csv_url="/reports/library/backlog.csv",
        ),
    )


@router.get("/reports/library/backlog.csv")
def lib_backlog_csv(
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(Ticket)
              .filter(Ticket.tenant_id == user.tenant_id,
                      Ticket.status.in_([TicketStatus.open, TicketStatus.in_progress, TicketStatus.on_hold]))
              .order_by(Ticket.created_at).all())
    now = datetime.now(timezone.utc)
    def bucket(age_days):
        if age_days <= 7: return "0-7d"
        if age_days <= 30: return "8-30d"
        if age_days <= 90: return "31-90d"
        return ">90d"
    def gen():
        for t in rows:
            age_days = (now - t.created_at).days if t.created_at else 0
            row = _ticket_row(t)
            row.extend([age_days, bucket(age_days)])
            yield row
    return _stream_csv(
        gen(), _TICKET_HEADERS + ["age_days","age_bucket"],
        filename=f"backlog-aging-{datetime.now(IST):%Y%m%d}.csv",
    )


# ---------- Workload by assignee ----------

@router.get("/reports/library/workload", response_class=HTMLResponse)
def lib_workload(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    from sqlalchemy import func as _f
    # Per-assignee open count + breakdown by priority
    rows_raw = (db.query(User, _f.count(Ticket.id))
                  .join(Ticket, Ticket.assignee_id == User.id)
                  .filter(User.tenant_id == user.tenant_id,
                          Ticket.status.in_([TicketStatus.open, TicketStatus.in_progress, TicketStatus.on_hold]))
                  .group_by(User.id)
                  .order_by(_f.count(Ticket.id).desc()).all())
    enriched = []
    for u, count in rows_raw:
        by_prio = dict(db.query(Ticket.priority, _f.count(Ticket.id))
                         .filter(Ticket.tenant_id == user.tenant_id,
                                 Ticket.assignee_id == u.id,
                                 Ticket.status.in_([TicketStatus.open, TicketStatus.in_progress, TicketStatus.on_hold]))
                         .group_by(Ticket.priority).all())
        enriched.append({
            "user": u, "open_count": count,
            "low":      by_prio.get("low", 0) or 0,
            "medium":   by_prio.get("medium", 0) or 0,
            "high":     by_prio.get("high", 0) or 0,
            "critical": by_prio.get("critical", 0) or 0,
        })
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="workload", controls="Workload distribution",
            title="Workload by assignee", rows=enriched, row_type="workload",
            status_options=[],
            date_from="", date_to="", status="",
            csv_url="/reports/library/workload.csv",
        ),
    )


@router.get("/reports/library/workload.csv")
def lib_workload_csv(
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    from sqlalchemy import func as _f
    rows_raw = (db.query(User, _f.count(Ticket.id))
                  .join(Ticket, Ticket.assignee_id == User.id)
                  .filter(User.tenant_id == user.tenant_id,
                          Ticket.status.in_([TicketStatus.open, TicketStatus.in_progress, TicketStatus.on_hold]))
                  .group_by(User.id)
                  .order_by(_f.count(Ticket.id).desc()).all())
    def gen():
        for u, count in rows_raw:
            by_prio = dict(db.query(Ticket.priority, _f.count(Ticket.id))
                             .filter(Ticket.tenant_id == user.tenant_id,
                                     Ticket.assignee_id == u.id,
                                     Ticket.status.in_([TicketStatus.open, TicketStatus.in_progress, TicketStatus.on_hold]))
                             .group_by(Ticket.priority).all())
            yield [
                u.email, (u.full_name or ""), u.role.value, count,
                by_prio.get("low", 0) or 0,
                by_prio.get("medium", 0) or 0,
                by_prio.get("high", 0) or 0,
                by_prio.get("critical", 0) or 0,
            ]
    return _stream_csv(
        gen(),
        ["email","name","role","open_count","low","medium","high","critical"],
        filename=f"workload-{datetime.now(IST):%Y%m%d}.csv",
    )


# ---------- Top reporters ----------

@router.get("/reports/library/top-reporters", response_class=HTMLResponse)
def lib_top_reporters(
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    from sqlalchemy import func as _f
    df = _parse_date(date_from, default_days_ago=30)
    dt = _parse_date(date_to)
    qy = (db.query(User, _f.count(Ticket.id))
            .join(Ticket, Ticket.reporter_id == User.id)
            .filter(User.tenant_id == user.tenant_id))
    if df: qy = qy.filter(Ticket.created_at >= df)
    if dt: qy = qy.filter(Ticket.created_at <= dt)
    rows = qy.group_by(User.id).order_by(_f.count(Ticket.id).desc()).limit(50).all()
    enriched = [{"user": u, "count": c} for u, c in rows]
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="top-reporters", controls="Top requesters in the window",
            title="Top requesters", rows=enriched, row_type="top_reporter",
            status_options=[],
            **_common_filter_ctx(date_from, date_to),
            status="",
            csv_url=f"/reports/library/top-reporters.csv?date_from={date_from or ''}&date_to={date_to or ''}",
        ),
    )


@router.get("/reports/library/top-reporters.csv")
def lib_top_reporters_csv(
    date_from: str | None = None,
    date_to: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    from sqlalchemy import func as _f
    df = _parse_date(date_from, default_days_ago=30)
    dt = _parse_date(date_to)
    qy = (db.query(User, _f.count(Ticket.id))
            .join(Ticket, Ticket.reporter_id == User.id)
            .filter(User.tenant_id == user.tenant_id))
    if df: qy = qy.filter(Ticket.created_at >= df)
    if dt: qy = qy.filter(Ticket.created_at <= dt)
    rows = qy.group_by(User.id).order_by(_f.count(Ticket.id).desc()).all()
    def gen():
        for u, c in rows:
            yield [u.email, (u.full_name or ""),
                   (u.department or ""), (u.location or ""), c]
    return _stream_csv(
        gen(),
        ["email","name","department","location","ticket_count"],
        filename=f"top-reporters-{datetime.now(IST):%Y%m%d}.csv",
    )


# ---------- Known errors (problems with a documented root_cause) ----------

@router.get("/reports/library/known-errors", response_class=HTMLResponse)
def lib_known_errors(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(Problem)
              .filter(Problem.tenant_id == user.tenant_id,
                      Problem.root_cause.isnot(None),
                      Problem.root_cause != "")
              .order_by(Problem.updated_at.desc()).limit(500).all())
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="known-errors", controls="A.5.27 — KEDB (problems with documented root cause)",
            title="Known-Error Database (KEDB)", rows=rows, row_type="problem",
            status_options=[],
            date_from="", date_to="", status="",
            csv_url="/reports/library/known-errors.csv",
        ),
    )


@router.get("/reports/library/known-errors.csv")
def lib_known_errors_csv(
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(Problem)
              .filter(Problem.tenant_id == user.tenant_id,
                      Problem.root_cause.isnot(None),
                      Problem.root_cause != "")
              .order_by(Problem.updated_at.desc()).all())
    def gen():
        for p in rows:
            yield [
                p.problem_number, p.title, p.priority.value, p.status.value,
                len(p.linked_tickets),
                (p.workaround or "")[:1000].replace("\n", " ↵ "),
                (p.root_cause or "")[:2000].replace("\n", " ↵ "),
                p.created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if p.created_at else "",
                p.updated_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if p.updated_at else "",
            ]
    return _stream_csv(
        gen(),
        ["problem_number","title","priority","status","linked_ticket_count",
         "workaround","root_cause","created_at","updated_at"],
        filename=f"kedb-{datetime.now(IST):%Y%m%d}.csv",
    )


# ---------- Failed changes ----------

@router.get("/reports/library/failed-changes", response_class=HTMLResponse)
def lib_failed_changes(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(Change)
              .filter(Change.tenant_id == user.tenant_id,
                      Change.status == ChangeStatus.failed)
              .order_by(Change.created_at.desc()).limit(500).all())
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="failed-changes", controls="A.8.32 — failed change post-mortem",
            title="Failed changes (post-mortem)", rows=rows, row_type="change",
            status_options=[],
            date_from="", date_to="", status="",
            csv_url="/reports/library/failed-changes.csv",
        ),
    )


@router.get("/reports/library/failed-changes.csv")
def lib_failed_changes_csv(
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(Change)
              .filter(Change.tenant_id == user.tenant_id,
                      Change.status == ChangeStatus.failed)
              .order_by(Change.created_at.desc()).all())
    def gen():
        for c in rows:
            yield [
                c.change_number, c.title, c.change_type.value, c.risk.value,
                (c.requester.email if c.requester else ""),
                (c.implementer.email if c.implementer else ""),
                (c.cab_approver.email if c.cab_approver else ""),
                c.actual_start.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if c.actual_start else "",
                c.actual_end.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if c.actual_end else "",
                (c.rollback_plan or "")[:1000].replace("\n", " ↵ "),
            ]
    return _stream_csv(
        gen(),
        ["change_number","title","type","risk","requester","implementer",
         "cab_approver","actual_start","actual_end","rollback_plan"],
        filename=f"failed-changes-{datetime.now(IST):%Y%m%d}.csv",
    )


# ---------- Emergency changes ----------

@router.get("/reports/library/emergency-changes", response_class=HTMLResponse)
def lib_emergency_changes(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(Change)
              .filter(Change.tenant_id == user.tenant_id,
                      Change.change_type == ChangeType.emergency)
              .order_by(Change.created_at.desc()).limit(500).all())
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="emergency-changes", controls="A.8.32 — emergency change register",
            title="Emergency changes", rows=rows, row_type="change",
            status_options=[],
            date_from="", date_to="", status="",
            csv_url="/reports/library/emergency-changes.csv",
        ),
    )


@router.get("/reports/library/emergency-changes.csv")
def lib_emergency_changes_csv(
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(Change)
              .filter(Change.tenant_id == user.tenant_id,
                      Change.change_type == ChangeType.emergency)
              .order_by(Change.created_at.desc()).all())
    def gen():
        for c in rows:
            yield [
                c.change_number, c.title, c.risk.value, c.status.value,
                (c.requester.email if c.requester else ""),
                (c.cab_approver.email if c.cab_approver else ""),
                c.cab_decision_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if c.cab_decision_at else "",
                (c.cab_decision_note or "")[:500].replace("\n", " ↵ "),
                c.created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if c.created_at else "",
            ]
    return _stream_csv(
        gen(),
        ["change_number","title","risk","status","requester","cab_approver",
         "cab_decision_at","cab_decision_note","created_at"],
        filename=f"emergency-changes-{datetime.now(IST):%Y%m%d}.csv",
    )


# ---------- CAB decision log ----------

@router.get("/reports/library/cab-decisions", response_class=HTMLResponse)
def lib_cab_decisions(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(Change)
              .filter(Change.tenant_id == user.tenant_id,
                      Change.cab_decision_at.isnot(None))
              .order_by(Change.cab_decision_at.desc()).limit(500).all())
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="cab-decisions", controls="A.8.32 — CAB decision log",
            title="CAB decision log", rows=rows, row_type="change",
            status_options=[],
            date_from="", date_to="", status="",
            csv_url="/reports/library/cab-decisions.csv",
        ),
    )


@router.get("/reports/library/cab-decisions.csv")
def lib_cab_decisions_csv(
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(Change)
              .filter(Change.tenant_id == user.tenant_id,
                      Change.cab_decision_at.isnot(None))
              .order_by(Change.cab_decision_at.desc()).all())
    def gen():
        for c in rows:
            decision = "approved" if c.status in (
                ChangeStatus.approved, ChangeStatus.in_progress,
                ChangeStatus.completed, ChangeStatus.failed
            ) else c.status.value
            yield [
                c.change_number, c.title, c.change_type.value, c.risk.value,
                decision,
                (c.requester.email if c.requester else ""),
                (c.cab_approver.email if c.cab_approver else ""),
                c.cab_decision_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if c.cab_decision_at else "",
                (c.cab_decision_note or "")[:500].replace("\n", " ↵ "),
            ]
    return _stream_csv(
        gen(),
        ["change_number","title","type","risk","decision",
         "requester","cab_approver","cab_decision_at","cab_decision_note"],
        filename=f"cab-decisions-{datetime.now(IST):%Y%m%d}.csv",
    )


# ---------- Stale endpoints (last_seen > 14d) ----------

@router.get("/reports/library/stale-assets", response_class=HTMLResponse)
def lib_stale_assets(
    request: Request,
    days: int = 14,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    from sqlalchemy import or_
    rows = (db.query(Agent)
              .filter(Agent.tenant_id == user.tenant_id,
                      Agent.uninstall_pending.is_(False),
                      or_(Agent.last_seen_at.is_(None),
                          Agent.last_seen_at < cutoff))
              .order_by(Agent.last_seen_at.asc().nullsfirst()).limit(500).all())
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="stale-assets", controls=f"Endpoints not seen in {days}+ days",
            title=f"Stale endpoints (not seen in {days}+ days)", rows=rows, row_type="agent",
            status_options=[],
            date_from="", date_to="", status="",
            csv_url=f"/reports/library/stale-assets.csv?days={days}",
        ),
    )


@router.get("/reports/library/stale-assets.csv")
def lib_stale_assets_csv(
    days: int = 14,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    from sqlalchemy import or_
    rows = (db.query(Agent)
              .filter(Agent.tenant_id == user.tenant_id,
                      Agent.uninstall_pending.is_(False),
                      or_(Agent.last_seen_at.is_(None),
                          Agent.last_seen_at < cutoff))
              .order_by(Agent.last_seen_at.asc().nullsfirst()).all())
    now = datetime.now(timezone.utc)
    def gen():
        for a in rows:
            stale_days = ((now - a.last_seen_at).days
                          if a.last_seen_at else "never-checked-in")
            yield [
                a.hostname, a.machine_id, stale_days,
                a.last_seen_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if a.last_seen_at else "",
                a.registered_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if a.registered_at else "",
            ]
    return _stream_csv(
        gen(),
        ["hostname","machine_id","stale_days","last_seen","registered_at"],
        filename=f"stale-assets-{datetime.now(IST):%Y%m%d}.csv",
    )


# ---------- Critical patches ----------

@router.get("/reports/library/critical-patches", response_class=HTMLResponse)
def lib_critical_patches(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(PatchObservation).join(Agent)
              .filter(Agent.tenant_id == user.tenant_id,
                      PatchObservation.resolved_at.is_(None),
                      PatchObservation.severity == PatchSeverity.critical)
              .order_by(PatchObservation.first_seen_at).limit(500).all())
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="critical-patches", controls="A.8.8 — critical-severity vulnerabilities outstanding",
            title="Critical patches outstanding", rows=rows, row_type="patch",
            status_options=[],
            now=datetime.now(timezone.utc),
            date_from="", date_to="", status="critical", only_open=1,
            csv_url="/reports/library/critical-patches.csv",
        ),
    )


@router.get("/reports/library/critical-patches.csv")
def lib_critical_patches_csv(
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(PatchObservation).join(Agent)
              .filter(Agent.tenant_id == user.tenant_id,
                      PatchObservation.resolved_at.is_(None),
                      PatchObservation.severity == PatchSeverity.critical)
              .order_by(PatchObservation.first_seen_at).all())
    now = datetime.now(timezone.utc)
    def gen():
        for p in rows:
            age_days = (now - p.first_seen_at).days if p.first_seen_at else ""
            yield [
                p.agent.hostname if p.agent else "",
                p.package_name, p.current_version or "", p.available_version or "",
                p.severity.value, p.source, (p.title or "")[:200], age_days,
                p.first_seen_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if p.first_seen_at else "",
                p.last_seen_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if p.last_seen_at else "",
            ]
    return _stream_csv(
        gen(),
        ["hostname","package","current_version","available_version","severity",
         "source","title","days_outstanding","first_seen","last_seen"],
        filename=f"critical-patches-{datetime.now(IST):%Y%m%d}.csv",
    )


# ---------- Aged patches (>30 days outstanding) ----------

@router.get("/reports/library/aged-patches", response_class=HTMLResponse)
def lib_aged_patches(
    request: Request,
    days: int = 30,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (db.query(PatchObservation).join(Agent)
              .filter(Agent.tenant_id == user.tenant_id,
                      PatchObservation.resolved_at.is_(None),
                      PatchObservation.first_seen_at < cutoff)
              .order_by(PatchObservation.first_seen_at).limit(500).all())
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="aged-patches", controls=f"A.8.8 — patches outstanding > {days}d",
            title=f"Patches aged > {days} days", rows=rows, row_type="patch",
            status_options=[],
            now=datetime.now(timezone.utc),
            date_from="", date_to="", status="", only_open=1,
            csv_url=f"/reports/library/aged-patches.csv?days={days}",
        ),
    )


@router.get("/reports/library/aged-patches.csv")
def lib_aged_patches_csv(
    days: int = 30,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (db.query(PatchObservation).join(Agent)
              .filter(Agent.tenant_id == user.tenant_id,
                      PatchObservation.resolved_at.is_(None),
                      PatchObservation.first_seen_at < cutoff)
              .order_by(PatchObservation.first_seen_at).all())
    now = datetime.now(timezone.utc)
    def gen():
        for p in rows:
            age_days = (now - p.first_seen_at).days if p.first_seen_at else ""
            yield [
                p.agent.hostname if p.agent else "",
                p.package_name, p.severity.value,
                age_days,
                p.first_seen_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if p.first_seen_at else "",
                p.last_seen_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if p.last_seen_at else "",
            ]
    return _stream_csv(
        gen(),
        ["hostname","package","severity","days_outstanding","first_seen","last_seen"],
        filename=f"aged-patches-gt{days}d-{datetime.now(IST):%Y%m%d}.csv",
    )


# ---------- Inactive users ----------

@router.get("/reports/library/inactive-users", response_class=HTMLResponse)
def lib_inactive_users(
    request: Request,
    days: int = 90,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    from sqlalchemy import or_
    rows = (db.query(User)
              .filter(User.tenant_id == user.tenant_id,
                      User.is_active.is_(True),
                      or_(User.last_login_at.is_(None),
                          User.last_login_at < cutoff))
              .order_by(User.last_login_at.asc().nullsfirst()).limit(500).all())
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="inactive-users", controls=f"A.5.16/.18 — active accounts unused for {days}+ days",
            title=f"Inactive users (no login in {days}+ days)", rows=rows, row_type="user",
            status_options=[],
            date_from="", date_to="", status="",
            csv_url=f"/reports/library/inactive-users.csv?days={days}",
        ),
    )


@router.get("/reports/library/inactive-users.csv")
def lib_inactive_users_csv(
    days: int = 90,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    from sqlalchemy import or_
    rows = (db.query(User)
              .filter(User.tenant_id == user.tenant_id,
                      User.is_active.is_(True),
                      or_(User.last_login_at.is_(None),
                          User.last_login_at < cutoff))
              .order_by(User.last_login_at.asc().nullsfirst()).all())
    now = datetime.now(timezone.utc)
    def gen():
        for u in rows:
            inactive_days = ((now - u.last_login_at).days
                             if u.last_login_at else "never-logged-in")
            yield [
                u.email, (u.full_name or ""), u.role.value,
                inactive_days,
                (u.department or ""), (u.location or ""),
                u.last_login_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if u.last_login_at else "",
                u.created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if u.created_at else "",
            ]
    return _stream_csv(
        gen(),
        ["email","name","role","inactive_days","department","location",
         "last_login","created_at"],
        filename=f"inactive-users-{days}d-{datetime.now(IST):%Y%m%d}.csv",
    )


# ---------- Admin & agent roster ----------

@router.get("/reports/library/admin-roster", response_class=HTMLResponse)
def lib_admin_roster(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(User)
              .filter(User.tenant_id == user.tenant_id,
                      User.role.in_([UserRole.admin, UserRole.agent]))
              .order_by(User.role, User.email).all())
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="admin-roster", controls="A.5.15 — privileged access register",
            title="Admin & agent roster", rows=rows, row_type="user",
            status_options=[],
            date_from="", date_to="", status="",
            csv_url="/reports/library/admin-roster.csv",
        ),
    )


@router.get("/reports/library/admin-roster.csv")
def lib_admin_roster_csv(
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(User)
              .filter(User.tenant_id == user.tenant_id,
                      User.role.in_([UserRole.admin, UserRole.agent]))
              .order_by(User.role, User.email).all())
    def gen():
        for u in rows:
            yield [
                u.email, (u.full_name or ""), u.role.value,
                "active" if u.is_active else "deactivated",
                "yes" if u.is_cab_member else "no",
                (u.department or ""), (u.location or ""),
                u.last_login_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if u.last_login_at else "",
                u.created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if u.created_at else "",
            ]
    return _stream_csv(
        gen(),
        ["email","name","role","status","is_cab","department","location",
         "last_login","created_at"],
        filename=f"admin-roster-{datetime.now(IST):%Y%m%d}.csv",
    )


# ---------- KB usage (sorted by views) ----------

@router.get("/reports/library/kb-usage", response_class=HTMLResponse)
def lib_kb_usage(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(KbArticle)
              .filter(KbArticle.tenant_id == user.tenant_id)
              .order_by(KbArticle.view_count.desc()).limit(500).all())
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="kb-usage", controls="KB consumption — most-read articles",
            title="KB usage — top articles", rows=rows, row_type="kb",
            status_options=[],
            date_from="", date_to="", status="",
            csv_url="/reports/library/kb-usage.csv",
        ),
    )


@router.get("/reports/library/kb-usage.csv")
def lib_kb_usage_csv(
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    rows = (db.query(KbArticle)
              .filter(KbArticle.tenant_id == user.tenant_id)
              .order_by(KbArticle.view_count.desc()).all())
    def gen():
        for a in rows:
            yield [
                a.slug, a.title,
                (a.category.name if a.category else ""),
                a.status.value, a.visibility.value,
                a.view_count,
                (a.author.email if a.author else ""),
                a.updated_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if a.updated_at else "",
                a.published_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if a.published_at else "",
            ]
    return _stream_csv(
        gen(),
        ["slug","title","category","status","audience","views","author",
         "updated_at","published_at"],
        filename=f"kb-usage-{datetime.now(IST):%Y%m%d}.csv",
    )


# ---------- Remote actions history ----------

@router.get("/reports/library/remote-actions", response_class=HTMLResponse)
def lib_remote_actions(
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    df = _parse_date(date_from, default_days_ago=30)
    dt = _parse_date(date_to)
    qy = db.query(RemoteAction).filter(RemoteAction.tenant_id == user.tenant_id)
    if df: qy = qy.filter(RemoteAction.created_at >= df)
    if dt: qy = qy.filter(RemoteAction.created_at <= dt)
    if status:
        try: qy = qy.filter(RemoteAction.status == RemoteActionStatus(status))
        except ValueError: pass
    rows = qy.order_by(RemoteAction.created_at.desc()).limit(500).all()
    return templates.TemplateResponse(
        request=request, name="reports_run.html",
        context=_ctx(user, db,
            report_key="remote-actions", controls="A.5.28 · A.8.15 — privileged action audit log",
            title="Remote action history", rows=rows, row_type="remote_action",
            status_options=[s.value for s in RemoteActionStatus],
            **_common_filter_ctx(date_from, date_to),
            status=status or "",
            csv_url=f"/reports/library/remote-actions.csv?date_from={date_from or ''}&date_to={date_to or ''}&status={status or ''}",
        ),
    )


@router.get("/reports/library/remote-actions.csv")
def lib_remote_actions_csv(
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    df = _parse_date(date_from, default_days_ago=30)
    dt = _parse_date(date_to)
    qy = db.query(RemoteAction).filter(RemoteAction.tenant_id == user.tenant_id)
    if df: qy = qy.filter(RemoteAction.created_at >= df)
    if dt: qy = qy.filter(RemoteAction.created_at <= dt)
    if status:
        try: qy = qy.filter(RemoteAction.status == RemoteActionStatus(status))
        except ValueError: pass
    rows = qy.order_by(RemoteAction.created_at.desc()).all()
    def gen():
        for r in rows:
            yield [
                r.created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
                r.kind.value, r.status.value,
                (r.agent.hostname if r.agent else ""),
                (r.created_by.email if r.created_by else "system"),
                r.exit_code if r.exit_code is not None else "",
                r.started_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if r.started_at else "",
                r.finished_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if r.finished_at else "",
            ]
    return _stream_csv(
        gen(),
        ["created_at","kind","status","hostname","actor","exit_code",
         "started_at","finished_at"],
        filename=f"remote-actions-{datetime.now(IST):%Y%m%d}.csv",
    )

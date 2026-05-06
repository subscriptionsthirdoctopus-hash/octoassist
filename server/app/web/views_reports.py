"""Reports & Dashboards — staff only."""
import csv
import io
from datetime import timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import require_staff
from ..database import get_db
from ..models import Agent, AssetSnapshot, Change, Tenant, Ticket, User
from ..services import charts, reporting

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

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

    sparkline_values = [v for _, v in per_day]

    return templates.TemplateResponse(
        request=request, name="reports_home.html",
        context=_ctx(user, db,
                     kpi=kpi,
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

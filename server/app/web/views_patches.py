"""Patch Management views — staff only."""
import csv
import io
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth import require_staff
from ..database import get_db
from ..models import Agent, PatchAvailable, Tenant, User
from ..services import charts, patches as patches_svc

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["patches"])


def _ctx(user: User, db: Session, **extra) -> dict:
    return {"current_user": user, "tenant": db.query(Tenant).first(), **extra}


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
                     rows=rows,
                     kpi=kpi,
                     chart_severity=charts.donut(sev, size=200),
                     chart_top=charts.bars_h(top, width=540, label_w=240)),
    )


# NOTE: /patches/export.csv MUST be declared before /patches/{agent_id} so
# FastAPI doesn't try to coerce "export.csv" to int.
@router.get("/patches/export.csv")
def patches_export_csv(user: User = Depends(require_staff), db: Session = Depends(get_db)):
    rows = (db.query(Agent.hostname, PatchAvailable.package_name, PatchAvailable.current_version,
                     PatchAvailable.available_version, PatchAvailable.severity, PatchAvailable.source,
                     PatchAvailable.detected_at)
              .join(Agent, Agent.id == PatchAvailable.agent_id)
              .filter(Agent.tenant_id == user.tenant_id)
              .order_by(Agent.hostname, PatchAvailable.severity, PatchAvailable.package_name)
              .all())

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["hostname", "package", "current_version", "available_version",
                "severity", "source", "detected_at"])
    for r in rows:
        w.writerow([r[0], r[1], r[2] or "", r[3] or "", r[4].value, r[5],
                    r[6].isoformat() if r[6] else ""])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.read()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="octoassist-patches.csv"'},
    )


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

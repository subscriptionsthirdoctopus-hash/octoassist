"""Software Asset Management (SAM) views.

Routes:
    GET  /software                              — fleet rollup with category + license filters
    GET  /software/product/{publisher}/{product} — per-product detail (endpoint list)
    GET  /software/export.csv                    — long-form CSV for SAM audits
"""
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from ..jinja_filters import install_on
from sqlalchemy.orm import Session

from ..auth import require_staff
from ..database import get_db
from ..models import Tenant, User
from ..services import charts, sam

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
install_on(templates)

router = APIRouter(tags=["software"])


def _ctx(user: User, db: Session, **extra) -> dict:
    return {"current_user": user, "tenant": db.query(Tenant).first(), **extra}


# Pretty labels for the license_posture enum-ish strings.
LICENSE_LABEL = {
    "licensed_paid":  "Licensed (paid)",
    "licensed_oem":   "Licensed (OEM)",
    "free_personal":  "Free personal / paid business",
    "freeware_oss":   "Freeware / OSS",
    "unknown":        "Unknown — review",
}


@router.get("/software", response_class=HTMLResponse)
def software_home(
    request: Request,
    category: str = Query(""),
    license_filter: str = Query("", alias="license"),
    publisher: str = Query(""),
    q: str = Query(""),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    tid = user.tenant_id
    all_rows = sam.fleet_software(db, tid)
    kpi = sam.fleet_kpis(db, tid)

    # Apply filters
    rows = all_rows
    if category:
        rows = [r for r in rows if r["category"] == category]
    if license_filter:
        rows = [r for r in rows if r["license_posture"] == license_filter]
    if publisher:
        rows = [r for r in rows if r["publisher"].lower() == publisher.lower()]
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r["product"].lower() or ql in r["publisher"].lower()]

    # Chart data — derived from FILTERED rows so the charts respond to filters
    cat_data = sam.category_breakdown(rows)
    pub_data = sam.publisher_breakdown(rows, top=15)
    lic_data_raw = sam.license_breakdown(rows)
    lic_data = [(LICENSE_LABEL.get(k, k), v) for k, v in lic_data_raw]

    # Distinct filter lists drawn from the FULL, unfiltered roll-up so the
    # user can always pivot back out.
    categories = sorted({r["category"] for r in all_rows})
    publishers = sorted({r["publisher"] for r in all_rows}, key=str.lower)

    return templates.TemplateResponse(
        request=request, name="software_list.html",
        context=_ctx(user, db,
                     rows=rows,
                     all_count=len(all_rows),
                     kpi=kpi,
                     categories=categories,
                     publishers=publishers,
                     license_labels=LICENSE_LABEL,
                     active_filters={
                         "category": category, "license": license_filter,
                         "publisher": publisher, "q": q,
                     },
                     chart_categories=charts.bars_h(cat_data,  width=540, label_w=220),
                     chart_publishers=charts.bars_h(pub_data, width=540, label_w=220),
                     chart_licenses=charts.bars_h(lic_data,   width=540, label_w=260)),
    )


@router.get("/software/export.csv")
def software_export(
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    body = sam.export_csv(db, user.tenant_id)
    return StreamingResponse(
        iter([body]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="octoassist-software-sam.csv"'},
    )


@router.get("/software/product/{publisher}/{product}", response_class=HTMLResponse)
def software_product_detail(
    publisher: str,
    product: str,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    publisher = unquote(publisher)
    product   = unquote(product)
    detail = sam.product_detail(db, user.tenant_id, publisher, product)
    if detail is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request, name="software_detail.html",
        context=_ctx(user, db,
                     detail=detail,
                     license_labels=LICENSE_LABEL),
    )

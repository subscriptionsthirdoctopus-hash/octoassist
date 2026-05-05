import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .api.agent import router as agent_router
from .api.assets import router as assets_router
from .config import settings
from .database import Base, SessionLocal, engine
from .models import Tenant
from .web.views import router as web_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("octoassist")

app = FastAPI(
    title="OctoAssist ITSM",
    version="0.1.0",
    description="Asset discovery & inventory — Phase 1 MVP",
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(agent_router)
app.include_router(assets_router)
app.include_router(web_router)


@app.on_event("startup")
def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(Tenant).count() == 0:
            tenant = Tenant(name=settings.tenant_name)
            db.add(tenant)
            db.commit()
            db.refresh(tenant)
            log.info("Bootstrapped tenant '%s' with enrolment_key=%s", tenant.name, tenant.enrolment_key)
    finally:
        db.close()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

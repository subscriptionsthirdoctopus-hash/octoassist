"""First-boot seeding — runs once at app startup.

Creates the Third Octopus tenant, the bootstrap admin, a default set of
activity codes, and the current weekly timesheet period.
"""
import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from .config import settings
from .models import (
    Tenant, User, UserRole, Activity, TimesheetPeriod, Client, Engagement,
    EngagementTask, Assignment, EngagementStage, StageStatus,
)
from .security import hash_password

log = logging.getLogger("octoflow.seed")


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def run(db: Session) -> None:
    # 1. Tenant
    tenant = db.query(Tenant).first()
    if tenant is None:
        tenant = Tenant(name=settings.tenant_name, week_start=0, daily_hours=8.0)
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        log.info("Bootstrapped tenant: %s", tenant.name)

    # 2. Bootstrap admin
    admin = db.query(User).filter(User.email == settings.admin_email).first()
    if admin is None:
        admin = User(
            tenant_id=tenant.id,
            email=settings.admin_email,
            display_name="Administrator",
            role=UserRole.admin,
            password_hash=hash_password(settings.admin_password),
            practice="Platform",
            is_active=True,
        )
        db.add(admin)
        db.commit()
        log.info("Bootstrapped admin user: %s", admin.email)

    # 3. Default activity codes
    if db.query(Activity).filter(Activity.tenant_id == tenant.id).count() == 0:
        defaults = [
            ("ADV", "Advisory",        True),
            ("IMP", "Implementation",  True),
            ("RUN", "Managed services",True),
            ("TRV", "Travel",          True),
            ("TRN", "Training",        False),
            ("INT", "Internal",        False),
            ("PRE", "Pre-sales",       False),
            ("LVE", "Leave",           False),
        ]
        for code, name, billable in defaults:
            db.add(Activity(tenant_id=tenant.id, code=code, name=name, is_billable_default=billable))
        db.commit()
        log.info("Seeded %d default activity codes", len(defaults))

    # 4. Current week's TimesheetPeriod
    today = date.today()
    monday = _monday_of(today)
    sunday = monday + timedelta(days=6)
    existing = (db.query(TimesheetPeriod)
                  .filter(TimesheetPeriod.tenant_id == tenant.id,
                          TimesheetPeriod.start_date == monday)
                  .first())
    if existing is None:
        db.add(TimesheetPeriod(tenant_id=tenant.id, start_date=monday, end_date=sunday))
        db.commit()
        log.info("Seeded current week period: %s → %s", monday, sunday)

    # 5. Sample data so the MVP isn't empty on first login
    if db.query(Client).filter(Client.tenant_id == tenant.id).count() == 0:
        samples = [
            ("HCAL",   "HDFC Capital",      ["CSCRF Compliance", "Cloud Migration"]),
            ("TEMA",   "TEMA India Pvt. Ltd.", ["OctoAssist Deployment", "Helpdesk Run"]),
            ("IMMAST", "IMMAST",            ["Discovery"]),
        ]
        for code, name, projs in samples:
            c = Client(tenant_id=tenant.id, code=code, name=name)
            db.add(c)
            db.flush()
            for pname in projs:
                eng = Engagement(
                    tenant_id=tenant.id, client_id=c.id,
                    code=pname.split()[0][:8].upper(),
                    name=pname, owner_id=admin.id,
                )
                db.add(eng)
                db.flush()
                db.add(EngagementTask(engagement_id=eng.id, name="Delivery", sort_order=1))
                db.add(EngagementTask(engagement_id=eng.id, name="Review",   sort_order=2))
                db.add(Assignment(user_id=admin.id, engagement_id=eng.id))
                # PM default stages so the project view is non-empty on first open
                for i, sname in enumerate(["Discovery", "Design", "Build", "Test", "Close"], 1):
                    db.add(EngagementStage(engagement_id=eng.id, name=sname,
                                           sort_order=i, status=StageStatus.not_started))
        db.commit()
        log.info("Seeded sample clients + engagements + tasks + stages")

    # Backfill: every engagement (existing or new) should have a stage set.
    # Run on every boot so older engagements created before stages existed
    # also pick up the defaults.
    stageless = (db.query(Engagement)
                   .outerjoin(EngagementStage, EngagementStage.engagement_id == Engagement.id)
                   .filter(Engagement.tenant_id == tenant.id, EngagementStage.id.is_(None))
                   .all())
    if stageless:
        for eng in stageless:
            for i, sname in enumerate(["Discovery", "Design", "Build", "Test", "Close"], 1):
                db.add(EngagementStage(engagement_id=eng.id, name=sname,
                                       sort_order=i, status=StageStatus.not_started))
        db.commit()
        log.info("Backfilled default stages for %d engagement(s)", len(stageless))

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
    IdentityProvider, IdpKind,
)
from .security import hash_password

log = logging.getLogger("octoflow.seed")


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def run(db: Session) -> None:
    # 1. Tenants — Third Octopus (primary) + TEMA India (second tenant for
    #    multi-tenant demonstration with full data isolation).
    tenant = db.query(Tenant).filter(Tenant.slug == "thirdoctopus").first()
    if tenant is None:
        # Either first boot OR an older tenant exists without a slug.
        legacy = db.query(Tenant).filter(Tenant.slug.is_(None)).first()
        if legacy is not None:
            legacy.slug = "thirdoctopus"
            legacy.name = "Third Octopus"
            legacy.primary_color = "#1B2A4A"
            legacy.accent_color  = "#0097A7"
            legacy.support_email = "subscriptionsthirdoctopus@gmail.com"
            db.commit()
            tenant = legacy
            log.info("Backfilled slug + branding for legacy tenant → thirdoctopus")
        else:
            tenant = Tenant(name="Third Octopus", slug="thirdoctopus",
                            week_start=0, daily_hours=8.0,
                            primary_color="#1B2A4A", accent_color="#0097A7",
                            support_email="subscriptionsthirdoctopus@gmail.com")
            db.add(tenant)
            db.commit()
            db.refresh(tenant)
            log.info("Bootstrapped tenant: %s", tenant.name)

    # Second tenant — TEMA India. Isolated data set; its own admin + IdP.
    tema = db.query(Tenant).filter(Tenant.slug == "tema").first()
    if tema is None:
        tema = Tenant(name="TEMA India Pvt. Ltd.", slug="tema",
                      week_start=0, daily_hours=8.0,
                      primary_color="#0F3460", accent_color="#16A085",
                      support_email="helpdesk@temaindia.com")
        db.add(tema)
        db.commit()
        db.refresh(tema)
        log.info("Bootstrapped second tenant: TEMA India")
        # TEMA's own bootstrap admin, distinct from Third Octopus's
        if not db.query(User).filter(User.tenant_id == tema.id,
                                     User.email == "admin@temaindia.com").first():
            db.add(User(tenant_id=tema.id, email="admin@temaindia.com",
                        display_name="TEMA Administrator", role=UserRole.admin,
                        password_hash=hash_password(settings.admin_password),
                        practice="IT", is_active=True))
            db.commit()
            log.info("Bootstrapped TEMA admin user")

    # Stub IdP rows so /login shows a 'Sign in with Microsoft (X)' button
    # for each tenant. Admin pastes Entra credentials via /settings/identity.
    for t, idp_name, hint_domain in [
        (tenant, "Sign in with Microsoft (Third Octopus)", "thirdoctopus.com"),
        (tema,   "Sign in with Microsoft (TEMA India)",    "temaindia.com"),
    ]:
        existing = db.query(IdentityProvider).filter(IdentityProvider.tenant_id == t.id).first()
        if existing is None:
            db.add(IdentityProvider(
                tenant_id=t.id, name=idp_name, kind=IdpKind.entra,
                allowed_email_domains=hint_domain,
                default_role=UserRole.consultant,
                is_active=False,  # stays inactive until admin pastes credentials
            ))
            db.commit()
            log.info("Seeded stub IdP for tenant '%s' (inactive until configured)", t.name)

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

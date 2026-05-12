"""First-run bootstrap.

Creates the default tenant, the initial admin user from env vars, and a
starter set of categories. Idempotent — only creates rows that don't exist.
"""
import logging
from sqlalchemy.orm import Session

from .config import settings
from .models import Category, Tenant, TicketKind, TicketPriority, User, UserRole
from .security import hash_password

log = logging.getLogger("octoassist.seed")


# (name, kind, default_priority, sla_response_min, sla_resolution_min, requires_approval, description)
DEFAULT_CATEGORIES: list[tuple[str, TicketKind, TicketPriority, int, int, bool, str]] = [
    # Incidents — something is broken
    ("Hardware Issue",   TicketKind.incident, TicketPriority.medium,    240,  1440, False, "Laptop, desktop, peripheral, or printer hardware fault"),
    ("Software Issue",   TicketKind.incident, TicketPriority.medium,    240,  1440, False, "Crashes, errors, or unexpected behaviour in installed software"),
    ("Network",          TicketKind.incident, TicketPriority.high,      120,   480, False, "Connectivity, VPN, wifi, or LAN problems"),
    ("Email",            TicketKind.incident, TicketPriority.high,      120,   480, False, "Email send/receive, calendar, or Outlook issues"),
    ("Access / Login",   TicketKind.incident, TicketPriority.high,       60,   240, False, "Cannot log in, account locked, MFA reset"),
    ("Security Concern", TicketKind.incident, TicketPriority.critical,   30,   120, False, "Suspected phishing, malware, or unauthorised access"),
    ("Other",            TicketKind.incident, TicketPriority.medium,    480,  2880, False, "Anything that doesn't fit the categories above"),

    # Service Requests — something needs to happen
    ("New Laptop",            TicketKind.service_request, TicketPriority.medium, 480, 4320, True,  "Issue a new laptop to a user (requires approval)"),
    ("Software Install",      TicketKind.service_request, TicketPriority.medium, 480, 1440, False, "Install or license a specific application"),
    ("VPN Access",            TicketKind.service_request, TicketPriority.medium, 240,  720, True,  "Grant or modify VPN access (requires approval)"),
    ("Password Reset",        TicketKind.service_request, TicketPriority.high,    60,  240, False, "Reset password for an enterprise system"),
    ("Onboarding",            TicketKind.service_request, TicketPriority.medium, 720, 4320, False, "New joiner setup — accounts, hardware, accesses"),
    ("Offboarding",           TicketKind.service_request, TicketPriority.medium, 240, 1440, False, "Departing employee — revoke accesses, recover hardware"),
    ("Meeting Room Setup",    TicketKind.service_request, TicketPriority.low,    240, 1440, False, "Set up a meeting room for a specific session"),
    ("Other Request",         TicketKind.service_request, TicketPriority.low,    480, 2880, False, "Anything that doesn't fit the categories above"),
]


def run(db: Session) -> None:
    tenant = db.query(Tenant).first()
    if tenant is None:
        tenant = Tenant(name=settings.tenant_name)
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        log.info("Bootstrapped tenant '%s' enrolment_key=%s", tenant.name, tenant.enrolment_key)

    # Bootstrap admin user from env
    admin = db.query(User).filter(User.email == settings.admin_email).first()
    if admin is None:
        admin = User(
            tenant_id=tenant.id,
            email=settings.admin_email,
            password_hash=hash_password(settings.admin_password),
            full_name="OctoAssist Administrator",
            role=UserRole.admin,
            is_active=True,
        )
        db.add(admin)
        db.commit()
        log.info("Bootstrapped admin user email=%s (use %s as username at /login)", admin.email, settings.admin_username)

    # Backwards-compat: also create a user keyed by admin_username (treated as email)
    # so old creds (admin / <pwd>) keep working at the login form.
    admin_alt = db.query(User).filter(User.email == settings.admin_username).first()
    if admin_alt is None and settings.admin_username != settings.admin_email:
        admin_alt = User(
            tenant_id=tenant.id,
            email=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            full_name="OctoAssist Administrator",
            role=UserRole.admin,
            is_active=True,
        )
        db.add(admin_alt)
        db.commit()

    # Seed default categories if none exist for this tenant
    existing = db.query(Category).filter(Category.tenant_id == tenant.id).count()
    if existing == 0:
        for name, kind, prio, resp, resol, approval, desc in DEFAULT_CATEGORIES:
            db.add(Category(
                tenant_id=tenant.id,
                name=name, kind=kind,
                default_priority=prio,
                sla_response_minutes=resp,
                sla_resolution_minutes=resol,
                requires_approval=approval,
                description=desc,
            ))
        db.commit()
        log.info("Seeded %d default categories", len(DEFAULT_CATEGORIES))

    # Seed 4 technician (agent) licences. Idempotent — only creates if the
    # email isn't already present. Initial password follows a predictable
    # pattern so the admin can communicate it to each tech for first login;
    # they must change it themselves on first sign-in (manual flow for now).
    DEFAULT_TECHNICIANS = [
        ("rohit.patil@thirdoctopus.com",   "Rohit Patil",   "OctoAssist!Rohit#2026"),
        ("priya.sharma@thirdoctopus.com",  "Priya Sharma",  "OctoAssist!Priya#2026"),
        ("karthik.iyer@thirdoctopus.com",  "Karthik Iyer",  "OctoAssist!Karthik#2026"),
        ("anjali.desai@thirdoctopus.com",  "Anjali Desai",  "OctoAssist!Anjali#2026"),
    ]
    created = 0
    for email, full_name, pwd in DEFAULT_TECHNICIANS:
        existing_u = db.query(User).filter(User.email == email).first()
        if existing_u is not None:
            continue
        db.add(User(
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password(pwd),
            full_name=full_name,
            role=UserRole.agent,
            is_active=True,
        ))
        created += 1
    if created:
        db.commit()
        log.info("Seeded %d technician licences (role=agent). Default passwords follow OctoAssist!<First>#2026", created)

    # Seed TEMA India-flavoured KB articles (heavy-engineering / manufacturing
    # IT context). Idempotent — keyed by slug.
    from .seed_kb_tema import seed_tema_kb
    seed_tema_kb(db, tenant)

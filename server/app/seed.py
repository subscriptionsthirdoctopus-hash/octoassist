"""First-run bootstrap.

Creates the default tenant, the initial admin user from env vars, and a
starter set of categories. Idempotent — only creates rows that don't exist.
"""
import logging
from sqlalchemy.orm import Session

from .config import settings
from .models import Category, Tenant, TicketKind, TicketPriority, User, UserRole, ReplyTemplate, CabCommittee
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
    elif tenant.name == "Third Octopus":
        tenant.name = settings.tenant_name
        db.commit()
        db.refresh(tenant)
        log.info("Auto-renamed legacy tenant 'Third Octopus' to '%s'", tenant.name)

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

    # Seed default ServiceCatalogItems if none exist
    from .models import ServiceCatalogItem
    existing_items = db.query(ServiceCatalogItem).filter(ServiceCatalogItem.tenant_id == tenant.id).count()
    if existing_items == 0:
        laptop_cat = db.query(Category).filter(Category.tenant_id == tenant.id, Category.name == "New Laptop").first()
        vpn_cat = db.query(Category).filter(Category.tenant_id == tenant.id, Category.name == "VPN Access").first()
        
        if laptop_cat:
            db.add(ServiceCatalogItem(
                tenant_id=tenant.id,
                name="Request Premium Laptop",
                description="Request a brand new high-performance laptop for development, design, or office work.",
                category_id=laptop_cat.id,
                fields=[
                    {"name": "laptop_model", "label": "Laptop Model", "type": "select", "choices": ["ThinkPad T14 (Intel i7, 16GB RAM)", "MacBook Pro 14 (M3 Pro, 18GB RAM)", "Dell Latitude 5440 (Intel i5, 16GB RAM)"], "is_required": True},
                    {"name": "ram_required_gb", "label": "RAM Upgrade Required (GB)", "type": "select", "choices": ["No Upgrade", "32 GB", "64 GB"], "is_required": False},
                    {"name": "department", "label": "Department", "type": "select", "choices": ["Engineering", "Product", "Sales", "HR", "Finance"], "is_required": True},
                    {"name": "justification", "label": "Business Justification", "type": "text", "is_required": True}
                ]
            ))
        if vpn_cat:
            db.add(ServiceCatalogItem(
                tenant_id=tenant.id,
                name="Request VPN Access",
                description="Request secure remote access to enterprise networks, production servers, or development databases.",
                category_id=vpn_cat.id,
                fields=[
                    {"name": "access_level", "label": "Access Level Required", "type": "select", "choices": ["Standard User VPN", "Production DB/Admin VPN", "Vendor Partner VPN"], "is_required": True},
                    {"name": "environments", "label": "Target Servers / Databases / Environments", "type": "text", "is_required": True},
                    {"name": "duration", "label": "Access Duration", "type": "select", "choices": ["Temporary (1 Month)", "Temporary (6 Months)", "Permanent"], "is_required": True},
                    {"name": "justification", "label": "Business Justification", "type": "text", "is_required": True}
                ]
            ))
        db.commit()
        log.info("Seeded default Service Catalog Items")


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

    # Seed 10 professional predefined canned response templates
    existing_templates = db.query(ReplyTemplate).filter(ReplyTemplate.tenant_id == tenant.id).count()
    if existing_templates == 0:
        canned_replies = [
            ("Acknowledge & Investigate", 
             "Hello,\n\nWe have received your request and are currently investigating the issue. A technician will update you as soon as we have more details or diagnostic findings.\n\nBest regards,\nTema IT Team", 1),
            
            ("Request for More Information", 
             "Hello,\n\nIn order to assist you further, could you please provide additional details, error codes, or a screenshot of the issue you are experiencing?\n\nBest regards,\nTema IT Team", 2),
            
            ("Password Reset Completed", 
             "Hello,\n\nYour account password has been successfully reset. Please log in using the temporary credentials sent to your registered mobile number and update your password immediately upon first login.\n\nBest regards,\nTema IT Team", 3),
            
            ("Software Installation Completed", 
             "Hello,\n\nWe have successfully pushed the requested software to your workstation remote agent. Please restart your system if you do not see the application icon on your desktop.\n\nBest regards,\nTema IT Team", 4),
            
            ("Escalated to Level 2 Support", 
             "Hello,\n\nWe have reviewed your ticket and escalated it to our Level 2 engineering support team for deeper investigation. We will keep you updated on the progress.\n\nBest regards,\nTema IT Team", 5),
            
            ("Scheduled Maintenance Notice", 
             "Hello,\n\nThe system you reported is scheduled for routine maintenance in the upcoming window. We anticipate services will be fully restored post-maintenance. Thank you for your patience.\n\nBest regards,\nTema IT Team", 6),
            
            ("On Hold - Pending Vendor Response", 
             "Hello,\n\nThis ticket is currently on hold as we have escalated the issue to the external software/hardware vendor for a patch or replacement. We will notify you once they respond.\n\nBest regards,\nTema IT Team", 7),
            
            ("On Hold - Pending User Verification", 
             "Hello,\n\nWe have applied a tentative fix. This ticket is on hold awaiting your verification. Please let us know if the issue is resolved so we can close the ticket.\n\nBest regards,\nTema IT Team", 8),
            
            ("Issue Resolved & Closing Ticket", 
             "Hello,\n\nWe believe the reported issue has been resolved. We are closing this ticket. If the problem persists, feel free to reopen this ticket or submit a new request.\n\nBest regards,\nTema IT Team", 9),
            
            ("Hardware Replacement Arranged", 
             "Hello,\n\nWe have approved a hardware replacement for your workstation. Please visit the IT Helpdesk desk at your earliest convenience to collect your new device and return the legacy hardware.\n\nBest regards,\nTema IT Team", 10)
        ]
        
        for title, body, order in canned_replies:
            db.add(ReplyTemplate(
                tenant_id=tenant.id,
                title=title,
                body=body,
                sort_order=order
            ))
        db.commit()
        log.info("Seeded 10 default predefined helpdesk responses")

    # Seed default CAB Committees (Change Management and Risk Management) if none exist
    existing_committees = db.query(CabCommittee).filter(CabCommittee.tenant_id == tenant.id).count()
    if existing_committees == 0:
        db.add(CabCommittee(
            tenant_id=tenant.id,
            name="Change Management Committee",
            description="Responsible for coordinating, reviewing, and approving scheduled system changes and infrastructure upgrades."
        ))
        db.add(CabCommittee(
            tenant_id=tenant.id,
            name="Risk Management Committee",
            description="Evaluates high-risk deployments, rollback plans, and security compliance metrics for production services."
        ))
        db.commit()
        log.info("Seeded default CAB Committees (Change Management and Risk Management)")

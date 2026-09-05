"""First-run bootstrap.

Creates the default tenant, the initial admin user from env vars, and a
starter set of categories. Idempotent — only creates rows that don't exist.
"""
import logging
from sqlalchemy.orm import Session

from .config import settings
from .models import Category, Tenant, TicketKind, TicketPriority, User, UserRole, ReplyTemplate, CabCommittee, Holiday
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
    elif tenant.name == "Third Octopus" and tenant.name != settings.tenant_name:
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
             "Dear User,\n\nThank you for contacting the Tema IT Support Helpdesk. We have successfully registered your support request and a service ticket has been created under our tracking system.\n\nOur systems engineering team is actively investigating the technical issues you reported. A dedicated support technician is currently running diagnostics to isolate the root cause.\n\nExpectations & Next Steps:\n- Diagnosis Window: We expect to complete initial diagnostic scans within the next 2 to 4 business hours.\n- Remote Intervention: If necessary, our technician may initiate a secure remote support session to inspect system logs. You will be prompted on your screen to authorize any remote desktop access.\n\nWe will provide a status update as soon as we have completed the initial diagnostic report. If you have any additional symptoms or screenshots to append to this request, please reply directly to this email thread.\n\nSincerely,\nTema IT Support Team\nEmail: support@temaindia.com | Hotdesk Extension: 4400", 1),
            
            ("Request for More Information", 
             "Dear User,\n\nWe are actively reviewing your support request, but require supplementary technical details to proceed with troubleshooting and isolate the root cause.\n\nTo help us resolve this as quickly as possible, please provide the following details by replying directly to this email:\n1. Exact Steps to Reproduce: Please list the exact sequence of actions or inputs that trigger the error.\n2. Error Codes or Messages: Please copy and paste the precise text of any error dialog boxes, or attach a full-screen screenshot showing the error.\n3. Impact and Scope: Is this issue occurring on a single workstation, or are other users in your department/location experiencing the same behavior?\n4. Recent Changes: Have there been any recent software updates, system configuration changes, or password modifications made to this workstation?\n\nOnce we receive this supplementary diagnostic information, our team will immediately resume investigation. We appreciate your cooperation in helping us expedite a resolution.\n\nSincerely,\nTema IT Support Team\nEmail: support@temaindia.com | Hotdesk Extension: 4400", 2),
            
            ("Password Reset Completed", 
             "Dear User,\n\nIn response to your request, the credential store security administrator has successfully reset your account credentials.\n\nTo regain access to your enterprise profile, please complete the following steps:\n1. Retrieve Temporary Password: Your temporary sign-in password has been securely dispatched to your registered corporate mobile number via SMS.\n2. Log In and Update: Navigate to the portal, enter your username and temporary password. You will be prompted immediately to establish a new, secure password.\n3. Password Complexity Guidelines:\n   - Minimum 12 characters in length.\n   - Must contain at least one uppercase letter (A-Z).\n   - Must contain at least one lowercase letter (a-z).\n   - Must contain at least one numerical digit (0-9).\n   - Must contain at least one special character (e.g. !, @, #, $, %).\n   - Cannot match any of your 5 most recently used passwords.\n4. MFA Refresh: If prompted, complete the Multi-Factor Authentication (MFA) challenge via the Microsoft Authenticator app on your enrolled corporate device.\n\nIf you encounter any difficulty during the login process or if your account remains locked, please contact our Helpdesk hot-extension at 4400.\n\nSincerely,\nTema IT Support Team\nEmail: support@temaindia.com | Hotdesk Extension: 4400", 3),
            
            ("Software Installation Completed", 
             "Dear User,\n\nWe are pleased to inform you that the software installation package request has been approved and successfully deployed to your workstation.\n\nThe software deployment was pushed silently via the OctoAssist background agent and completed without disruption. To initialize and verify the application, please perform the following steps:\n1. Verify Shortcut: Locate the application shortcut icon on your Windows Desktop or within your Start Menu under 'Recently Added'.\n2. Post-Install Restart: For the registry configurations and environment variables to take full effect, we highly recommend restarting your workstation at your earliest convenience.\n3. Launch and Enrolment: Open the application and complete any initial sign-in prompts using your standard single sign-on (SSO) credentials.\n\nIf the application fails to launch or if you do not see the icon after restarting your workstation, please reply directly to this thread so we can push a re-installation or initiate a manual diagnostic scan.\n\nSincerely,\nTema IT Support Team\nEmail: support@temaindia.com | Hotdesk Extension: 4400", 4),
            
            ("Escalated to Level 2 Support", 
             "Dear User,\n\nPlease be advised that your support ticket has been escalated from our Level 1 Helpdesk tier to our Level 2 Systems Engineering Support group for advanced troubleshooting and deeper system logs analysis.\n\nDue to the complex nature of the issue (which involves backend system infrastructure or enterprise-wide application services), the Level 2 engineers will need to perform specialized diagnostic procedures.\n\nNext Steps & Service Level Agreement (SLA):\n- Target SLA Review: A Level 2 systems engineer is currently scheduled to review your ticket within the next 4 to 6 business hours.\n- Contact Notice: The assigned engineer may reach out to you directly via Microsoft Teams or internal extension to schedule a live debugging session.\n\nWe are committed to resolving this issue as quickly and robustly as possible. We will notify you of any progress or workaround options immediately as they become available.\n\nSincerely,\nTema IT Support Team\nEmail: support@temaindia.com | Hotdesk Extension: 4400", 5),
            
            ("Scheduled Maintenance Notice", 
             "Dear User,\n\nThank you for reporting this issue. Upon review of our centralized system monitor, we have identified that the workstation or system server you reported is currently scheduled for routine corrective maintenance.\n\nTo resolve the stability issues and apply critical security patches, we have scheduled a dedicated system maintenance window:\n- Scheduled Maintenance Window: Sat 22:00 – Sun 02:00 (Local Time).\n- Expected Downtime: We anticipate brief service interruptions of approximately 15 to 30 minutes while host servers reboot and registry configurations compile.\n- Corrective Plan: Apply monthly cumulative security patches, clear temporary cache volumes, and perform a full database index optimization.\n\nFollowing this window, all services will be fully restored. Please keep your workstation powered on but logged off during this window. We appreciate your patience as we complete this critical baseline hardening.\n\nSincerely,\nTema IT Support Team\nEmail: support@temaindia.com | Hotdesk Extension: 4400", 6),
            
            ("On Hold - Pending Vendor Response", 
             "Dear User,\n\nWe have completed our initial internal diagnostics on your support request and have identified that the root cause lies within a proprietary software/hardware bug that requires external support.\n\nAccordingly, we have escalated this ticket to the original equipment manufacturer (OEM) / proprietary software vendor under our enterprise priority support agreement.\n\nCurrent Ticket Status:\n- Status: On Hold (Pending Vendor Response)\n- Vendor Reference ID: E-TEMA-2026-9482\n- Expected Turnaround: The vendor is currently evaluating our diagnostics logs under their Tier-3 SLA (typically 12 to 24 business hours).\n\nOur team will monitor this vendor escalation closely. We will contact you immediately once a hotfix package, patch version, or equipment replacement is released. Thank you for your understanding.\n\nSincerely,\nTema IT Support Team\nEmail: support@temaindia.com | Hotdesk Extension: 4400", 7),
            
            ("On Hold - Pending User Verification", 
             "Dear User,\n\nOur systems engineering team has successfully applied a corrective patch and completed preliminary functional testing to resolve the issue you reported.\n\nTo ensure the problem has been fully resolved on your end, we have placed this ticket on hold awaiting your User Acceptance Testing (UAT). Could you please verify the following:\n1. Operational Check: Open the application or resource and attempt the actions that previously triggered the failure.\n2. Stability Check: Verify that the application continues to run without crashes, error dialogs, or performance degradation.\n3. Confirm Resolution: Please reply directly to this email to confirm if the fix is successful and if we have your permission to close this ticket.\n\nIf we do not receive a response within 3 business days, this ticket will be automatically closed by our system. However, you can reopen it at any time if the issue recurs.\n\nSincerely,\nTema IT Support Team\nEmail: support@temaindia.com | Hotdesk Extension: 4400", 8),
            
            ("Issue Resolved & Closing Ticket", 
             "Dear User,\n\nWe are pleased to inform you that we have successfully resolved the issue reported in this support request. The corrective actions have been verified, and this ticket is now marked as Resolved and Closed under our Helpdesk system.\n\nSummary of Resolution:\n- Action Taken: Cleaned registry cache, updated workstation agent configurations, and verified system environment pathways.\n- Verification status: Workstation is active, online, and reporting healthy compliance telemetry.\n\nNo further action is required on your part. If you experience any recurrence of this problem, or if you require any new technical assistance, please do not hesitate to submit a new service request. Thank you for choosing Tema IT Support.\n\nSincerely,\nTema IT Support Team\nEmail: support@temaindia.com | Hotdesk Extension: 4400", 9),
            
            ("Hardware Replacement Arranged", 
             "Dear User,\n\nPlease be advised that your hardware replacement request has been approved by the IT asset manager and your new workstation has been fully provisioned and imaged.\n\nThe replacement unit has been pre-configured with your user profile, department software baseline, and the OctoAssist workstation agent.\n\nCollection Instructions:\n- Pickup Location: IT Support Desk (Main Office, 2nd Floor, Technical Wing).\n- Schedule: Monday to Friday, between 09:00 and 17:00.\n- Mandatory Handover: Please bring your legacy hardware device, charger, and any company-issued peripheral accessories for immediate handover and secure data destruction processing.\n\nPlease ensure all your personal local files are backed up to your OneDrive folder prior to handover. If you have any questions or require data migration assistance, please reply directly to this thread.\n\nSincerely,\nTema IT Support Team\nEmail: support@temaindia.com | Hotdesk Extension: 4400", 10)
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

    # Seed 2026 Indian corporate holidays
    from datetime import date
    existing_holidays = db.query(Holiday).filter(Holiday.tenant_id == tenant.id).count()
    if existing_holidays == 0:
        holidays_2026 = [
            ("New Year's Day", date(2026, 1, 1)),
            ("Republic Day", date(2026, 1, 26)),
            ("Maha Shivratri", date(2026, 2, 15)),
            ("Holi", date(2026, 3, 4)),
            ("Good Friday", date(2026, 4, 3)),
            ("Dr. Ambedkar Jayanti", date(2026, 4, 14)),
            ("May Day / Maharashtra Day", date(2026, 5, 1)),
            ("Bakrid / Eid al-Adha", date(2026, 5, 27)),
            ("Independence Day", date(2026, 8, 15)),
            ("Ganesh Chaturthi", date(2026, 9, 14)),
            ("Gandhi Jayanti", date(2026, 10, 2)),
            ("Dussehra", date(2026, 10, 20)),
            ("Diwali (Laxmi Puja)", date(2026, 11, 8)),
            ("Guru Nanak Jayanti", date(2026, 11, 24)),
            ("Christmas Day", date(2026, 12, 25))
        ]
        for name, h_date in holidays_2026:
            db.add(Holiday(tenant_id=tenant.id, name=name, holiday_date=h_date))
        db.commit()
        log.info("Seeded 15 standard 2026 corporate holidays")


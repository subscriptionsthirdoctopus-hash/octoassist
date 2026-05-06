"""Demo / showcase data seeder.

Run inside the running container:

    docker exec -it octoassist-app \
        python -c "from app.seed_demo import run; run(force=True)"

Or via module entry:

    docker exec -it octoassist-app python -m app.seed_demo --force

Idempotent. Each section checks "do I already have N demo rows" and
skips itself if yes (unless --force). All demo emails use the
@octoassist.demo TLD so real users can be told apart at a glance.
"""
from __future__ import annotations

import argparse
import logging
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import (
    Agent,
    AssetSnapshot,
    Category,
    Change,
    ChangeEvent,
    ChangeEventKind,
    ChangeRisk,
    ChangeStatus,
    ChangeType,
    KbArticle,
    KbArticleStatus,
    KbArticleVisibility,
    KbCategory,
    Problem,
    ProblemStatus,
    ProblemTicketLink,
    Tenant,
    Ticket,
    TicketComment,
    TicketEvent,
    TicketEventKind,
    TicketKind,
    TicketPriority,
    TicketStatus,
    User,
    UserRole,
)
from .security import hash_password
from .services.kb import unique_slug

log = logging.getLogger("octoassist.seed_demo")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

DEMO_PASSWORD = "ChangeMe2026!"
NOW = lambda: datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Users — 22 (admin already exists; add 21 more)
# ---------------------------------------------------------------------------

USERS: list[tuple[str, str, UserRole]] = [
    # Admins
    ("aaron.fernandes@octoassist.demo", "Aaron Fernandes", UserRole.admin),
    ("anjali.iyer@octoassist.demo",     "Anjali Iyer",     UserRole.admin),
    # Helpdesk agents
    ("rohan.kumar@octoassist.demo",     "Rohan Kumar",     UserRole.agent),
    ("priya.shetty@octoassist.demo",    "Priya Shetty",    UserRole.agent),
    ("vikram.desai@octoassist.demo",    "Vikram Desai",    UserRole.agent),
    ("nisha.menon@octoassist.demo",     "Nisha Menon",     UserRole.agent),
    ("arjun.bhatt@octoassist.demo",     "Arjun Bhatt",     UserRole.agent),
    # Requesters (end users)
    ("priya.mehra@octoassist.demo",     "Priya Mehra",     UserRole.requester),
    ("rohit.singh@octoassist.demo",     "Rohit Singh",     UserRole.requester),
    ("kavya.rao@octoassist.demo",       "Kavya Rao",       UserRole.requester),
    ("aryan.kapoor@octoassist.demo",    "Aryan Kapoor",    UserRole.requester),
    ("sneha.patel@octoassist.demo",     "Sneha Patel",     UserRole.requester),
    ("rajeev.verma@octoassist.demo",    "Rajeev Verma",    UserRole.requester),
    ("suresh.kulkarni@octoassist.demo", "Suresh Kulkarni", UserRole.requester),
    ("mira.nair@octoassist.demo",       "Mira Nair",       UserRole.requester),
    ("aman.malhotra@octoassist.demo",   "Aman Malhotra",   UserRole.requester),
    ("ishaan.joshi@octoassist.demo",    "Ishaan Joshi",    UserRole.requester),
    ("tanvi.sharma@octoassist.demo",    "Tanvi Sharma",    UserRole.requester),
    ("kabir.khanna@octoassist.demo",    "Kabir Khanna",    UserRole.requester),
    ("zara.ahuja@octoassist.demo",      "Zara Ahuja",      UserRole.requester),
    ("dev.chopra@octoassist.demo",      "Dev Chopra",      UserRole.requester),
    ("riya.banerjee@octoassist.demo",   "Riya Banerjee",   UserRole.requester),
]


def seed_users(db: Session, tenant: Tenant, force: bool) -> dict[str, User]:
    existing = {u.email: u for u in db.query(User).filter(User.tenant_id == tenant.id).all()}
    added = 0
    for email, full_name, role in USERS:
        if email in existing:
            continue
        u = User(
            tenant_id=tenant.id,
            email=email,
            full_name=full_name,
            password_hash=hash_password(DEMO_PASSWORD),
            role=role,
            is_active=True,
        )
        db.add(u)
        added += 1
    if added:
        db.commit()
    log.info("users: added=%d total=%d", added, db.query(User).filter(User.tenant_id == tenant.id).count())
    return {u.email: u for u in db.query(User).filter(User.tenant_id == tenant.id).all()}


# ---------------------------------------------------------------------------
# KB categories (10) and articles (24)
# ---------------------------------------------------------------------------

KB_CATEGORIES = [
    ("Onboarding & Offboarding", "Joining and leaving — what IT needs from you and what we'll do.", 10),
    ("Network & Connectivity",   "WiFi, VPN, internet, intranet.", 20),
    ("Email & Calendar",         "Outlook, Microsoft 365, Teams.", 30),
    ("Software Installation",    "Approved software catalogue and how to request it.", 40),
    ("Hardware",                 "Laptops, monitors, printers, peripherals.", 50),
    ("Security & Access",        "Passwords, MFA, BitLocker, phishing.", 60),
    ("File Sharing & Storage",   "OneDrive, SharePoint, Teams files, external sharing.", 70),
    ("Compliance & Policies",    "ISO 27001, DPDP Act 2023, SEBI CSCRF, acceptable use.", 80),
    ("Remote Work",              "Working from home or while travelling.", 90),
    ("Known Issues",             "Active product/service issues with workarounds.", 100),
]


KB_ARTICLES = [
    # (cat_name, title, summary, body)
    ("Security & Access", "Reset your Microsoft 365 password",
     "Step-by-step for self-service password reset using your registered phone number.",
     """1. Visit https://passwordreset.microsoftonline.com from any browser.
2. Enter your work email address and the captcha.
3. Choose 'I forgot my password' and verify via SMS or Microsoft Authenticator.
4. Set a new password (min 12 chars, mix of letters, numbers, and symbols).
5. Sign in to Outlook / Teams / OctoAssist with the new password.

If you don't have access to your registered phone, raise a ticket via the portal — IT will validate your identity over a video call before resetting."""),

    ("Network & Connectivity", "Set up VPN access on a corporate laptop",
     "Cisco AnyConnect setup for new joiners.",
     """1. AnyConnect is pre-installed on all corporate laptops. Look for it in Start menu.
2. Server address: vpn.thirdoctopus.com
3. First time: sign in with your O365 email + password, then approve the MFA prompt.
4. Once connected, you'll see the corporate network; intranet sites resolve normally.
5. Disconnect from the menu when you're done — leaving it on permanently isn't required.

If sign-in fails: try once more, wait 60s, then raise a ticket if still failing."""),

    ("Network & Connectivity", "Connect to corporate WiFi (TEMA-Secure)",
     "WPA2-Enterprise setup using your O365 credentials.",
     """1. Click the WiFi icon on the taskbar.
2. Choose 'TEMA-Secure'.
3. Sign-in method: 'Use my Windows account' if domain-joined; otherwise enter your O365 email + password.
4. Trust the certificate when prompted.
5. You should be online within 10 seconds.

Guests should connect to 'TEMA-Guest' (separate captive portal — receptionist provides daily code)."""),

    ("Email & Calendar", "Outlook crashes on launch — known issue and workaround",
     "Office 16.0.18000.x has a launch-crash bug; full fix below.",
     """Affected: Outlook for Microsoft 365 builds 16.0.18000.20XXX.

Workaround:
1. Close Outlook fully (Task Manager → end OUTLOOK.EXE if needed).
2. Run: outlook.exe /safe
3. File → Options → Add-ins → COM Add-ins → disable third-party add-ins one at a time.
4. Restart Outlook normally.

Permanent fix: install the latest channel update from https://learn.microsoft.com/en-us/officeupdates/.

If still crashing after the update, raise a ticket — we may need to recreate your Outlook profile."""),

    ("Onboarding & Offboarding", "New joiner — Day 1 IT checklist",
     "What you and your manager will receive on day one.",
     """**Day 0 (manager prep):**
- Manager files a 'New Laptop' service request 5 working days before joining date.
- HR shares the joiner's preferred email handle.

**Day 1 (joiner):**
- Receive laptop with Windows 11 Pro, Office 365, AnyConnect, OctoAssist agent pre-installed.
- Receive temporary O365 password (force-changed on first login).
- Set up Microsoft Authenticator on personal phone (required for MFA).
- Sign in to Outlook, Teams, SharePoint.
- Review Acceptable Use Policy and sign electronically.

**Week 1:**
- Manager assigns SharePoint / Teams / file-share access via service request.
- IT provisions any specialised software (per role)."""),

    ("Onboarding & Offboarding", "Offboarding — manager + IT joint checklist",
     "Process triggered when an employee gives notice or is terminated.",
     """**T-7 days from departure:**
- Manager files an 'Offboarding' service request with departure date.
- Identify any handover items (file ownership, calendar, distribution lists).

**Departure day:**
- Account is automatically disabled at 18:00 IST on the agreed date.
- AnyConnect / VPN access removed.
- BitLocker recovery key archived in CMDB.
- Laptop returned to IT (Mumbai HQ floor 5).

**T+7 days:**
- Mailbox set to read-only and forwarded to manager for 30 days.
- After 30 days, mailbox archived to compliance store; account fully removed.
- Asset record updated to 'Retired' in OctoAssist."""),

    ("Software Installation", "Request approved software",
     "How to install software via the OctoAssist self-service portal.",
     """Approved software is in the service catalogue at /portal/new (kind=service_request → Software Install).

Common requests:
- Adobe Acrobat Pro (licence required — manager approval)
- AutoCAD / Solidworks (engineering only)
- Visual Studio Code (free, no approval)
- WinRAR (free, no approval)
- TeamViewer Host (IT-installed only)
- Slack / Zoom / Teams (already provisioned for everyone)

For software not in the catalogue, file a ticket and IT will assess licence + security implications."""),

    ("Hardware", "Request a new monitor or docking station",
     "Hardware refresh + new-issue process.",
     """Eligibility: full-time staff with > 6 months tenure may request:
- One additional 24" monitor (free)
- Or one 27" monitor (manager approval)
- Docking station (Dell WD19 / WD22) — manager approval

How to file: /portal/new → Service Request → 'Hardware'.

Turnaround: 5 working days for in-stock items; up to 4 weeks for ordered items.

Old hardware: return to IT for inventory update before receiving the new unit."""),

    ("Hardware", "Printer setup — Mumbai HQ floor 4 / 5",
     "Default printer queue and how to add it.",
     """**Floor 4 (Operations):**
- Printer name: PR-MUM-04-Color
- Server: \\\\print-mum-01\\PR-MUM-04-Color
- Default for Operations team

**Floor 5 (Finance):**
- Printer name: PR-MUM-05-Color
- Server: \\\\print-mum-01\\PR-MUM-05-Color

To add: Settings → Bluetooth & devices → Printers → Add device → enter the server path.

Stuck on 'connecting'? Open AnyConnect first (some printers require corporate network)."""),

    ("Security & Access", "Set up Microsoft Authenticator (2FA)",
     "Required for all staff. One-time setup, ~5 minutes.",
     """1. On your personal phone, install 'Microsoft Authenticator' from App Store / Play Store.
2. On a corporate device, sign in to https://aka.ms/mfasetup.
3. Click 'Add method' → Authenticator app → Next.
4. Open the app on your phone → tap '+' → 'Work or school account' → scan the QR code shown on screen.
5. Approve the test prompt that pops up on your phone.
6. Done — future sign-ins will trigger a push notification.

If you change phones, raise a ticket from a corporate device to reset the authenticator."""),

    ("Security & Access", "BitLocker recovery — what to do if your laptop won't boot",
     "Step-by-step recovery using the corporate BitLocker key store.",
     """If you see a blue 'BitLocker recovery' screen at boot:

1. Note the **Recovery key ID** shown on screen (a long string like 1234ABCD-...).
2. Visit https://account.microsoft.com/devices on a different device.
3. Sign in with your work account → find your laptop → 'Manage recovery keys'.
4. Match the Key ID and copy the 48-digit recovery key.
5. Type the recovery key into the BitLocker prompt.

If you can't access the recovery portal, raise a ticket — IT can pull the key from our CMDB after identity verification."""),

    ("Security & Access", "I clicked a phishing link — what to do now",
     "If you suspect you opened a malicious link or attachment.",
     """**Do this immediately:**
1. Disconnect from network (turn off WiFi, unplug ethernet, disable VPN).
2. Note the time, the email subject, the link clicked, and any credentials entered.
3. Walk to IT in person (Mumbai HQ floor 5) OR call +91-XXXXXX-XXXX.
4. Forward the original phishing email as an attachment to security@thirdoctopus.com.

**Don't:**
- Don't reply to the phishing email.
- Don't try to "test" the link again.
- Don't shut down the laptop — IT may need to investigate the live state.

We will reset your password, revoke all active sessions, and run a malware scan. None of this is a disciplinary action — fast reporting is what we need."""),

    ("Email & Calendar", "Microsoft Teams — audio / video troubleshooting",
     "Most-common Teams meeting issues and one-line fixes.",
     """**No audio:** Settings → Devices → check correct mic + speaker selected. Run 'Make a test call'.
**Video frozen:** Restart Teams. If on VPN, also restart the VPN client.
**Echo on call:** Use a headset, not laptop speakers + mic. Or mute when not speaking.
**'Could not join' error:** Sign out of Teams, sign back in. Or join from web (teams.microsoft.com).
**Can't share screen:** Settings → Permissions → 'Display capture' → Allow.
**Background blur not working:** Settings → Devices → 'Camera' → check the camera works first."""),

    ("Compliance & Policies", "Acceptable Use — what IT monitors and what it doesn't",
     "Plain-English summary of the staff Acceptable Use Policy.",
     """**What IT monitors:**
- Email metadata (sender, recipient, subject, attachment names) — for retention / discovery.
- Device health (patch level, antivirus state, BitLocker on/off) — via OctoAssist agent.
- Sign-in events and IP addresses — for security investigation.
- Network traffic to known malicious domains — blocked at the edge.

**What IT does NOT monitor:**
- Email content (mailboxes are not read by anyone unless an HR / legal hold is in place).
- Browsing history.
- Personal devices brought to office (unless they connect to corporate WiFi for work).
- Location data outside of office hours.

**You are responsible for:**
- Not installing unapproved software.
- Not sharing credentials.
- Reporting suspected security incidents within 4 hours."""),

    ("Compliance & Policies", "DPDP Act 2023 — what employees should know",
     "Plain-English summary of your obligations under the Digital Personal Data Protection Act.",
     """The DPDP Act applies whenever you handle 'personal data of any identified or identifiable Indian citizen'.

**As an employee handling personal data, you must:**
- Only collect what's necessary for the stated purpose.
- Store it on approved corporate systems (OneDrive, SharePoint, OctoAssist) — not personal email or USB.
- Honour erasure requests within the SLA your manager sets.
- Report suspected breaches to security@thirdoctopus.com within 24 hours.

**You should NOT:**
- Forward personal data of customers to your personal email account.
- Print personal data and leave it on your desk.
- Take personal data home on a USB stick.

A 12-minute mandatory training is in /portal/kb → 'DPDP Act 2023 mandatory training'."""),

    ("Remote Work", "Working from home — VPN and bandwidth",
     "What works at home and what doesn't.",
     """**Will work over residential broadband:**
- Outlook, Teams, SharePoint, OctoAssist
- VPN-protected internal apps (HRMS, ServiceDesk)
- 1:1 Teams calls and meetings up to 50 people

**May not work well:**
- Large file uploads (use OneDrive / SharePoint instead of email)
- Teams meetings with > 100 people if your home network is < 25 Mbps down
- Software installs / Windows updates over VPN — disconnect VPN for these

**Tip:** if Teams is choppy, disconnect from VPN for the call (Teams traffic doesn't need VPN). Reconnect after."""),

    ("Remote Work", "Working from outside India — security and logistics",
     "Travel rules and pre-departure checklist.",
     """**Before you travel:**
- Notify IT 5 working days in advance with destination + dates.
- Manager approval for trips > 2 weeks (security exposure).
- We'll add the destination IP range to your Conditional Access whitelist.

**During travel:**
- Always use VPN on untrusted WiFi (cafes, hotels).
- Use a privacy screen on your laptop in public spaces.
- Don't use public USB charging stations — bring your own charger.

**Restricted countries:**
- IT will brief you separately if you're travelling to a country where Microsoft 365 / VPN traffic is blocked or inspected (China, Russia, Iran, etc.)."""),

    ("File Sharing & Storage", "Sharing files securely with external partners",
     "Approved tools and prohibited methods for external file exchange.",
     """**Approved (use these):**
- OneDrive / SharePoint share links with 'Specific people' permission and an expiry date.
- Microsoft Teams 'Channel files' for partners with a guest account in your tenant.
- Approved managed-file-transfer (Citrix ShareFile) for files > 1 GB or sensitive data.

**Prohibited:**
- Personal Dropbox / Google Drive / WeTransfer.
- Email attachments over 25 MB (will be blocked).
- USB sticks or external hard drives.

For one-off external collaboration, request a guest invite via /portal/new — turnaround is < 1 hour."""),

    ("File Sharing & Storage", "Convert from emailed attachments to OneDrive links",
     "How to break the email-attachment habit.",
     """1. In Outlook, attach a file using 'Attach file' → 'Browse this PC' → choose file.
2. After attachment appears, click the dropdown next to it → 'Upload to OneDrive'.
3. Outlook converts it into a sharing link automatically.
4. Click the dropdown again → 'Manage permissions' → set the right access level.

Why this matters:
- Attachments age. Links to OneDrive always reflect the latest version.
- Links can be revoked. Attachments cannot.
- Audit trail of who opened the file."""),

    ("Email & Calendar", "Configure Outlook on your phone (Outlook Mobile)",
     "Mobile email with full corporate compliance.",
     """1. Install 'Microsoft Outlook' from App Store (iOS) or Play Store (Android).
2. Open it → 'Add account' → enter your work email.
3. The app will auto-detect Microsoft 365 — sign in with your password + MFA.
4. Approve any device-management prompts (registers your phone with Intune).
5. Done — calendar, mail, and Teams are all available.

Notes:
- We don't see your personal photos / messages — Intune only manages the Outlook app sandbox.
- If you leave the company, the Outlook app + corporate data is wiped from your phone; nothing else."""),

    ("Known Issues", "Slow logins on Friday afternoons (5–6 PM IST)",
     "Investigation in progress — workaround below.",
     """Status: Active problem record PRB-00003 — Group Policy refresh storm at end-of-week.

Symptoms: Login takes 60–120s on Friday afternoons, especially on the Mumbai HQ network.

Workaround:
- If logging in fresh: just wait, it will complete.
- If you're already logged in: stay logged in (don't lock the laptop) until the GPO refresh completes (typically 18:30 IST).
- Use OneDrive offline mode for files — they sync once login finishes.

Permanent fix: scheduled change CHG-00003 — distribute GPO refresh window. Target: 2026-Q3."""),

    ("Known Issues", "External monitor not detected — Dell USB-C dock",
     "Workaround for Dell WD19 / WD22 docks losing the external display.",
     """Symptoms: External monitor goes black after laptop sleep / hibernation.

Workaround (do all three):
1. Unplug the USB-C cable from the laptop.
2. Wait 10 seconds.
3. Plug back in.

If still black: hold Power button on the dock for 5 seconds (hard reset of the dock).

Permanent fix: install the latest WD19 firmware (1.00.32 or newer). IT can push it to your laptop on request — file a ticket."""),

    ("Hardware", "Connecting external monitors / dual-screen setup",
     "How to plug in, configure, and arrange.",
     """**Cables you'll need:**
- USB-C → HDMI / DisplayPort (for thunderbolt / USB-C laptops)
- HDMI → HDMI (older laptops)
- Or use the Dell dock if you have one.

**Set up:**
1. Plug in the cable while the laptop is on.
2. Windows should detect the monitor in 5–10 seconds.
3. Win+P → Extend (most common) or Duplicate.
4. Settings → System → Display → drag the monitor icons to match physical layout.

**Tips:**
- Set scale to 100% on external monitors to avoid blurry text.
- Right-click on desktop → Display Settings → set the external monitor as 'Main display' if it's bigger."""),

    ("Email & Calendar", "Set up automatic Out of Office",
     "For planned leave or after-hours signal.",
     """1. Open Outlook (desktop or web).
2. File → Automatic Replies (or Settings → Mail → Automatic replies on web).
3. Toggle 'Send automatic replies' on.
4. Set start/end dates.
5. Different message for inside vs outside the organisation.
6. Save.

**Good practice:**
- Outside-org reply should not reveal your specific dates of return — say 'limited access' instead.
- Always include a fallback contact (peer or manager) for urgent matters.
- Don't disclose who you're with or what for ('travelling abroad' is fine; 'in Bali for a wedding' is not)."""),
]


def seed_kb(db: Session, tenant: Tenant, force: bool) -> None:
    cat_existing = {c.name: c for c in db.query(KbCategory).filter(KbCategory.tenant_id == tenant.id).all()}
    cats_added = 0
    for name, desc, sort_order in KB_CATEGORIES:
        if name in cat_existing:
            continue
        c = KbCategory(tenant_id=tenant.id, name=name, description=desc, sort_order=sort_order)
        db.add(c)
        cats_added += 1
    if cats_added:
        db.commit()
    cat_existing = {c.name: c for c in db.query(KbCategory).filter(KbCategory.tenant_id == tenant.id).all()}

    art_count = db.query(KbArticle).filter(KbArticle.tenant_id == tenant.id).count()
    if art_count >= len(KB_ARTICLES) and not force:
        log.info("kb_articles: already %d, skipping", art_count)
        log.info("kb_categories: total=%d (added=%d)", len(cat_existing), cats_added)
        return

    # Use the admin user as author (always exists as the bootstrap)
    admin = (db.query(User)
              .filter(User.tenant_id == tenant.id, User.role == UserRole.admin)
              .order_by(User.id).first())

    arts_added = 0
    for cat_name, title, summary, body in KB_ARTICLES:
        if db.query(KbArticle).filter(KbArticle.title == title, KbArticle.tenant_id == tenant.id).first():
            continue
        cat = cat_existing.get(cat_name)
        slug = unique_slug(db, title)
        art = KbArticle(
            tenant_id=tenant.id,
            category_id=cat.id if cat else None,
            slug=slug,
            title=title,
            summary=summary,
            body=body,
            status=KbArticleStatus.published,
            visibility=KbArticleVisibility.portal,
            author_id=admin.id if admin else None,
            view_count=random.randint(8, 312),
            published_at=NOW() - timedelta(days=random.randint(1, 90)),
            created_at=NOW() - timedelta(days=random.randint(1, 100)),
        )
        db.add(art)
        arts_added += 1
    db.commit()
    log.info("kb_articles: added=%d total=%d", arts_added, db.query(KbArticle).filter(KbArticle.tenant_id == tenant.id).count())
    log.info("kb_categories: added=%d total=%d", cats_added, len(cat_existing))


# ---------------------------------------------------------------------------
# Asset agents — 25 endpoints with full snapshot
# ---------------------------------------------------------------------------

# (hostname, logged_in_user_email, ram_gb, model, disk_gb, dept_short)
ENDPOINTS = [
    ("LT-FIN-001", "priya.mehra@octoassist.demo",     16, "Latitude 5550",   512, "Finance"),
    ("LT-FIN-002", "rajeev.verma@octoassist.demo",    16, "Latitude 5550",   512, "Finance"),
    ("LT-FIN-003", "suresh.kulkarni@octoassist.demo", 16, "Latitude 7440",   1024, "Finance"),
    ("LT-FIN-004", "mira.nair@octoassist.demo",       16, "Latitude 5550",   512, "Finance"),
    ("LT-FIN-005", "tanvi.sharma@octoassist.demo",    16, "Latitude 5550",   512, "Finance"),
    ("LT-FIN-006", "kavya.rao@octoassist.demo",       8,  "Latitude 5440",   256, "Finance"),
    ("LT-DEV-001", "rohit.singh@octoassist.demo",     32, "Latitude 7440",   1024, "Engineering"),
    ("LT-DEV-002", "aman.malhotra@octoassist.demo",   32, "Latitude 7440",   1024, "Engineering"),
    ("LT-DEV-003", "ishaan.joshi@octoassist.demo",    32, "Latitude 7440",   1024, "Engineering"),
    ("LT-DEV-004", "kabir.khanna@octoassist.demo",    16, "Latitude 5550",   512, "Engineering"),
    ("LT-DEV-005", "dev.chopra@octoassist.demo",      16, "Latitude 5550",   512, "Engineering"),
    ("LT-DEV-006", "riya.banerjee@octoassist.demo",   32, "Latitude 7440",   1024, "Engineering"),
    ("WS-OPS-001", "rohan.kumar@octoassist.demo",      8, "OptiPlex 7010",   256, "Operations"),
    ("WS-OPS-002", "priya.shetty@octoassist.demo",     8, "OptiPlex 7010",   256, "Operations"),
    ("WS-OPS-003", "vikram.desai@octoassist.demo",     8, "OptiPlex 7010",   256, "Operations"),
    ("WS-OPS-004", "nisha.menon@octoassist.demo",      8, "OptiPlex 7010",   256, "Operations"),
    ("WS-OPS-005", "arjun.bhatt@octoassist.demo",      8, "OptiPlex 7010",   256, "Operations"),
    ("LT-MKT-001", "sneha.patel@octoassist.demo",     16, "Latitude 5550",   512, "Marketing"),
    ("LT-MKT-002", "aryan.kapoor@octoassist.demo",    16, "Latitude 5550",   512, "Marketing"),
    ("LT-MKT-003", "zara.ahuja@octoassist.demo",      16, "Latitude 5550",   512, "Marketing"),
    ("LT-MKT-004", "mira.nair@octoassist.demo",       16, "Latitude 5550",   512, "Marketing"),
    ("LT-EXEC-01", "aaron.fernandes@octoassist.demo", 32, "XPS 15 9540",     1024, "Executive"),
    ("LT-EXEC-02", "anjali.iyer@octoassist.demo",     32, "XPS 15 9540",     1024, "Executive"),
    ("LT-EXEC-03", "admin",                            32, "XPS 15 9540",     1024, "Executive"),
    ("WS-RECP-01", "kavya.rao@octoassist.demo",        8, "OptiPlex 3000",   256, "Reception"),
]


SOFTWARE_BASE = [
    ("Microsoft 365 Apps for enterprise", "16.0.18025.20096", "Microsoft Corporation"),
    ("Microsoft Edge", "131.0.2903.51", "Microsoft Corporation"),
    ("Google Chrome", "131.0.6778.86", "Google LLC"),
    ("Microsoft Teams", "24322.2906.3279.6849", "Microsoft Corporation"),
    ("Adobe Acrobat Reader DC", "24.005.20320", "Adobe Systems Incorporated"),
    ("Microsoft OneDrive", "24.226.1107.0001", "Microsoft Corporation"),
    ("Microsoft Visual C++ 2015-2022 Redistributable (x64)", "14.40.33810.0", "Microsoft Corporation"),
    ("Microsoft .NET Runtime - 8.0.10 (x64)", "8.0.10.34504", "Microsoft Corporation"),
    ("7-Zip 24.07 (x64)", "24.07", "Igor Pavlov"),
    ("Notepad++ (64-bit)", "8.6.5", "Notepad++ Team"),
    ("Cisco AnyConnect Secure Mobility Client", "5.1.4.74", "Cisco Systems, Inc."),
    ("Kaspersky Endpoint Security for Windows", "12.5.0.539", "AO Kaspersky Lab"),
    ("VLC media player", "3.0.21", "VideoLAN"),
    ("Zoom", "6.1.10 (43623)", "Zoom Video Communications, Inc."),
    ("Slack", "4.40.122", "Slack Technologies, Inc."),
]
SOFTWARE_DEV_EXTRA = [
    ("Visual Studio Code", "1.94.2", "Microsoft Corporation"),
    ("Git", "2.46.0", "The Git Development Community"),
    ("Docker Desktop", "4.34.2", "Docker Inc."),
    ("Postman", "11.18.0", "Postman, Inc."),
    ("Node.js", "20.18.0", "Node.js Foundation"),
    ("Python 3.12.7 (64-bit)", "3.12.7150.0", "Python Software Foundation"),
]
SOFTWARE_FIN_EXTRA = [
    ("Tally Prime", "4.0.0.51", "Tally Solutions"),
    ("Adobe Acrobat Pro DC", "24.005.20320", "Adobe Systems Incorporated"),
]
SOFTWARE_MKT_EXTRA = [
    ("Adobe Creative Cloud", "6.4.0.394", "Adobe Systems Incorporated"),
    ("Canva", "1.99.0", "Canva Pty Ltd"),
]


def _make_snapshot(host: str, user: str, ram_gb: int, model: str, disk_gb: int, dept: str) -> dict[str, Any]:
    cpu = random.choice([
        ("Intel(R) Core(TM) i5-1345U", 10, 12),
        ("Intel(R) Core(TM) i7-1355U", 10, 12),
        ("Intel(R) Core(TM) i7-1365U", 10, 12),
        ("Intel(R) Core(TM) i7-13700H", 14, 20),
        ("Intel(R) Core(TM) Ultra 7 155H", 16, 22),
    ])
    used_gb = round(disk_gb * random.uniform(0.18, 0.62), 1)
    free_gb = round(disk_gb - used_gb, 1)

    sw = list(SOFTWARE_BASE)
    if dept == "Engineering":
        sw += SOFTWARE_DEV_EXTRA
    if dept == "Finance":
        sw += SOFTWARE_FIN_EXTRA
    if dept == "Marketing":
        sw += SOFTWARE_MKT_EXTRA

    octets = (10, 122, random.randint(1, 4), random.randint(10, 250))
    mac = ":".join(f"{random.randint(0, 255):02X}" for _ in range(6))
    return {
        "snapshot_at": NOW().isoformat(),
        "os": {
            "caption": "Microsoft Windows 11 Pro",
            "version": "10.0.22631",
            "build_number": "22631",
            "architecture": "64-bit",
            "install_date": (NOW() - timedelta(days=random.randint(45, 540))).isoformat(),
        },
        "cpu": {"name": cpu[0], "cores": cpu[1], "logical_processors": cpu[2], "max_clock_mhz": 4500},
        "memory": {"total_gb": float(ram_gb)},
        "disks": [
            {"drive": "C:", "filesystem": "NTFS", "size_gb": float(disk_gb), "free_gb": free_gb},
        ],
        "network": [
            {"name": "Wi-Fi", "mac": mac, "ip": ".".join(str(o) for o in octets)},
        ],
        "bios": {
            "manufacturer": "Dell Inc.",
            "version": f"{random.randint(1, 2)}.{random.randint(8, 22)}.{random.randint(0, 4)}",
            "serial": f"D{random.randint(1, 9)}{''.join(random.choice('ABCDEFGHJKMNPQRSTUVWXYZ23456789') for _ in range(6))}",
        },
        "system": {
            "manufacturer": "Dell Inc.",
            "model": model,
            "domain": "thirdoctopus.local",
            "workgroup": None,
        },
        "logged_in_user": user,
        "software": [{"name": n, "version": v, "publisher": p, "install_date": None} for (n, v, p) in sw],
    }


def seed_assets(db: Session, tenant: Tenant, force: bool) -> None:
    existing = db.query(Agent).filter(Agent.tenant_id == tenant.id).count()
    if existing >= len(ENDPOINTS) and not force:
        log.info("agents: already %d, skipping", existing)
        return
    by_host = {a.hostname: a for a in db.query(Agent).filter(Agent.tenant_id == tenant.id).all()}
    added = 0
    for host, user_email, ram, model, disk, dept in ENDPOINTS:
        if host in by_host and not force:
            continue
        if host in by_host:
            agent = by_host[host]
        else:
            agent = Agent(
                tenant_id=tenant.id,
                machine_id=str(uuid.uuid4()),
                hostname=host,
                last_seen_at=NOW() - timedelta(minutes=random.randint(2, 360)),
            )
            db.add(agent)
            db.flush()
            added += 1
        snap = AssetSnapshot(
            agent_id=agent.id,
            snapshot_at=NOW() - timedelta(minutes=random.randint(2, 360)),
            payload=_make_snapshot(host, user_email, ram, model, disk, dept),
        )
        db.add(snap)
    db.commit()
    log.info("agents: added=%d total=%d", added, db.query(Agent).filter(Agent.tenant_id == tenant.id).count())


# ---------------------------------------------------------------------------
# Tickets — 25 with realistic statuses, assignees, comments
# ---------------------------------------------------------------------------

TICKETS = [
    # (kind, category_name, title, description, priority, status, reporter_email, assignee_email, comments)
    (TicketKind.incident, "Software Issue",
     "Outlook crashing on launch", "Outlook closes itself within 3s of opening. Started this morning after the Office update. I've already restarted twice.",
     TicketPriority.high, TicketStatus.in_progress,
     "priya.mehra@octoassist.demo", "rohan.kumar@octoassist.demo",
     [
       ("rohan.kumar@octoassist.demo", "Reproduced on a clean profile. Looks like the recent Office update — rolling back.", False),
       ("rohan.kumar@octoassist.demo", "KB-127 covers this rollback. Linking.", True),
     ]),
    (TicketKind.incident, "Network",
     "VPN drops every 30 minutes", "Cisco AnyConnect drops the connection every 30 mins. Reconnecting works but I lose work in progress.",
     TicketPriority.critical, TicketStatus.in_progress,
     "rohit.singh@octoassist.demo", "vikram.desai@octoassist.demo",
     [("vikram.desai@octoassist.demo", "Looking at the VPN concentrator logs for your session.", True)]),
    (TicketKind.incident, "Access / Login",
     "Cannot access SharePoint site", "Got 'You don't have access' on the Finance SharePoint. I had access yesterday.",
     TicketPriority.medium, TicketStatus.open,
     "tanvi.sharma@octoassist.demo", None,
     []),
    (TicketKind.incident, "Network",
     "Slow internet from Mumbai HQ floor 4", "Pages take 30+ seconds to load. Speedtest shows 4 Mbps down. Other floors are fine.",
     TicketPriority.medium, TicketStatus.in_progress,
     "kavya.rao@octoassist.demo", "arjun.bhatt@octoassist.demo",
     []),
    (TicketKind.incident, "Software Issue",
     "BitLocker recovery prompt on boot", "Laptop asked for BitLocker recovery after this morning's restart. I used the portal to get the key, all fine now.",
     TicketPriority.high, TicketStatus.resolved,
     "rajeev.verma@octoassist.demo", "priya.shetty@octoassist.demo",
     [("priya.shetty@octoassist.demo", "Resolved by self-service. Logging the recovery event for audit.", False)]),
    (TicketKind.incident, "Email",
     "Email rules not syncing", "Outlook web rules show but desktop doesn't apply them.",
     TicketPriority.low, TicketStatus.on_hold,
     "mira.nair@octoassist.demo", "nisha.menon@octoassist.demo",
     [("nisha.menon@octoassist.demo", "Microsoft has confirmed this is a known sync delay — waiting for the rollout.", False)]),
    (TicketKind.incident, "Access / Login",
     "Two-factor not working — blocked", "Authenticator app shows 'Code expired' even though I just received it.",
     TicketPriority.high, TicketStatus.in_progress,
     "suresh.kulkarni@octoassist.demo", "rohan.kumar@octoassist.demo",
     []),
    (TicketKind.incident, "Hardware Issue",
     "Printer offline — Mumbai floor 4", "PR-MUM-04-Color shows offline since Monday morning.",
     TicketPriority.low, TicketStatus.resolved,
     "ishaan.joshi@octoassist.demo", "vikram.desai@octoassist.demo",
     [("vikram.desai@octoassist.demo", "Rebooted print spooler. Online now.", False)]),
    (TicketKind.incident, "Access / Login",
     "Account locked after password reset", "Reset my password 10 min ago, now everything says 'Account is locked'.",
     TicketPriority.high, TicketStatus.resolved,
     "aman.malhotra@octoassist.demo", "rohan.kumar@octoassist.demo",
     [("rohan.kumar@octoassist.demo", "AD takes a few minutes to propagate. Unlocked manually for you.", False)]),
    (TicketKind.incident, "Software Issue",
     "Excel macros disabled by policy", "I need to run a financial macro and Excel is blocking it.",
     TicketPriority.medium, TicketStatus.closed,
     "rajeev.verma@octoassist.demo", "priya.shetty@octoassist.demo",
     [("priya.shetty@octoassist.demo", "We don't enable macros from internet-sourced files. If this is a known internal macro, share the file via SharePoint and I'll mark it as trusted.", False)]),

    (TicketKind.service_request, "New Laptop",
     "New laptop request — Priya Mehra", "Replacement for LT-FIN-001 — battery doesn't hold charge. Tenure: 3 years.",
     TicketPriority.medium, TicketStatus.open,
     "priya.mehra@octoassist.demo", None,
     []),
    (TicketKind.service_request, "VPN Access",
     "VPN access for Rohit Singh", "New joiner, started today.",
     TicketPriority.medium, TicketStatus.in_progress,
     "rohit.singh@octoassist.demo", "vikram.desai@octoassist.demo",
     [("vikram.desai@octoassist.demo", "Manager approval received. Provisioning AnyConnect.", False)]),
    (TicketKind.service_request, "Software Install",
     "Adobe Acrobat Pro for Finance review", "Need full Acrobat Pro for redaction work on regulatory submissions.",
     TicketPriority.low, TicketStatus.in_progress,
     "rajeev.verma@octoassist.demo", "rohan.kumar@octoassist.demo",
     []),
    (TicketKind.service_request, "Onboarding",
     "Onboarding — Sneha Patel starts Monday", "New marketing exec. Day 1 setup needed.",
     TicketPriority.medium, TicketStatus.open,
     "sneha.patel@octoassist.demo", "priya.shetty@octoassist.demo",
     []),
    (TicketKind.service_request, "Offboarding",
     "Offboarding — Mr. Rajeev departing", "Notice period ends Friday.",
     TicketPriority.medium, TicketStatus.in_progress,
     "anjali.iyer@octoassist.demo", "nisha.menon@octoassist.demo",
     [("nisha.menon@octoassist.demo", "Disabling at 18:00 IST Friday. Mailbox forwarded to manager for 30 days.", False)]),
    (TicketKind.service_request, "Meeting Room Setup",
     "Boardroom AV setup — Friday board meeting", "11am-2pm. Need Teams Rooms console working + remote attendees.",
     TicketPriority.low, TicketStatus.open,
     "aaron.fernandes@octoassist.demo", None,
     []),
    (TicketKind.service_request, "Password Reset",
     "Password reset for SAP — Suresh K", "SAP login locked after returning from leave.",
     TicketPriority.high, TicketStatus.resolved,
     "suresh.kulkarni@octoassist.demo", "vikram.desai@octoassist.demo",
     [("vikram.desai@octoassist.demo", "Reset done — temporary password sent over Teams DM.", False)]),
    (TicketKind.service_request, "Other Request",
     "Email distribution list change — IT-Ops", "Add Nisha and Arjun to it-ops@thirdoctopus.com",
     TicketPriority.low, TicketStatus.closed,
     "rohan.kumar@octoassist.demo", "rohan.kumar@octoassist.demo",
     [("rohan.kumar@octoassist.demo", "Done.", False)]),
    (TicketKind.service_request, "Other Request",
     "Additional 27\" monitor — Kavya R", "Tenure > 6 months. Manager approved.",
     TicketPriority.low, TicketStatus.open,
     "kavya.rao@octoassist.demo", None,
     []),
    (TicketKind.service_request, "Onboarding",
     "Onboarding — three new joiners next month", "Vikram, Aman, Priya — start dates: 5th, 12th, 12th.",
     TicketPriority.medium, TicketStatus.open,
     "anjali.iyer@octoassist.demo", "rohan.kumar@octoassist.demo",
     []),
    (TicketKind.incident, "Email",
     "Microsoft Teams audio not working", "Camera works, mic doesn't. KB-12 didn't help.",
     TicketPriority.medium, TicketStatus.resolved,
     "zara.ahuja@octoassist.demo", "vikram.desai@octoassist.demo",
     [("vikram.desai@octoassist.demo", "Was a privacy permission. Settings → Mic → Allow Teams. Fixed.", False)]),
    (TicketKind.incident, "Security Concern",
     "Phishing email — need help reporting", "Got an email pretending to be the CEO asking for a wire transfer. I didn't click.",
     TicketPriority.high, TicketStatus.resolved,
     "tanvi.sharma@octoassist.demo", "priya.shetty@octoassist.demo",
     [
       ("priya.shetty@octoassist.demo", "Thanks for reporting. Forwarded to security@. Blocking sender domain.", False),
       ("priya.shetty@octoassist.demo", "Internal note: confirmed phishing attempt, sender on known threat-actor IOC list.", True),
     ]),
    (TicketKind.incident, "Access / Login",
     "Cannot login to ServiceNow", "ServiceNow shows 'invalid credentials' even with correct password.",
     TicketPriority.high, TicketStatus.in_progress,
     "kabir.khanna@octoassist.demo", "rohan.kumar@octoassist.demo",
     []),
    (TicketKind.incident, "Hardware Issue",
     "External monitor not detected", "Dell WD22 dock — external monitor goes black after laptop sleep.",
     TicketPriority.low, TicketStatus.on_hold,
     "dev.chopra@octoassist.demo", "vikram.desai@octoassist.demo",
     [("vikram.desai@octoassist.demo", "KB-22 has the workaround. Permanent fix needs WD22 firmware push.", False)]),
    (TicketKind.incident, "Software Issue",
     "Office app activation error 0x80070005", "All Office apps show 'Activation required' since this morning.",
     TicketPriority.medium, TicketStatus.open,
     "riya.banerjee@octoassist.demo", None,
     []),
]


def _seed_one_ticket(
    db: Session, tenant: Tenant, *,
    kind: TicketKind, category: Category, title: str, description: str,
    priority: TicketPriority, status: TicketStatus,
    reporter: User, assignee: User | None, comments: list[tuple[str, str, bool]],
    users_by_email: dict[str, User], number_offset: int,
    forced_n: int | None = None,
) -> Ticket:
    prefix = "INC" if kind == TicketKind.incident else "SR"
    n = forced_n if forced_n is not None else (db.query(Ticket).filter(Ticket.tenant_id == tenant.id, Ticket.kind == kind).count()) + 1
    created = NOW() - timedelta(days=random.randint(0, 14), hours=random.randint(0, 23))

    # Compute SLA targets
    multipliers = {TicketPriority.critical: 0.25, TicketPriority.high: 0.5,
                   TicketPriority.medium: 1.0, TicketPriority.low: 2.0}
    mult = multipliers.get(priority, 1.0)
    due_response = created + timedelta(minutes=int(category.sla_response_minutes * mult))
    due_resolution = created + timedelta(minutes=int(category.sla_resolution_minutes * mult))

    t = Ticket(
        tenant_id=tenant.id,
        ticket_number=f"{prefix}-{n:05d}",
        kind=kind, category_id=category.id,
        title=title, description=description,
        priority=priority, status=status,
        reporter_id=reporter.id,
        assignee_id=assignee.id if assignee else None,
        created_at=created,
        updated_at=created + timedelta(hours=random.randint(0, 6)),
        due_response_at=due_response,
        due_resolution_at=due_resolution,
    )
    if status in (TicketStatus.resolved, TicketStatus.closed):
        t.first_response_at = created + timedelta(minutes=random.randint(20, 240))
        t.resolved_at = t.first_response_at + timedelta(minutes=random.randint(60, 480))
    if status == TicketStatus.closed:
        t.closed_at = (t.resolved_at or created) + timedelta(hours=random.randint(2, 48))
    db.add(t)
    db.flush()

    # Audit: created event
    db.add(TicketEvent(
        ticket_id=t.id, actor_id=reporter.id, kind=TicketEventKind.created,
        after_value={"ticket_number": t.ticket_number, "title": title, "priority": priority.value, "category": category.name, "kind": kind.value},
        created_at=created,
    ))
    if assignee:
        db.add(TicketEvent(
            ticket_id=t.id, actor_id=None, kind=TicketEventKind.assigned,
            after_value={"assignee_id": assignee.id, "assignee_email": assignee.email},
            created_at=created + timedelta(minutes=5),
        ))

    # Comments + audit events for each
    for author_email, body, is_internal in comments:
        author = users_by_email.get(author_email)
        if author is None:
            continue
        when = created + timedelta(minutes=random.randint(15, 240))
        db.add(TicketComment(
            ticket_id=t.id, author_id=author.id, body=body,
            is_internal=is_internal, created_at=when,
        ))
        db.add(TicketEvent(
            ticket_id=t.id, actor_id=author.id, kind=TicketEventKind.comment_added,
            after_value={"body_preview": body[:120], "is_internal": is_internal},
            created_at=when,
        ))

    if status != TicketStatus.open:
        db.add(TicketEvent(
            ticket_id=t.id, actor_id=(assignee.id if assignee else None),
            kind=TicketEventKind.status_changed,
            before_value={"status": "open"},
            after_value={"status": status.value},
            created_at=t.updated_at,
        ))
    return t


def seed_tickets(db: Session, tenant: Tenant, force: bool) -> None:
    existing = db.query(Ticket).filter(Ticket.tenant_id == tenant.id).count()
    if existing >= len(TICKETS) and not force:
        log.info("tickets: already %d, skipping", existing)
        return
    users_by_email = {u.email: u for u in db.query(User).filter(User.tenant_id == tenant.id).all()}
    cats_by_name = {c.name: c for c in db.query(Category).filter(Category.tenant_id == tenant.id).all()}

    # Sort so existing tickets keep their numbers
    seeded_titles = {t.title for t in db.query(Ticket).filter(Ticket.tenant_id == tenant.id).all()}
    # Per-kind counter — keeps numbering correct without relying on autoflush
    counters = {
        TicketKind.incident: db.query(Ticket).filter(Ticket.tenant_id == tenant.id, Ticket.kind == TicketKind.incident).count() + 1,
        TicketKind.service_request: db.query(Ticket).filter(Ticket.tenant_id == tenant.id, Ticket.kind == TicketKind.service_request).count() + 1,
    }
    added = 0
    for spec in TICKETS:
        kind, cat_name, title, desc, prio, status, reporter_email, assignee_email, comments = spec
        if title in seeded_titles:
            continue
        cat = cats_by_name.get(cat_name)
        reporter = users_by_email.get(reporter_email)
        if cat is None or reporter is None:
            continue
        assignee = users_by_email.get(assignee_email) if assignee_email else None
        _seed_one_ticket(
            db, tenant,
            kind=kind, category=cat, title=title, description=desc,
            priority=prio, status=status,
            reporter=reporter, assignee=assignee, comments=comments,
            users_by_email=users_by_email, number_offset=0,
            forced_n=counters[kind],
        )
        counters[kind] += 1
        added += 1
    db.commit()
    log.info("tickets: added=%d total=%d", added, db.query(Ticket).filter(Ticket.tenant_id == tenant.id).count())


# ---------------------------------------------------------------------------
# Problems — 8
# ---------------------------------------------------------------------------

PROBLEMS = [
    ("Recurring Outlook crashes after Office update",
     "Multiple finance team members report Outlook crashing on launch since the Oct Office update.",
     TicketPriority.high, ProblemStatus.workaround_in_place,
     "Office build 16.0.18000.x has a launch-crash bug triggered by a specific add-in.",
     "Run Outlook in safe mode and disable third-party add-ins; KB-04 has the steps.",
     "rohan.kumar@octoassist.demo"),
    ("VPN drops on Mumbai office segment",
     "Multiple users on the Mumbai HQ network experience VPN disconnects every 30 mins.",
     TicketPriority.high, ProblemStatus.investigating,
     "",
     "",
     "vikram.desai@octoassist.demo"),
    ("Slow logins on Friday afternoons (5-6 PM IST)",
     "Group Policy refresh storm at end-of-week causing 60-120s login delays.",
     TicketPriority.medium, ProblemStatus.root_cause_identified,
     "GPO refresh is scheduled for the same window across all OUs, creating CPU contention on DCs.",
     "Stay logged in until 18:30 IST. Permanent fix tracked in CHG-00009.",
     "anjali.iyer@octoassist.demo"),
    ("BitLocker recovery prompts on boot",
     "Sporadic BitLocker recovery requests on boot for laptops < 1 year old.",
     TicketPriority.medium, ProblemStatus.investigating,
     "",
     "",
     "rohan.kumar@octoassist.demo"),
    ("Phishing campaign targeting finance team",
     "Active campaign impersonating CEO asking for wire transfers.",
     TicketPriority.critical, ProblemStatus.workaround_in_place,
     "Threat actor sending from typo-squatted lookalike domains.",
     "Sender domains blocked at the email gateway. Awareness training scheduled.",
     "priya.shetty@octoassist.demo"),
    ("SharePoint site unavailable on legacy IE",
     "Some users still on Internet Explorer can't access modern SharePoint sites.",
     TicketPriority.low, ProblemStatus.resolved,
     "Microsoft deprecated IE for SharePoint Online effective last quarter.",
     "All users migrated to Edge. IE no longer supported per company policy.",
     "nisha.menon@octoassist.demo"),
    ("Printer fleet random offline events",
     "Printers in Mumbai HQ go offline randomly across all floors.",
     TicketPriority.low, ProblemStatus.investigating,
     "",
     "Reboot print spooler service on the print server (5 min hand-fix).",
     "vikram.desai@octoassist.demo"),
    ("2FA failures spike on weekends",
     "Slack alerts firing for unusually high 2FA failure rate on Saturday/Sunday.",
     TicketPriority.medium, ProblemStatus.investigating,
     "",
     "Monitoring; no user impact yet.",
     "arjun.bhatt@octoassist.demo"),
]


def seed_problems(db: Session, tenant: Tenant, force: bool) -> None:
    existing = db.query(Problem).filter(Problem.tenant_id == tenant.id).count()
    if existing >= len(PROBLEMS) and not force:
        log.info("problems: already %d, skipping", existing)
        return
    users_by_email = {u.email: u for u in db.query(User).filter(User.tenant_id == tenant.id).all()}
    seeded = {p.title for p in db.query(Problem).filter(Problem.tenant_id == tenant.id).all()}
    # Local counter — session has autoflush=False so count() doesn't see in-flight rows
    next_n = db.query(Problem).filter(Problem.tenant_id == tenant.id).count() + 1
    added = 0
    for title, desc, prio, status, root_cause, workaround, assignee_email in PROBLEMS:
        if title in seeded:
            continue
        n = next_n
        next_n += 1
        reporter = users_by_email.get("aaron.fernandes@octoassist.demo") or next(iter(users_by_email.values()))
        assignee = users_by_email.get(assignee_email) if assignee_email else None
        created = NOW() - timedelta(days=random.randint(2, 30))
        p = Problem(
            tenant_id=tenant.id,
            problem_number=f"PRB-{n:05d}",
            title=title, description=desc,
            priority=prio, status=status,
            root_cause=root_cause, workaround=workaround,
            reporter_id=reporter.id,
            assignee_id=assignee.id if assignee else None,
            created_at=created,
            updated_at=created + timedelta(hours=random.randint(2, 240)),
        )
        if status in (ProblemStatus.resolved, ProblemStatus.closed):
            p.resolved_at = created + timedelta(days=random.randint(1, 14))
        db.add(p)
        added += 1
    db.commit()
    log.info("problems: added=%d total=%d", added, db.query(Problem).filter(Problem.tenant_id == tenant.id).count())


# ---------------------------------------------------------------------------
# Changes — 14
# ---------------------------------------------------------------------------

CHANGES = [
    # (title, type, risk, status, description, impact, rollback)
    ("Upgrade PostgreSQL to 17", ChangeType.normal, ChangeRisk.medium, ChangeStatus.completed,
     "Upgrade primary database from PG16 to PG17 for security and feature reasons.",
     "30 mins downtime on internal apps; HRMS read-only window.",
     "pg_dumpall before; restore on PG16 cluster if upgrade fails."),
    ("Microsoft Defender policy update", ChangeType.standard, ChangeRisk.low, ChangeStatus.completed,
     "Enable cloud-delivered protection on all endpoints.",
     "No user impact.",
     "Revert via GPO if endpoint performance degrades."),
    ("Azure AD conditional access tightening", ChangeType.normal, ChangeRisk.high, ChangeStatus.approved,
     "Block legacy auth, require MFA from outside India.",
     "Users in legacy clients (older Outlook) must update first; communications sent.",
     "Disable conditional access policy."),
    ("Patch wave — April Cumulative Update", ChangeType.normal, ChangeRisk.medium, ChangeStatus.completed,
     "Roll out April 2026 Windows + Office CU to all endpoints in waves.",
     "Endpoints reboot during patch window (after-hours).",
     "Pause patch deployment via Intune; uninstall problematic patches if needed."),
    ("VPN concentrator reboot — emergency", ChangeType.emergency, ChangeRisk.high, ChangeStatus.completed,
     "VPN concentrator showing high CPU; reboot to clear.",
     "20 mins of VPN unavailability, late evening.",
     "If failover doesn't recover, fall back to spare concentrator."),
    ("SSL cert renewal for hrms.thirdoctopus.com", ChangeType.standard, ChangeRisk.low, ChangeStatus.completed,
     "Annual cert renewal. Standard process.",
     "30s of HTTPS unavailability during certbot reload.",
     "Restore previous cert from /etc/letsencrypt/archive."),
    ("Migrate file server FS-MUM-01 to FS-MUM-02", ChangeType.normal, ChangeRisk.high, ChangeStatus.in_progress,
     "Move file shares to new server with bigger storage and snapshots.",
     "Phased per department; each move requires 30 min file lock.",
     "Reverse robocopy + DFS namespace pointer reverts to old server."),
    ("Rotate service account passwords", ChangeType.standard, ChangeRisk.low, ChangeStatus.completed,
     "Quarterly rotation of all service account passwords.",
     "Brief auth failures if any system caches credentials beyond rotation window.",
     "Restore from password vault snapshot."),
    ("Decommission old print server", ChangeType.normal, ChangeRisk.medium, ChangeStatus.draft,
     "Remove PRINT-MUM-OLD after migration to PRINT-MUM-01.",
     "No user impact — server is unused for 30 days.",
     "Power back on; re-add to AD if anyone reports issues."),
    ("Implement DLP policy for sensitive files", ChangeType.normal, ChangeRisk.medium, ChangeStatus.under_review,
     "Microsoft Purview DLP policy to block external sharing of files marked Confidential.",
     "Users may see new prompts when sharing externally.",
     "Set DLP policy to 'audit only' mode."),
    ("Roll out Microsoft Authenticator passwordless", ChangeType.normal, ChangeRisk.low, ChangeStatus.draft,
     "Enable passwordless sign-in for all users via Microsoft Authenticator.",
     "Users opt-in initially; mandatory rollout after 60 days.",
     "Disable passwordless capability in Entra; password sign-in remains."),
    ("Emergency patch — CVE-2026-XXXX", ChangeType.emergency, ChangeRisk.high, ChangeStatus.completed,
     "Out-of-band patch for actively-exploited Windows vulnerability.",
     "Endpoint reboot required.",
     "Uninstall the KB; isolate affected endpoints if exploitation suspected."),
    ("Enable Conditional Access for OctoAssist tenant", ChangeType.normal, ChangeRisk.medium, ChangeStatus.in_progress,
     "Require trusted location + MFA for /admin and /settings routes.",
     "Admins on untrusted networks must complete MFA.",
     "Disable CA policy in Entra portal."),
    ("Deprecate SHA-1 in internal CA", ChangeType.normal, ChangeRisk.medium, ChangeStatus.under_review,
     "Move all internal services to SHA-256 only.",
     "Legacy clients (Win 7 / Server 2008) will fail TLS — none in scope.",
     "Revert internal CA configuration."),
]


def seed_changes(db: Session, tenant: Tenant, force: bool) -> None:
    existing = db.query(Change).filter(Change.tenant_id == tenant.id).count()
    if existing >= len(CHANGES) and not force:
        log.info("changes: already %d, skipping", existing)
        return
    users_by_email = {u.email: u for u in db.query(User).filter(User.tenant_id == tenant.id).all()}
    requester = users_by_email.get("aaron.fernandes@octoassist.demo") or next(iter(users_by_email.values()))
    approver  = users_by_email.get("anjali.iyer@octoassist.demo") or requester
    implementer = users_by_email.get("rohan.kumar@octoassist.demo") or requester

    seeded = {c.title for c in db.query(Change).filter(Change.tenant_id == tenant.id).all()}
    next_n = db.query(Change).filter(Change.tenant_id == tenant.id).count() + 1
    added = 0
    for spec in CHANGES:
        title, ctype, risk, status, desc, impact, rollback = spec
        if title in seeded:
            continue
        n = next_n
        next_n += 1
        created = NOW() - timedelta(days=random.randint(1, 60))
        c = Change(
            tenant_id=tenant.id,
            change_number=f"CHG-{n:05d}",
            title=title, description=desc,
            change_type=ctype, risk=risk, status=status,
            impact=impact, rollback_plan=rollback,
            requester_id=requester.id,
            implementer_id=implementer.id if status in (ChangeStatus.in_progress, ChangeStatus.completed, ChangeStatus.failed) else None,
            planned_start=created + timedelta(days=random.randint(2, 14)),
            planned_end=created + timedelta(days=random.randint(2, 14), hours=random.randint(1, 6)),
            created_at=created,
            updated_at=created + timedelta(hours=random.randint(1, 240)),
        )
        if status in (ChangeStatus.approved, ChangeStatus.in_progress, ChangeStatus.completed,
                      ChangeStatus.failed, ChangeStatus.rejected):
            c.cab_approver_id = approver.id
            c.cab_decision_at = created + timedelta(hours=random.randint(8, 48))
            c.cab_decision_note = "Reviewed; approved." if status != ChangeStatus.rejected else "Insufficient rollback plan."
        if status in (ChangeStatus.in_progress, ChangeStatus.completed, ChangeStatus.failed):
            c.actual_start = c.cab_decision_at + timedelta(hours=random.randint(2, 24))
        if status in (ChangeStatus.completed, ChangeStatus.failed):
            c.actual_end = c.actual_start + timedelta(hours=random.randint(1, 6))
        db.add(c)
        db.flush()

        # Events
        db.add(ChangeEvent(change_id=c.id, actor_id=requester.id, kind=ChangeEventKind.created,
                           after_value={"change_number": c.change_number, "title": title}, created_at=created))
        if status not in (ChangeStatus.draft,):
            db.add(ChangeEvent(change_id=c.id, actor_id=requester.id, kind=ChangeEventKind.submitted_for_review,
                               created_at=created + timedelta(hours=1)))
        if status in (ChangeStatus.approved, ChangeStatus.in_progress, ChangeStatus.completed, ChangeStatus.failed):
            db.add(ChangeEvent(change_id=c.id, actor_id=approver.id, kind=ChangeEventKind.approved,
                               note=c.cab_decision_note, created_at=c.cab_decision_at))
        if status == ChangeStatus.rejected:
            db.add(ChangeEvent(change_id=c.id, actor_id=approver.id, kind=ChangeEventKind.rejected,
                               note=c.cab_decision_note, created_at=c.cab_decision_at))
        if status in (ChangeStatus.in_progress, ChangeStatus.completed, ChangeStatus.failed):
            db.add(ChangeEvent(change_id=c.id, actor_id=implementer.id, kind=ChangeEventKind.started,
                               created_at=c.actual_start))
        if status == ChangeStatus.completed:
            db.add(ChangeEvent(change_id=c.id, actor_id=implementer.id, kind=ChangeEventKind.completed,
                               created_at=c.actual_end))
        if status == ChangeStatus.failed:
            db.add(ChangeEvent(change_id=c.id, actor_id=implementer.id, kind=ChangeEventKind.failed,
                               note="Rolled back per plan.", created_at=c.actual_end))
        added += 1
    db.commit()
    log.info("changes: added=%d total=%d", added, db.query(Change).filter(Change.tenant_id == tenant.id).count())


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def run(force: bool = False) -> None:
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).first()
        if tenant is None:
            log.error("No tenant. Start the app at least once first.")
            return
        log.info("Seeding for tenant: %s", tenant.name)
        seed_users(db, tenant, force=force)
        seed_kb(db, tenant, force=force)
        seed_assets(db, tenant, force=force)
        seed_tickets(db, tenant, force=force)
        seed_problems(db, tenant, force=force)
        seed_changes(db, tenant, force=force)
        log.info("DONE.")
    finally:
        db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true", help="Seed even if data already exists")
    args = p.parse_args()
    run(force=args.force)

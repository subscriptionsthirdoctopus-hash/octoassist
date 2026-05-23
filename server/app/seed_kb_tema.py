"""KB seed pack tailored to a TEMA India-style heavy-engineering / process-
equipment manufacturer (heat exchangers, pressure vessels, columns).

Context the articles assume:
- Engineering design teams using AutoCAD / SolidWorks / PV Elite / HTRI.
- Shop floor at a Bhandup / Mangalore-style plant, NC / CNC machines, MES.
- SAP B1 / SAP S/4HANA for ERP, sales, purchase, despatch.
- Microsoft 365 for email + Teams; VPN for off-site users; Intune for BYOD.
- ISO 9001 / 14001 / 45001 / 27001 posture — auditable, formal change control.

All slugs are namespaced 'tema-' so re-running the seeder is idempotent and
doesn't collide with generic articles authored later in the UI.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import (
    KbArticle, KbArticleStatus, KbArticleVisibility, KbCategory, Tenant, User, UserRole,
)

log = logging.getLogger("octoassist.seed_kb_tema")


KB_CATEGORIES: list[tuple[str, str, int]] = [
    # (name, description, sort_order)
    ("Getting Started",        "First-day setup, email, Wi-Fi, password, Teams.",                 10),
    ("Engineering Software",   "AutoCAD, SolidWorks, PV Elite, HTRI, Aveva — install + licence.", 20),
    ("ERP & Business Apps",    "SAP, MES, despatch, quality, document control.",                  30),
    ("Network & Remote Access", "Office Wi-Fi, plant network, VPN, BYOD.",                        40),
    ("Security & Compliance",  "ISO 27001 essentials, phishing, USB policy, OT/ICS rules.",       50),
    ("Hardware & Workstations", "Design workstations, plant terminals, printers, plotters.",      60),
]


def _ensure_kb_categories(db: Session, tenant: Tenant) -> dict[str, KbCategory]:
    out: dict[str, KbCategory] = {}
    for name, desc, sort_order in KB_CATEGORIES:
        cat = (db.query(KbCategory)
                 .filter(KbCategory.tenant_id == tenant.id, KbCategory.name == name)
                 .first())
        if cat is None:
            cat = KbCategory(
                tenant_id=tenant.id, name=name,
                description=desc, sort_order=sort_order,
            )
            db.add(cat)
            db.flush()
        out[name] = cat
    return out


# Article spec: (slug, title, category_name, visibility, summary, body)
ARTICLES: list[tuple[str, str, str, KbArticleVisibility, str, str]] = [
    # ---------------- Getting Started ----------------
    ("tema-first-day-setup",
     "First day — accounts, email, Wi-Fi, Teams",
     "Getting Started",
     KbArticleVisibility.portal,
     "What to set up on day 1: M365 account, corporate Wi-Fi, MS Teams, MFA.",
     """**Welcome to the company.** IT pre-creates your Microsoft 365 account before
your join date. On day 1 the IT desk hands you a one-page printout with:

- Your corporate email (`firstname.lastname@<company>.com`)
- A temporary password — change it on first sign-in
- Wi-Fi SSID + 802.1X user/password for the office network

**Steps on your work laptop**
1. Sign in to Windows with your corporate email + temp password.
2. Open **Microsoft Authenticator** on your phone and complete MFA enrolment when prompted.
   This is mandatory — without MFA you cannot access email or SAP.
3. Open **Outlook** — it auto-configures from your sign-in.
4. Open **Microsoft Teams** — same auto-sign-in. Pin the channels for your department and project.
5. Bookmark the **OctoAssist self-service portal** (this site, `/portal`) — for IT tickets.

**If anything above fails**, raise a ticket via the portal under *Onboarding* category.
The IT team has a 4-business-hour response SLA on new joiners.
"""),

    ("tema-password-reset",
     "Reset your Microsoft 365 password",
     "Getting Started",
     KbArticleVisibility.portal,
     "Self-service password reset via Microsoft Entra ID. No IT ticket needed.",
     """If you have **already enrolled MFA**, you can reset your own password
without raising a ticket.

1. Go to <https://passwordreset.microsoftonline.com>
2. Enter your corporate email + the captcha.
3. Verify using Microsoft Authenticator (preferred) or SMS.
4. Set the new password — minimum 12 characters, one uppercase, one number, one symbol.
5. Wait 1–2 minutes, then sign in again on your laptop with the new password.
   Outlook, Teams and Windows credential prompts will all re-authenticate.

**If you have NOT enrolled MFA** (typically first-day joiners), call the IT desk
or raise a ticket under *Access / Login* category — we can reset for you after
verifying your identity through your reporting manager.

**Account locked out?** 10 wrong attempts = 30-minute auto-lock. Wait it out
or raise a ticket if it's urgent.
"""),

    # ---------------- Engineering Software ----------------
    ("tema-request-cad-licence",
     "Request an AutoCAD / SolidWorks / PV Elite licence",
     "Engineering Software",
     KbArticleVisibility.portal,
     "How to request a paid CAD / engineering software licence; approval flow.",
     """Engineering licences are seat-limited and cost-tracked, so every assignment
goes through a formal request + approval flow.

**To request a licence**
1. Open the portal → *New Request* → category **Software Install**.
2. In the description, include:
   - **Software + edition** (e.g. *AutoCAD 2024 Mechanical*, *SolidWorks 2024 Premium*, *PV Elite 2024*).
   - **Business justification** — which project, who's the project lead, design deliverable.
   - **Your workstation hostname** — find it on **OctoAssist → /assets** or on a sticker on the machine.
   - **Manager's name** for approval.
3. Submit. The ticket auto-routes to the Engineering IT queue.

**What happens next**
- *Engineering IT* validates licence availability against the floating-licence pool.
- If a seat is free, install proceeds within 1 business day.
- If the pool is full, the request goes to the *Engineering Head* for either
  (a) a re-allocation from a colleague who no longer needs it, or
  (b) procurement approval for a new seat.

**SLA**: response 8h, resolution 1–3 days depending on procurement.
**Do not download installers from the internet** — all engineering software
comes from the licensed media on the IT file server.
""" ),

    ("tema-cad-licence-checkout-issues",
     "AutoCAD / SolidWorks licence not picking up",
     "Engineering Software",
     KbArticleVisibility.portal,
     "Troubleshoot floating-licence checkout failures from the network licence server.",
     """Engineering CAD apps run against a floating licence server on the LAN.
If you see *“No licence available”* or the app falls back to *Viewer mode*,
follow these steps before raising a ticket.

**Step 1 — confirm you can reach the licence server**
Open **Command Prompt** and run:
```
ping cad-lic.<company>.local
```
If ping fails, you're either off the corporate network or the server is down.
On VPN? Disconnect and reconnect — some VPN profiles don't route the licence subnet.

**Step 2 — close any orphaned AutoCAD / SolidWorks process**
If your machine crashed earlier, a phantom process may still be holding the
licence. Task Manager → end any `acad.exe` / `sldworks.exe` / `solidworkslauncher.exe`.

**Step 3 — restart the licence-borrow service**
```
net stop FLEXnet Licensing Service 64
net start FLEXnet Licensing Service 64
```
(needs admin; the IT desk can do this remotely)

**Step 4 — if peak hours (10:00–17:00) and pool is full**
There's no quick fix — raise a ticket under *Software Issue* tagged
*Engineering Licence*. We can either nudge a colleague to release, or
provision a borrowed-licence token for offline use.
"""),

    # ---------------- ERP & Business Apps ----------------
    ("tema-sap-login-issues",
     "Cannot log in to SAP — what to try first",
     "ERP & Business Apps",
     KbArticleVisibility.portal,
     "SAP login fails: account, password, network, or SAP GUI version.",
     """Before raising a ticket, run these four checks.

**1. Confirm your SAP user is still active**
Open OctoAssist → *My Profile* (top right). If your *Role* is anything other
than *active*, your account was deactivated — typically during offboarding or
extended-leave processes. Raise a *Onboarding* ticket to reactivate.

**2. Password sync between AD and SAP**
SAP authenticates against the same Active Directory password as your Windows
login. If you changed your Windows password in the last 60 minutes, wait —
SAP picks it up on the next half-hour sync.

**3. Network reachability**
Open Command Prompt and run:
```
telnet sap-prod.<company>.local 3200
```
If it doesn't connect within 5 seconds, you're off-network. Connect via VPN
(see *VPN connection* article) and retry.

**4. SAP GUI version drift**
We're standardised on **SAP GUI 8.00 patch level 9** for Windows. Older
versions fail handshakes on the new TLS-enforced production server. Check
your version: SAP Logon → top-left icon → *About SAP Logon*. If it's older,
raise a *Software Install* ticket.

If all four pass and you still can't log in, raise *Access / Login* — high
priority, 1h response SLA.
"""),

    ("tema-erp-purchase-order",
     "Raise a purchase order in SAP — quick reference",
     "ERP & Business Apps",
     KbArticleVisibility.portal,
     "Step-by-step for raising a standard PO; who approves at each ladder rung.",
     """This is the **standard PO path** for indirect / consumables / engineering
materials. For project-tagged shop-floor materials, use transaction **ME21N**
with the project WBS — your project lead has the WBS code.

**Steps**
1. SAP Logon → transaction **ME21N** (Create Purchase Order).
2. Document type **NB** (Standard PO).
3. Vendor — pick from the approved vendor master. If your vendor isn't there,
   raise a *Vendor Master* ticket first (separate workflow with finance review).
4. Item rows — material code, quantity, plant, storage location.
   Net price defaults from the info record; override only with manager approval.
5. Header → *Texts* — paste the *PR reference number*.
6. Save → take note of the PO number.

**Approval ladder (auto-routed by SAP workflow)**
- < ₹50,000 — your department head
- ₹50,000 – ₹5,00,000 — function head
- ₹5,00,000 – ₹25,00,000 — director
- > ₹25,00,000 — MD + finance committee

**If the approval is stuck for > 24 hours**, raise an IT ticket under
*Software Issue* tagged *SAP-Workflow* — we can re-trigger the workflow from
the workflow inbox without touching the document.
"""),

    # ---------------- Network & Remote Access ----------------
    ("tema-vpn-connection",
     "VPN connection for off-site / work-from-home",
     "Network & Remote Access",
     KbArticleVisibility.portal,
     "Connect to the corporate network from outside via the Sophos / GlobalProtect VPN.",
     """Off-site users need VPN to reach SAP, design servers, MES dashboards, and
intranet pages. The corporate VPN is **always-on for managed laptops** —
it should auto-connect within 30 seconds of the laptop reaching the internet.

**If VPN does NOT auto-connect**
1. Click the VPN system-tray icon → *Connect*.
2. Sign in with your corporate email + Windows password.
3. Complete MFA prompt on Microsoft Authenticator (push notification).

**Symptoms that mean VPN is up but routing is wrong**
- You can ping `8.8.8.8` but `sap-prod.<company>.local` doesn't resolve.
- *Solution*: VPN system-tray → *Reset DNS*, then retry.

**Symptoms that mean MFA is rejecting you**
- Push notification doesn't appear on phone.
- *Solution*: open Authenticator → *Refresh* — the phone may have lost time-sync.
  If still failing, raise *Access / Login* ticket.

**Personal devices (BYOD)**
Personal devices need Intune enrolment first — see the *BYOD enrolment* article.
Without Intune, the VPN gateway will reject the connection regardless of
correct credentials.
"""),

    ("tema-plant-network-rules",
     "Plant / OT network — what you must NEVER do",
     "Network & Remote Access",
     KbArticleVisibility.portal,
     "Hard rules for anyone whose laptop reaches the plant / OT VLAN.",
     """The plant / OT network carries traffic for CNC controllers, PLCs, MES
terminals, SCADA HMIs and quality-instrument workstations. Even one wrong
packet can stop the line.

**Strict rules — auditable, no exceptions**
1. **Never** plug a personal laptop, USB drive, mobile phone, or tablet into
   any plant-network port or shop-floor workstation. This is an ISO 27001 finding
   AND a production-safety risk.
2. **Never** install software on a plant-floor terminal. They are sealed
   golden-image machines — installing anything breaks the validation envelope.
3. **Never** run a network scan (nmap, ping sweep, port scan) from any
   machine on the plant VLAN. These trigger the OT IDS and may cause PLCs to
   fail-safe.
4. **Never** disable Windows Defender, the corporate AV, or the host firewall
   on any plant-network machine. If the AV is causing a problem, raise a
   ticket — IT will provide a controlled exclusion.

**If you need legitimate access**
Raise a *VPN Access* ticket and specify *Plant OT subnet*. This requires
**Engineering Head + Plant Head dual approval** and creates a time-bound,
audited access — typically 8 working hours.

**If you suspect an OT incident** (PLC hung, terminal frozen, weird HMI
behaviour) — call the Plant IT desk by phone IMMEDIATELY. Don't try to
fix it remotely. Don't power-cycle anything. Don't disconnect any cable.
"""),

    # ---------------- Security & Compliance ----------------
    ("tema-phishing-reporting",
     "Suspect a phishing email — how to report it",
     "Security & Compliance",
     KbArticleVisibility.portal,
     "The 60-second rule: don't click, don't reply, report via the Outlook button.",
     """Phishing is the **#1 way** attackers get into manufacturing companies.
TEMA-style organisations are common targets because design IP and procurement
flows are valuable.

**The 60-second rule**
1. **Don't click** any link.
2. **Don't reply** — even *“please remove me”*.
3. **Don't download** any attachment.
4. **Don't forward** to colleagues to ask their opinion.

**What to do instead**
Use the **Report Phishing** button in Outlook (Home tab, right side). The
email is forwarded to Microsoft + the IT security mailbox and then deleted
from your inbox. That's it. You can stop reading.

**If you already clicked / replied / downloaded**
- Disconnect your laptop from Wi-Fi + LAN immediately.
- Call the IT desk by **phone** — do NOT email.
- Don't power off the machine — we may need the live memory for forensics.
- Raise a *Security Concern* ticket (critical priority, 30-min response SLA).

**Common red flags in real phishing**
- Sender domain looks almost-right (`@temaiindia.com` instead of `@tema-india.com`).
- Urgency language (*“within 1 hour”*, *“before COB”*, *“legal action”*).
- Generic greeting (*“Dear customer”*, *“Dear user”*).
- Link previews showing a domain you've never seen.
- *“Click here to view the invoice”* / *“Verify your password”* / *“Update banking details”*.
"""),

    ("tema-usb-policy",
     "USB drives and removable media — policy",
     "Security & Compliance",
     KbArticleVisibility.portal,
     "Where USBs are allowed, where they're blocked, and how to share files instead.",
     """Removable media is the second-largest infection vector after phishing. The
corporate policy is straightforward.

**By default**: USB mass-storage is **blocked** on all managed laptops and on
every plant-network workstation. Plugging a USB drive in will result in:
- The drive being read-only.
- A pop-up notification.
- A security event logged in OctoAssist under *Security Concern*.

**Approved exceptions**
- Engineering Design (CAD model exchange with vendors) — request via
  *Software Install* ticket tagged *USB-Write Exception*.
- Quality / Lab (instrument data export) — pre-approved per workstation,
  no ticket needed.
- Plant maintenance (PLC firmware updates) — strictly via the IT-controlled
  *engineering-locker* USB drive, signed out from the Plant IT desk.

**To share files with someone — use these instead**
- **OneDrive** (within the company) — open the file → Share → enter colleague's email.
- **SharePoint** (project folders) — drop in the project library; permissions
  inherit from the SharePoint site.
- **External vendors** — request a *Secure Transfer* ticket; IT generates a
  time-limited download link.

**Personal USB drives are never permitted** on any machine that has access
to plant systems or design servers, even if your role normally allows USBs.
"""),

    # ---------------- Hardware & Workstations ----------------
    ("tema-design-workstation-issue",
     "Design workstation is slow / crashing",
     "Hardware & Workstations",
     KbArticleVisibility.internal,
     "Triage workflow for CAD workstation performance / stability issues.",
     """**Information to capture first** (paste in the ticket):
- Hostname (visible on OctoAssist → /assets, or the sticker on the chassis).
- Time the issue started.
- Application(s) crashing — AutoCAD, SolidWorks, PV Elite, HTRI?
- Was a Windows Update applied recently? Check via Windows Settings → Update history.
- Is the issue reproducible with a specific file/project? Or random?

**Self-help before escalation**
1. Restart the machine — schedule it during your lunch break.
2. Check disk free space — design machines need ≥20% free for AutoCAD undo file.
   Right-click `C:` → Properties. If < 20%, clean the AutoCAD `Plot.log` /
   SolidWorks temp folder under `%TEMP%`.
3. Check RAM utilisation under load — Task Manager → Performance → Memory.
   If you're regularly at > 90% with AutoCAD + SolidWorks open, that's an
   upgrade case, not a bug.
4. Confirm GPU driver — open NVIDIA Control Panel → System Information.
   Drift from the approved version causes SolidWorks graphics glitches.

**Common root causes the IT team has seen**
- A Windows Update KB pushed an incompatible chipset driver — IT keeps a
  rollback runbook.
- Antivirus scanning the SolidWorks PDM vault folder — IT adds the path
  to the exclusion list.
- Failed disk SMART status — replace under warranty.

**SLA**: hardware fault = same-business-day replacement laptop; performance
tuning = 2 business days.
"""),
]


def seed_tema_kb(db: Session, tenant: Tenant) -> None:
    """Idempotent KB seeder for TEMA India-style content."""
    # Need an author user — prefer admin, but any active staff works.
    author = (db.query(User)
                .filter(User.tenant_id == tenant.id,
                        User.role.in_([UserRole.admin, UserRole.agent]))
                .order_by(User.id).first())

    cats = _ensure_kb_categories(db, tenant)
    now = datetime.now(timezone.utc)
    created = 0
    for slug, title, cat_name, vis, summary, body in ARTICLES:
        if db.query(KbArticle).filter(KbArticle.slug == slug).first() is not None:
            continue
        article = KbArticle(
            tenant_id=tenant.id,
            category_id=cats[cat_name].id,
            slug=slug,
            title=title,
            summary=summary,
            body=body,
            status=KbArticleStatus.published,
            visibility=vis,
            author_id=author.id if author else None,
            published_at=now,
        )
        db.add(article)
        created += 1
    if created:
        db.commit()
        log.info("Seeded %d TEMA-style KB articles across %d categories",
                 created, len(cats))

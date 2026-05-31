#!/usr/bin/env python3
"""Seed demo-quality data into the Third Octopus tenant — 7 consultants,
projects, timesheets, expenses, status updates from 1 Jan 2026 → today.

Idempotent: re-running won't duplicate users/clients/engagements; will
only ADD timesheets/entries/expenses that don't already exist.

Run via:
    docker exec -i octoflow-app python /tmp/seed_demo.py
"""
import hashlib
import random
import sys
from datetime import date, datetime, timedelta, timezone

from app.database import SessionLocal
from app.models import (
    Activity, Assignment, Client, Engagement, EngagementStage, EngagementTask,
    Expense, ExpenseCategory, ExpenseStatus, Milestone, MilestoneStatus,
    ProjectStatus, RagStatus, StageStatus, StatusUpdate, Tenant, TimeEntry,
    Timesheet, TimesheetPeriod, TimesheetStatus, User, UserRole,
)
from app.security import hash_password


TENANT_NAME = "Third Octopus"
DEMO_PW     = "Demo2026!"
TODAY       = date(2026, 5, 31)
START       = date(2026, 1, 1)


CONSULTANTS = [
    # (display_name, email, role, practice)
    ("Ranjit Yadav",   "ranjit.yadav@thirdoctopus.com",   "manager",    "Cloud & Infra"),
    ("Ashutosh Patti", "ashutosh.patti@thirdoctopus.com", "manager",    "Cybersecurity"),
    ("Ritesh Gupta",   "ritesh.gupta@thirdoctopus.com",   "consultant", "SAP & ITSM"),
    ("Moin Shah",      "moin.shah@thirdoctopus.com",      "consultant", "Cloud Architecture"),
    ("Navneet Kumar",  "navneet.kumar@thirdoctopus.com",  "consultant", "Compliance & Audit"),
    ("Aryan Gupta",    "aryan.gupta@thirdoctopus.com",    "consultant", "M365 & Endpoint"),
    ("Farhan Sharif",  "farhan.sharif@thirdoctopus.com",  "consultant", "DR & Workplace"),
]

REPORTS_TO = {
    "ranjit.yadav@thirdoctopus.com":   "arun@thirdoctopus.com",
    "ashutosh.patti@thirdoctopus.com": "arun@thirdoctopus.com",
    "moin.shah@thirdoctopus.com":      "ranjit.yadav@thirdoctopus.com",
    "ritesh.gupta@thirdoctopus.com":   "ranjit.yadav@thirdoctopus.com",
    "aryan.gupta@thirdoctopus.com":    "ranjit.yadav@thirdoctopus.com",
    "navneet.kumar@thirdoctopus.com":  "ashutosh.patti@thirdoctopus.com",
    "farhan.sharif@thirdoctopus.com":  "ashutosh.patti@thirdoctopus.com",
}

NEW_ENGAGEMENTS = [
    # (client_code, code, name, description, start, end, owner_email)
    ("SUN",    "AUDIT",  "IT GxP Audit & Remediation",
     "Periodic IT controls audit for Sun Pharma manufacturing — 21 CFR Part 11, "
     "computer system validation, gap remediation across 4 plants.",
     date(2026, 1, 12), date(2026, 4, 30), "ashutosh.patti@thirdoctopus.com"),
    ("SUN",    "DR",     "DR/BCP Architecture",
     "Active-passive DR design across Mumbai + Hyderabad data centres; "
     "BCP playbooks for regulated GxP systems.",
     date(2026, 4, 6),  date(2026, 7, 31), "ranjit.yadav@thirdoctopus.com"),
    ("IMMAST", "PORTAL", "Digital Workplace Portal",
     "M365 SharePoint-based collaboration portal + governance; replaces "
     "legacy intranet built on classic ASP.",
     date(2026, 3, 2),  date(2026, 6, 30), "ranjit.yadav@thirdoctopus.com"),
]

ASSIGNMENTS = {
    "CSCRF":    ["ashutosh.patti@thirdoctopus.com", "navneet.kumar@thirdoctopus.com"],
    "CLOUD":    ["ranjit.yadav@thirdoctopus.com",   "moin.shah@thirdoctopus.com"],
    "OCTOASSI": ["ritesh.gupta@thirdoctopus.com",   "aryan.gupta@thirdoctopus.com"],
    "HELPDESK": ["aryan.gupta@thirdoctopus.com",    "farhan.sharif@thirdoctopus.com"],
    "DISCOVER": ["farhan.sharif@thirdoctopus.com",  "ritesh.gupta@thirdoctopus.com"],
    "PORTAL":   ["ranjit.yadav@thirdoctopus.com",   "aryan.gupta@thirdoctopus.com",
                 "ritesh.gupta@thirdoctopus.com"],
    "AUDIT":    ["ashutosh.patti@thirdoctopus.com", "navneet.kumar@thirdoctopus.com"],
    "DR":       ["ranjit.yadav@thirdoctopus.com",   "farhan.sharif@thirdoctopus.com"],
}

# Primary activity per engagement (defaults to ADV)
ACT_FOR_ENG = {
    "CSCRF":    "ADV",
    "CLOUD":    "IMP",
    "OCTOASSI": "IMP",
    "HELPDESK": "RUN",
    "DISCOVER": "ADV",
    "PORTAL":   "IMP",
    "AUDIT":    "ADV",
    "DR":       "ADV",
}

STAGES = {
    "CSCRF":    ["Gap Assessment", "Policy Drafting", "Control Implementation",
                 "Internal Audit", "Closure"],
    "CLOUD":    ["Landing Zone Design", "Network & Identity", "Workload Migration",
                 "Run-mode Handover"],
    "OCTOASSI": ["Discovery", "Configuration", "Pilot", "Roll-out", "Hypercare"],
    "HELPDESK": ["Onboarding", "Operating Cadence", "Continuous Improvement",
                 "Periodic Review"],
    "DISCOVER": ["Stakeholder Interviews", "Current-State Map",
                 "Future-State Vision", "Roadmap"],
    "PORTAL":   ["Information Architecture", "Build", "Content Migration", "Launch"],
    "AUDIT":    ["Scoping", "Fieldwork", "Findings & Report", "Remediation Tracking"],
    "DR":       ["DR Strategy", "Architecture Design", "Build & Test", "Documentation"],
}

MILESTONES = {
    "CSCRF":    [(14,  "Gap report signed off"),
                 (45,  "Policy framework approved by ISC"),
                 (90,  "Controls operational"),
                 (140, "Internal audit clean")],
    "CLOUD":    [(20,  "Landing zone deployed"),
                 (60,  "Hub-spoke + Entra Connect live"),
                 (90,  "Workload wave-1 cut over"),
                 (130, "Wave-2 + handover")],
    "OCTOASSI": [(20,  "Configuration baselined"),
                 (50,  "Pilot acceptance"),
                 (90,  "Full roll-out"),
                 (130, "Hypercare exit")],
    "DISCOVER": [(20,  "Stakeholder map complete"),
                 (50,  "Current-state report"),
                 (80,  "Future-state vision"),
                 (110, "Roadmap signed")],
    "PORTAL":   [(25,  "IA approved"),
                 (60,  "Build complete"),
                 (90,  "Content migrated"),
                 (115, "Go-live")],
    "AUDIT":    [(15,  "Scope locked"),
                 (45,  "Fieldwork complete"),
                 (70,  "Findings issued"),
                 (105, "Remediation closed")],
    "DR":       [(20,  "Strategy paper"),
                 (50,  "HLD signed"),
                 (90,  "First DR drill"),
                 (115, "Documentation handover")],
}


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def stable_rand(*keys) -> random.Random:
    h = hashlib.sha256(":".join(map(str, keys)).encode()).hexdigest()
    return random.Random(int(h[:8], 16))


def _note_for(r, eng_id, engs_by_code) -> str:
    code = next((c for c, e in engs_by_code.items() if e.id == eng_id), "")
    pool = {
        "CSCRF":    ["Gap closure: SOC controls", "Drafting ISMS clauses",
                     "Workshop with InfoSec team", "Evidence walkthrough with auditor",
                     "Policy review with CISO"],
        "CLOUD":    ["Landing zone Bicep updates", "Network peering tests",
                     "Hub-spoke firewall rules", "Wave-2 cutover prep",
                     "Entra Connect sync rules"],
        "OCTOASSI": ["Configuring SLA workflows", "Helpdesk agent training",
                     "Catalog + form tuning", "Integration with Entra ID",
                     "Hypercare ticket grooming"],
        "HELPDESK": ["Tier-2 ticket triage", "Run-book updates",
                     "Weekly KPI review", "Process improvement sprint"],
        "DISCOVER": ["Stakeholder interviews", "Current-state map drafting",
                     "Pain-point synthesis", "Roadmap option workshop"],
        "PORTAL":   ["IA review with sponsors", "Component library build",
                     "Content migration scripts", "Pre-launch QA"],
        "AUDIT":    ["Walk-through with IT ops", "Sampling + evidence collection",
                     "Findings draft", "Remediation tracker review"],
        "DR":       ["DR architecture HLD", "Active/passive failover design",
                     "Run-book authoring", "Tabletop exercise"],
    }
    return r.choice(pool.get(code, ["Project work"]))


def main():
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.name == TENANT_NAME).first()
        if not tenant:
            print(f"!! tenant {TENANT_NAME!r} not found", file=sys.stderr); sys.exit(1)
        T = tenant.id
        print(f"== tenant: {tenant.name} (id={T})")

        arun = db.query(User).filter(User.email == "arun@thirdoctopus.com",
                                     User.tenant_id == T).first()
        if not arun:
            print("!! Arun admin not found", file=sys.stderr); sys.exit(1)

        # ─── 1. CONSULTANTS ──────────────────────────────────────────
        users_by_email = {u.email: u for u in db.query(User).filter(User.tenant_id == T).all()}
        added = 0
        for name, email, role, practice in CONSULTANTS:
            if email in users_by_email:
                continue
            u = User(tenant_id=T, email=email, display_name=name,
                     role=UserRole(role), practice=practice,
                     password_hash=hash_password(DEMO_PW), is_active=True)
            db.add(u); db.flush()
            users_by_email[email] = u
            added += 1
            print(f"  + user {email} · {role:10s} · {practice}")
        db.commit()
        print(f"== users added: {added}; total in tenant: {len(users_by_email)}")

        # Manager hierarchy
        for email, mgr_email in REPORTS_TO.items():
            u = users_by_email.get(email); mgr = users_by_email.get(mgr_email)
            if u and mgr and u.manager_id != mgr.id:
                u.manager_id = mgr.id
        db.commit()

        # ─── 2. ENGAGEMENTS ─────────────────────────────────────────
        clients_by_code     = {c.code: c for c in db.query(Client).filter(Client.tenant_id == T).all()}
        engagements_by_code = {e.code: e for e in db.query(Engagement).filter(Engagement.tenant_id == T).all()}

        for cli_code, code, name, desc, sd, ed, owner_email in NEW_ENGAGEMENTS:
            if code in engagements_by_code:
                continue
            cli = clients_by_code.get(cli_code)
            if not cli:
                print(f"  ?? client {cli_code} missing — skip {code}"); continue
            owner = users_by_email.get(owner_email)
            e = Engagement(tenant_id=T, client_id=cli.id, code=code, name=name,
                           description=desc, status=ProjectStatus.active,
                           start_date=sd, end_date=ed,
                           owner_id=owner.id if owner else None)
            db.add(e); db.flush()
            db.add(EngagementTask(engagement_id=e.id, name="Delivery", sort_order=1))
            db.add(EngagementTask(engagement_id=e.id, name="Review",   sort_order=2))
            engagements_by_code[code] = e
            print(f"  + engagement {cli_code}/{code} · {name}")
        db.commit()

        # Backfill owners on existing engagements that have none
        owner_overrides = {
            "CSCRF":    "ashutosh.patti@thirdoctopus.com",
            "CLOUD":    "ranjit.yadav@thirdoctopus.com",
            "OCTOASSI": "ritesh.gupta@thirdoctopus.com",
            "HELPDESK": "aryan.gupta@thirdoctopus.com",
            "DISCOVER": "farhan.sharif@thirdoctopus.com",
        }
        for code, owner_email in owner_overrides.items():
            e = engagements_by_code.get(code); o = users_by_email.get(owner_email)
            if e and o and e.owner_id is None:
                e.owner_id = o.id
                # Backfill start/end if missing
                if not e.start_date: e.start_date = date(2026, 1, 5)
                if not e.end_date:   e.end_date   = date(2026, 6, 30)
        db.commit()

        # ─── 3. ASSIGNMENTS ──────────────────────────────────────────
        existing_assigns = {(a.user_id, a.engagement_id) for a in db.query(Assignment).all()}
        new_a = 0
        for code, emails in ASSIGNMENTS.items():
            e = engagements_by_code.get(code)
            if not e: continue
            for email in emails:
                u = users_by_email.get(email)
                if not u: continue
                if (u.id, e.id) in existing_assigns: continue
                db.add(Assignment(user_id=u.id, engagement_id=e.id))
                new_a += 1
        db.commit()
        print(f"== assignments added: {new_a}")

        # ─── 4. STAGES ──────────────────────────────────────────────
        for code, stage_names in STAGES.items():
            e = engagements_by_code.get(code)
            if not e: continue
            if db.query(EngagementStage).filter(EngagementStage.engagement_id == e.id).count() > 0:
                continue
            for i, sn in enumerate(stage_names):
                if e.start_date and e.end_date:
                    span = (e.end_date - e.start_date).days or 1
                    progress = (TODAY - e.start_date).days / span
                else:
                    progress = 0.5
                hi = (i + 1) / len(stage_names)
                lo = i       / len(stage_names)
                if   progress >= hi: st = StageStatus.completed
                elif progress >= lo: st = StageStatus.in_progress
                else:                st = StageStatus.not_started
                db.add(EngagementStage(engagement_id=e.id, name=sn,
                                       sort_order=i + 1, status=st))
        db.commit()

        # ─── 5. MILESTONES ──────────────────────────────────────────
        for code, ms in MILESTONES.items():
            e = engagements_by_code.get(code)
            if not e or not e.start_date: continue
            if db.query(Milestone).filter(Milestone.engagement_id == e.id).count() > 0:
                continue
            for offset, name in ms:
                due = e.start_date + timedelta(days=offset)
                if due <= TODAY - timedelta(days=7):
                    st = MilestoneStatus.met
                    met_at = datetime.combine(due, datetime.min.time()).replace(tzinfo=timezone.utc)
                elif due <= TODAY + timedelta(days=14):
                    st = MilestoneStatus.in_progress; met_at = None
                else:
                    st = MilestoneStatus.planned;     met_at = None
                db.add(Milestone(engagement_id=e.id, name=name, due_date=due,
                                 status=st, met_at=met_at))
        db.commit()

        # ─── 6. STATUS UPDATES (one per engagement per month) ───────
        for code, emails in ASSIGNMENTS.items():
            e = engagements_by_code.get(code)
            if not e: continue
            lead = users_by_email.get(emails[0])
            if not lead: continue
            existing_updates = {su.period_start for su in
                                db.query(StatusUpdate).filter(StatusUpdate.engagement_id == e.id)}
            for month in range(1, 6):
                # Mon of last full week of the month
                d = date(2026, month, 28)
                while d.month == month:
                    d += timedelta(days=1)
                wk = monday_of(d - timedelta(days=1))
                if wk in existing_updates or wk > TODAY:
                    continue
                r = stable_rand("statusup", code, month)
                p = r.random()
                rag = (RagStatus.green if p < 0.78 else
                       RagStatus.amber if p < 0.94 else RagStatus.red)
                summary = (
                    f"{e.name}: month-end checkpoint. Workstream progressing per plan; "
                    f"team utilisation steady around 82-88%."
                )
                blockers = ("None outstanding." if rag == RagStatus.green
                            else "Pending client sign-off on scope clarification; "
                                 "no impact to critical path yet.")
                next_week = ("Continue per project plan; weekly client review "
                             "Tuesday; deliverable handover Thursday.")
                db.add(StatusUpdate(engagement_id=e.id, author_id=lead.id,
                                    period_start=wk, rag=rag,
                                    summary=summary, blockers=blockers,
                                    next_week=next_week))
        db.commit()

        # ─── 7. TIMESHEETS + ENTRIES ────────────────────────────────
        activities = {a.code: a for a in db.query(Activity).filter(Activity.tenant_id == T).all()}
        ADV = activities.get("ADV"); IMP = activities.get("IMP")
        INT = activities.get("INT"); RUN = activities.get("RUN")
        LVE = activities.get("LVE"); TRV = activities.get("TRV")
        act_lookup = {"ADV": ADV, "IMP": IMP, "RUN": RUN, "TRV": TRV}

        tasks_by_eng = {}
        for e in db.query(Engagement).filter(Engagement.tenant_id == T).all():
            tasks_by_eng[e.id] = [t for t in e.tasks if t.is_active]

        # user_id → [(engagement_id, primary_activity_id)]
        user_engs = {}
        for code, emails in ASSIGNMENTS.items():
            e = engagements_by_code.get(code)
            if not e: continue
            act_obj = act_lookup.get(ACT_FOR_ENG.get(code, "ADV"), ADV)
            for email in emails:
                u = users_by_email.get(email)
                if not u or u.role == UserRole.admin: continue
                user_engs.setdefault(u.id, []).append((e.id, act_obj.id))

        # Mondays Mon 2025-12-29 (which contains Jan 1) → current week
        first_mon = monday_of(START)
        last_mon  = monday_of(TODAY)
        mondays   = []
        m = first_mon
        while m <= last_mon:
            mondays.append(m); m += timedelta(days=7)

        periods_by_mon = {p.start_date: p for p in
                          db.query(TimesheetPeriod).filter(TimesheetPeriod.tenant_id == T).all()}
        for mon in mondays:
            if mon not in periods_by_mon:
                p = TimesheetPeriod(tenant_id=T, start_date=mon,
                                    end_date=mon + timedelta(days=6))
                db.add(p); db.flush()
                periods_by_mon[mon] = p
        db.commit()

        added_sheets  = 0
        added_entries = 0
        for uid, engs in user_engs.items():
            for mon in mondays:
                period = periods_by_mon[mon]
                sheet = (db.query(Timesheet)
                           .filter(Timesheet.user_id == uid,
                                   Timesheet.period_id == period.id).first())
                if sheet and sheet.entries:
                    continue
                if not sheet:
                    sheet = Timesheet(tenant_id=T, user_id=uid,
                                      period_id=period.id,
                                      status=TimesheetStatus.draft)
                    db.add(sheet); db.flush()
                    added_sheets += 1

                r = stable_rand("sheet", uid, mon.isoformat())
                any_entries = False
                for offset in range(5):  # Mon..Fri
                    d = mon + timedelta(days=offset)
                    if d < START or d > TODAY:
                        continue

                    if r.random() < 0.04:
                        db.add(TimeEntry(timesheet_id=sheet.id, entry_date=d,
                                         engagement_id=engs[0][0], task_id=None,
                                         activity_id=LVE.id if LVE else None,
                                         hours=8.0, is_billable=False,
                                         note="PTO / personal leave"))
                        added_entries += 1; any_entries = True
                        continue

                    total = round(r.uniform(7.5, 8.5) * 2) / 2
                    n_engs = 1 if len(engs) == 1 else (2 if r.random() < 0.55 else 1)
                    picks  = r.sample(engs, n_engs)

                    if n_engs == 1:
                        bh = round((total - r.uniform(0.5, 1.0)) * 2) / 2
                        bh = max(4.0, min(total - 0.5, bh))
                        ih = total - bh
                        eng_id, act_id = picks[0]
                        tasks = tasks_by_eng.get(eng_id, [])
                        tid = tasks[0].id if tasks else None
                        db.add(TimeEntry(timesheet_id=sheet.id, entry_date=d,
                                         engagement_id=eng_id, task_id=tid,
                                         activity_id=act_id, hours=bh,
                                         is_billable=True,
                                         note=_note_for(r, eng_id, engagements_by_code)))
                        added_entries += 1; any_entries = True
                        if ih > 0:
                            db.add(TimeEntry(timesheet_id=sheet.id, entry_date=d,
                                             engagement_id=eng_id, task_id=None,
                                             activity_id=INT.id if INT else None,
                                             hours=ih, is_billable=False,
                                             note="Internal admin / standup"))
                            added_entries += 1
                    else:
                        split = round(r.uniform(0.4, 0.6) * total * 2) / 2
                        for i, (eng_id, act_id) in enumerate(picks):
                            h = split if i == 0 else total - split
                            tasks = tasks_by_eng.get(eng_id, [])
                            tid = tasks[0].id if tasks else None
                            db.add(TimeEntry(timesheet_id=sheet.id, entry_date=d,
                                             engagement_id=eng_id, task_id=tid,
                                             activity_id=act_id, hours=h,
                                             is_billable=True,
                                             note=_note_for(r, eng_id, engagements_by_code)))
                            added_entries += 1; any_entries = True

                # Set sheet status by week age
                cutoff_approved  = date(2026, 5, 4)   # weeks before this → approved
                cutoff_submitted = date(2026, 5, 25)  # weeks before this → submitted
                if not any_entries:
                    sheet.status = TimesheetStatus.draft
                elif mon < cutoff_approved:
                    sheet.status = TimesheetStatus.approved
                    sheet.submitted_at = datetime.combine(
                        mon + timedelta(days=6), datetime.min.time()
                    ).replace(tzinfo=timezone.utc) + timedelta(hours=18)
                    sheet.approved_at  = sheet.submitted_at + timedelta(hours=14)
                    sheet.approver_id  = arun.id
                elif mon < cutoff_submitted:
                    sheet.status = TimesheetStatus.submitted
                    sheet.submitted_at = datetime.combine(
                        mon + timedelta(days=6), datetime.min.time()
                    ).replace(tzinfo=timezone.utc) + timedelta(hours=19)
                else:
                    sheet.status = TimesheetStatus.draft
            db.commit()
        print(f"== timesheets added: {added_sheets} · entries added: {added_entries}")

        # ─── 8. EXPENSES ─────────────────────────────────────────────
        cats = {c.code: c for c in db.query(ExpenseCategory).filter(ExpenseCategory.tenant_id == T).all()}
        templates = [
            # (cat_code, amount_range, description, billable)
            ("TRV", (200,  800),  "Ola to client office",                  True),
            ("TRV", (180,  600),  "Uber for client meeting",               True),
            ("TRV", (2400, 7500), "Domestic flight — Mumbai ↔ Hyderabad",  True),
            ("TRV", (3500, 9000), "Domestic flight — onsite engagement",   True),
            ("LDG", (3800, 9500), "Hotel — 2 nights onsite",               True),
            ("MEA", (350,  1400), "Working lunch with client team",        True),
            ("MEA", (240,  800),  "Team meal during onsite",               True),
            ("SUB", (180,  450),  "Coffee/tea during client workshops",    False),
            ("MIL", (450,  1200), "Car mileage to client site",            True),
            ("SFT", (1800, 6500), "Tooling subscription — annual",         False),
            ("OTH", (300,  1500), "Printing + workshop collateral",        False),
        ]

        added_exp = 0
        for _, email, _, _ in CONSULTANTS:
            u = users_by_email.get(email)
            if not u: continue
            if db.query(Expense).filter(Expense.user_id == u.id).count() > 0:
                continue
            r = stable_rand("exp", u.id)
            n = r.randint(6, 9)
            their_engs = [eid for (eid, _) in user_engs.get(u.id, [])]
            for _ in range(n):
                day_offset = r.randint(0, (TODAY - START).days - 1)
                ed = START + timedelta(days=day_offset)
                while ed.weekday() >= 5:
                    ed += timedelta(days=1)
                if ed > TODAY:
                    ed = TODAY - timedelta(days=1)
                cat_code, (lo, hi), desc, billable = r.choice(templates)
                cat = cats.get(cat_code)
                amt = round(r.uniform(lo, hi), 2)
                eid = r.choice(their_engs) if their_engs and billable else None

                age = (TODAY - ed).days
                if   age > 50: st = ExpenseStatus.reimbursed
                elif age > 25: st = ExpenseStatus.approved
                elif age > 10: st = ExpenseStatus.submitted
                else:           st = ExpenseStatus.draft
                if r.random() < 0.10 and st == ExpenseStatus.submitted:
                    st = ExpenseStatus.approved

                submitted_at  = datetime.combine(ed + timedelta(days=2),
                                                 datetime.min.time()
                                ).replace(tzinfo=timezone.utc) + timedelta(hours=17)
                approved_at   = submitted_at + timedelta(days=2)
                reimbursed_at = approved_at  + timedelta(days=4)

                ex = Expense(
                    tenant_id=T, user_id=u.id, expense_date=ed,
                    category_id=cat.id if cat else None,
                    engagement_id=eid, amount=amt, currency="INR",
                    description=desc, is_billable=billable, status=st,
                    submitted_at=submitted_at if st != ExpenseStatus.draft else None,
                    approver_id=arun.id if st in (ExpenseStatus.approved,
                                                  ExpenseStatus.reimbursed) else None,
                    approved_at=approved_at if st in (ExpenseStatus.approved,
                                                      ExpenseStatus.reimbursed) else None,
                    reimbursed_at=reimbursed_at if st == ExpenseStatus.reimbursed else None,
                    reimbursed_by_id=arun.id   if st == ExpenseStatus.reimbursed else None,
                )
                db.add(ex); added_exp += 1
        db.commit()
        print(f"== expenses added: {added_exp}")

        # ─── 9. SUMMARY ─────────────────────────────────────────────
        print()
        print("════════════════════════════ FINAL COUNTS ════════════════════════════")
        print(f"  users:        {db.query(User).filter(User.tenant_id == T).count()}")
        print(f"  engagements:  {db.query(Engagement).filter(Engagement.tenant_id == T).count()}")
        print(f"  assignments:  {db.query(Assignment).join(User, User.id==Assignment.user_id).filter(User.tenant_id==T).count()}")
        print(f"  periods:      {db.query(TimesheetPeriod).filter(TimesheetPeriod.tenant_id == T).count()}")
        print(f"  timesheets:   {db.query(Timesheet).filter(Timesheet.tenant_id == T).count()}")
        print(f"  time entries: {db.query(TimeEntry).join(Timesheet).filter(Timesheet.tenant_id == T).count()}")
        print(f"  expenses:     {db.query(Expense).filter(Expense.tenant_id == T).count()}")
        print(f"  stages:       {db.query(EngagementStage).join(Engagement).filter(Engagement.tenant_id == T).count()}")
        print(f"  milestones:   {db.query(Milestone).join(Engagement).filter(Engagement.tenant_id == T).count()}")
        print(f"  status updt:  {db.query(StatusUpdate).join(Engagement).filter(Engagement.tenant_id == T).count()}")
        print()
        print(f"  Demo password for all 7 consultants: {DEMO_PW!r}")
    finally:
        db.close()


if __name__ == "__main__":
    main()

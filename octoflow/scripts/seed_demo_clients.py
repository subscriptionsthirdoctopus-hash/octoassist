#!/usr/bin/env python3
"""Adds 8 more clients to Third Octopus tenant — Ablepay, ICICI Bank,
Asian Paints, HDFC AMC, Invesco, 3 PIM Investment, Dani, SNG Partners.
HDFC Capital + TEMA India already exist (HCAL, TEMA codes), so they're
skipped here.

For each new client, one engagement is added with realistic start/end
dates, 2 consultants assigned, stages + milestones + monthly status
updates. Then time entries are spliced into existing timesheets on
1-2 days per week so the new engagements show actual delivered hours.

Idempotent: skips clients/engagements that exist by code; skips
time-entry seeding for any (sheet, day, engagement) tuple that already
has an entry.

Run via:
    rsync scripts/seed_demo_clients.py octoassist:/opt/octoflow/scripts/
    ssh octoassist 'docker cp /opt/octoflow/scripts/seed_demo_clients.py \
                    octoflow-app:/tmp/ && \
                    docker exec octoflow-app python /tmp/seed_demo_clients.py'
"""
import hashlib
import random
import sys
from datetime import date, datetime, timedelta, timezone

from app.database import SessionLocal
from app.models import (
    Activity, Assignment, Client, Engagement, EngagementStage, EngagementTask,
    Milestone, MilestoneStatus, ProjectStatus, RagStatus, StageStatus,
    StatusUpdate, Tenant, TimeEntry, Timesheet, TimesheetPeriod,
    User, UserRole,
)


TENANT_NAME = "Third Octopus"
TODAY       = date(2026, 5, 31)
START       = date(2026, 1, 1)


# (code, name) — HCAL + TEMA already exist in the DB; skipped here
NEW_CLIENTS = [
    ("ABLE",    "Ablepay"),
    ("ICICI",   "ICICI Bank"),
    ("ASIAN",   "Asian Paints"),
    ("HDFCAMC", "HDFC Asset Management Company"),
    ("INVES",   "Invesco"),
    ("3PIM",    "3 PIM Investment Advisors"),
    ("DANI",    "Dani"),
    ("SNG",     "SNG Partners"),
]


# (client_code, eng_code, eng_name, description, start, end, owner_email,
#  primary_activity, [assigned consultants])
NEW_ENGAGEMENTS = [
    ("ABLE", "RAMP", "Payment Platform Hardening",
     "Security hardening of Ablepay's UPI + card-acquiring stack — "
     "PCI-DSS gap closure, secrets rotation, threat model refresh.",
     date(2026, 3, 2),  date(2026, 6, 30),
     "ashutosh.patti@thirdoctopus.com", "ADV",
     ["ashutosh.patti@thirdoctopus.com", "navneet.kumar@thirdoctopus.com"]),

    ("ICICI", "DSAR", "DPDP Act Tooling & DSAR Workflow",
     "DPDP Act 2023 readiness for ICICI Bank — data subject rights "
     "request workflow, consent register, breach playbook integration.",
     date(2026, 2, 2),  date(2026, 5, 29),
     "navneet.kumar@thirdoctopus.com", "ADV",
     ["navneet.kumar@thirdoctopus.com", "ashutosh.patti@thirdoctopus.com"]),

    ("ASIAN", "M365", "Hybrid Workplace Roll-out",
     "M365 + Intune + Entra deployment for Asian Paints HO + factory "
     "users — co-management, autopilot, ZTNA pilot.",
     date(2026, 1, 12), date(2026, 6, 30),
     "aryan.gupta@thirdoctopus.com", "IMP",
     ["aryan.gupta@thirdoctopus.com", "moin.shah@thirdoctopus.com"]),

    ("HDFCAMC", "ITGC", "IT General Controls Audit",
     "Annual ITGC audit for HDFC AMC — access controls, change "
     "management, data centre operations, third-party reviews.",
     date(2026, 1, 5),  date(2026, 4, 30),
     "ashutosh.patti@thirdoctopus.com", "ADV",
     ["ashutosh.patti@thirdoctopus.com", "navneet.kumar@thirdoctopus.com"]),

    ("INVES", "CMDB", "CMDB & Service Discovery",
     "Initial CMDB build for Invesco India ops — automated discovery, "
     "service mapping, integration with their existing ITSM.",
     date(2026, 3, 16), date(2026, 7, 31),
     "ritesh.gupta@thirdoctopus.com", "IMP",
     ["ritesh.gupta@thirdoctopus.com", "moin.shah@thirdoctopus.com"]),

    ("3PIM", "CYBER", "Cyber Posture Assessment",
     "Point-in-time cyber posture review for 3 PIM Investment — "
     "external attack surface, identity hygiene, incident response.",
     date(2026, 4, 13), date(2026, 6, 13),
     "ashutosh.patti@thirdoctopus.com", "ADV",
     ["ashutosh.patti@thirdoctopus.com", "farhan.sharif@thirdoctopus.com"]),

    ("DANI", "INFRA", "Cloud Cost Optimisation",
     "FinOps engagement for Dani group — Azure + AWS spend review, "
     "reserved-instance strategy, right-sizing roadmap.",
     date(2026, 2, 16), date(2026, 5, 22),
     "moin.shah@thirdoctopus.com", "ADV",
     ["moin.shah@thirdoctopus.com", "ranjit.yadav@thirdoctopus.com"]),

    ("SNG", "ITSM", "ServiceNow → OctoAssist Migration",
     "Migration from incumbent ServiceNow instance to OctoAssist for "
     "SNG Partners — workflow re-platforming + change freeze playbook.",
     date(2026, 4, 6),  date(2026, 8, 31),
     "ritesh.gupta@thirdoctopus.com", "IMP",
     ["ritesh.gupta@thirdoctopus.com", "aryan.gupta@thirdoctopus.com"]),
]


# 3-stage skeleton per engagement (works for any project type)
STAGE_TEMPLATES = {
    "RAMP":  ["Threat Model", "Hardening Sprint", "Pen-test + Sign-off"],
    "DSAR":  ["Discovery", "Workflow Build", "User Acceptance"],
    "M365":  ["Design", "Pilot", "Roll-out", "Hypercare"],
    "ITGC":  ["Planning", "Fieldwork", "Findings", "Closure"],
    "CMDB":  ["Discovery", "Modeling", "Population", "Handover"],
    "CYBER": ["Reconnaissance", "Assessment", "Report-out"],
    "INFRA": ["Spend Baseline", "Recommendations", "Implementation Sprint"],
    "ITSM":  ["Workflow Mapping", "Build", "Cutover", "Hypercare"],
}

# milestones (offset_days from start, name)
MILESTONE_TEMPLATES = {
    "RAMP":  [(20, "Threat model accepted"), (60, "Hardening sprint exit"),
              (95, "Pen-test clean"), (115, "Final report")],
    "DSAR":  [(20, "DPDP gap report"), (45, "Workflow MVP"),
              (80, "UAT sign-off"), (110, "Go-live")],
    "M365":  [(25, "Design baselined"), (60, "Pilot accept"),
              (110, "Roll-out wave-1"), (150, "Roll-out wave-2")],
    "ITGC":  [(15, "Walkthroughs done"), (45, "Sampling complete"),
              (75, "Draft findings"), (105, "Final report")],
    "CMDB":  [(25, "Discovery agents live"), (60, "Initial model"),
              (100, "Service maps validated"), (130, "Handover")],
    "CYBER": [(15, "Attack surface mapped"), (35, "Risk register"),
              (55, "Report-out")],
    "INFRA": [(15, "Spend baseline"), (40, "Optimisation plan"),
              (75, "Wave-1 savings realised")],
    "ITSM":  [(25, "Workflows mapped"), (60, "Build complete"),
              (100, "Cutover dry-run"), (135, "Go-live")],
}


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def stable_rand(*keys) -> random.Random:
    h = hashlib.sha256(":".join(map(str, keys)).encode()).hexdigest()
    return random.Random(int(h[:8], 16))


def _note(r, eng_code: str) -> str:
    pool = {
        "RAMP":  ["PCI evidence walkthrough", "Secrets rotation playbook",
                  "Threat model workshop", "Hardening backlog grooming"],
        "DSAR":  ["DPDP gap analysis", "DSAR workflow build",
                  "Consent register review", "Privacy notice draft"],
        "M365":  ["Intune compliance policies", "Autopilot lab",
                  "Conditional access design", "Wave-1 device pilot"],
        "ITGC":  ["IT ops walkthrough", "Change management sampling",
                  "Access review evidence", "Findings draft"],
        "CMDB":  ["Discovery probe install", "CI class modeling",
                  "Service map sprint", "Reconciliation rules"],
        "CYBER": ["External attack surface scan", "Identity hygiene review",
                  "IR playbook walkthrough"],
        "INFRA": ["Spend pattern analysis", "RI/SP modeling",
                  "Right-sizing recommendations"],
        "ITSM":  ["ServiceNow workflow extraction", "OctoAssist mapping sprint",
                  "Cutover rehearsal", "Hypercare desk setup"],
    }
    return r.choice(pool.get(eng_code, ["Project work"]))


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

        # ─── 1. CLIENTS ─────────────────────────────────────────────
        existing_codes = {c.code for c in db.query(Client).filter(Client.tenant_id == T).all()}
        added_clients = 0
        for code, name in NEW_CLIENTS:
            if code in existing_codes:
                print(f"  · skip client {code} (exists)")
                continue
            db.add(Client(tenant_id=T, code=code, name=name, is_active=True))
            added_clients += 1
            print(f"  + client {code} · {name}")
        db.commit()
        print(f"== clients added: {added_clients}")

        clients_by_code = {c.code: c for c in db.query(Client).filter(Client.tenant_id == T).all()}
        users_by_email  = {u.email: u for u in db.query(User).filter(User.tenant_id == T).all()}
        engagements_by_code = {e.code: e for e in db.query(Engagement).filter(Engagement.tenant_id == T).all()}

        # ─── 2. ENGAGEMENTS ─────────────────────────────────────────
        added_eng = 0
        for (cli_code, eng_code, name, desc, sd, ed, owner_email, primary_act,
             consultants) in NEW_ENGAGEMENTS:
            if eng_code in engagements_by_code:
                print(f"  · skip engagement {eng_code} (exists)")
                continue
            cli = clients_by_code.get(cli_code)
            owner = users_by_email.get(owner_email)
            if not cli:
                print(f"  ?? client {cli_code} missing — skip"); continue
            e = Engagement(tenant_id=T, client_id=cli.id, code=eng_code,
                           name=name, description=desc,
                           status=ProjectStatus.active,
                           start_date=sd, end_date=ed,
                           owner_id=owner.id if owner else None)
            db.add(e); db.flush()
            db.add(EngagementTask(engagement_id=e.id, name="Delivery", sort_order=1))
            db.add(EngagementTask(engagement_id=e.id, name="Review",   sort_order=2))
            engagements_by_code[eng_code] = e
            added_eng += 1
            print(f"  + engagement {cli_code}/{eng_code} · {name}")
        db.commit()
        print(f"== engagements added: {added_eng}")

        # ─── 3. ASSIGNMENTS ─────────────────────────────────────────
        existing_assigns = {(a.user_id, a.engagement_id)
                            for a in db.query(Assignment).all()}
        new_a = 0
        for (_, eng_code, _, _, _, _, _, _, consultants) in NEW_ENGAGEMENTS:
            e = engagements_by_code.get(eng_code)
            if not e: continue
            for email in consultants:
                u = users_by_email.get(email)
                if not u: continue
                if (u.id, e.id) in existing_assigns: continue
                db.add(Assignment(user_id=u.id, engagement_id=e.id))
                new_a += 1
        db.commit()
        print(f"== assignments added: {new_a}")

        # ─── 4. STAGES ──────────────────────────────────────────────
        for (_, eng_code, _, _, _, _, _, _, _) in NEW_ENGAGEMENTS:
            e = engagements_by_code.get(eng_code)
            if not e: continue
            if db.query(EngagementStage).filter(EngagementStage.engagement_id == e.id).count() > 0:
                continue
            names = STAGE_TEMPLATES.get(eng_code, ["Discovery", "Build", "Closure"])
            span = (e.end_date - e.start_date).days or 1
            progress = (TODAY - e.start_date).days / span
            for i, sn in enumerate(names):
                hi = (i + 1) / len(names)
                lo = i       / len(names)
                if   progress >= hi: st = StageStatus.completed
                elif progress >= lo: st = StageStatus.in_progress
                else:                st = StageStatus.not_started
                db.add(EngagementStage(engagement_id=e.id, name=sn,
                                       sort_order=i + 1, status=st))
        db.commit()

        # ─── 5. MILESTONES ──────────────────────────────────────────
        for (_, eng_code, _, _, _, _, _, _, _) in NEW_ENGAGEMENTS:
            e = engagements_by_code.get(eng_code)
            if not e: continue
            if db.query(Milestone).filter(Milestone.engagement_id == e.id).count() > 0:
                continue
            for offset, name in MILESTONE_TEMPLATES.get(eng_code, []):
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

        # ─── 6. STATUS UPDATES (monthly per new engagement) ─────────
        for (_, eng_code, _, _, sd, ed, _, _, consultants) in NEW_ENGAGEMENTS:
            e = engagements_by_code.get(eng_code)
            if not e: continue
            lead = users_by_email.get(consultants[0])
            if not lead: continue
            existing = {su.period_start for su in
                        db.query(StatusUpdate).filter(StatusUpdate.engagement_id == e.id)}
            for month in range(1, 6):
                # Last Monday of this month, only if engagement is active by then
                d = date(2026, month, 28)
                while d.month == month:
                    d += timedelta(days=1)
                wk = monday_of(d - timedelta(days=1))
                if wk in existing or wk > TODAY or wk < sd:
                    continue
                r = stable_rand("update", eng_code, month)
                p = r.random()
                rag = (RagStatus.green if p < 0.78 else
                       RagStatus.amber if p < 0.94 else RagStatus.red)
                db.add(StatusUpdate(
                    engagement_id=e.id, author_id=lead.id, period_start=wk,
                    rag=rag,
                    summary=f"{e.name}: month-end checkpoint. "
                            f"Workstream progressing per plan; client steerco satisfied.",
                    blockers=("None outstanding." if rag == RagStatus.green
                              else "Awaiting client decision on scope clarification — "
                                   "no critical path impact yet."),
                    next_week="Continue per plan; weekly client touchpoint Tuesday.",
                ))
        db.commit()

        # ─── 7. TIME ENTRIES on new engagements ─────────────────────
        # For each new engagement: for each assigned consultant, for each
        # weekday in [start, min(today, end)], add a 2-3h entry on 2 days
        # per week to their existing timesheet. We don't touch other
        # entries — this just appends new ones, so daily hours grow a bit.
        activities = {a.code: a for a in db.query(Activity).filter(Activity.tenant_id == T).all()}
        sheets_by_user_period = {(s.user_id, s.period_id): s
                                 for s in db.query(Timesheet).filter(Timesheet.tenant_id == T).all()}
        periods_by_mon = {p.start_date: p for p in
                          db.query(TimesheetPeriod).filter(TimesheetPeriod.tenant_id == T).all()}

        # Index existing (sheet_id, entry_date, engagement_id) tuples to skip
        existing_tuples = set()
        for te in db.query(TimeEntry).join(Timesheet).filter(Timesheet.tenant_id == T).all():
            existing_tuples.add((te.timesheet_id, te.entry_date, te.engagement_id))

        added_entries = 0
        for (_, eng_code, _, _, sd, ed, _, primary_act, consultants) in NEW_ENGAGEMENTS:
            e = engagements_by_code.get(eng_code)
            if not e: continue
            act = activities.get(primary_act) or activities.get("ADV")
            tasks = [t for t in e.tasks if t.is_active]
            tid = tasks[0].id if tasks else None
            for email in consultants:
                u = users_by_email.get(email)
                if not u: continue
                # Walk Mondays in engagement window
                first_mon = monday_of(max(sd, START))
                last_mon  = monday_of(min(TODAY, ed))
                m = first_mon
                while m <= last_mon:
                    period = periods_by_mon.get(m)
                    if not period:
                        m += timedelta(days=7); continue
                    sheet = sheets_by_user_period.get((u.id, period.id))
                    if not sheet:
                        m += timedelta(days=7); continue
                    r = stable_rand("xent", u.id, eng_code, m.isoformat())
                    # Pick 2 weekdays this week
                    weekdays = list(range(5))
                    r.shuffle(weekdays)
                    for offset in weekdays[:2]:
                        d = m + timedelta(days=offset)
                        if d < max(sd, START) or d > min(TODAY, ed):
                            continue
                        if (sheet.id, d, e.id) in existing_tuples:
                            continue
                        hours = round(r.uniform(1.5, 3.0) * 2) / 2
                        db.add(TimeEntry(
                            timesheet_id=sheet.id, entry_date=d,
                            engagement_id=e.id, task_id=tid,
                            activity_id=act.id if act else None,
                            hours=hours, is_billable=True,
                            note=_note(r, eng_code),
                        ))
                        added_entries += 1
                    m += timedelta(days=7)
        db.commit()
        print(f"== new time entries on new engagements: {added_entries}")

        # ─── 8. SUMMARY ─────────────────────────────────────────────
        print()
        print("════════════════════ FINAL COUNTS ════════════════════")
        print(f"  clients:      {db.query(Client).filter(Client.tenant_id == T).count()}")
        print(f"  engagements:  {db.query(Engagement).filter(Engagement.tenant_id == T).count()}")
        print(f"  assignments:  {db.query(Assignment).join(User, User.id==Assignment.user_id).filter(User.tenant_id==T).count()}")
        print(f"  time entries: {db.query(TimeEntry).join(Timesheet).filter(Timesheet.tenant_id == T).count()}")
        print(f"  stages:       {db.query(EngagementStage).join(Engagement).filter(Engagement.tenant_id == T).count()}")
        print(f"  milestones:   {db.query(Milestone).join(Engagement).filter(Engagement.tenant_id == T).count()}")
        print(f"  status updt:  {db.query(StatusUpdate).join(Engagement).filter(Engagement.tenant_id == T).count()}")
    finally:
        db.close()


if __name__ == "__main__":
    main()

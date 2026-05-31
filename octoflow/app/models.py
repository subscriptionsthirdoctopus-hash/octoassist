"""Phase 1 data model for OctoFlow.

The model is intentionally tight: just enough to support the 43 MVP
features (capture, project structure, approvals, users, basic reporting).
Phases 2 and 3 add rate cards, expenses, leave, multi-entity etc.
"""
import enum
from datetime import datetime, date, timezone

from sqlalchemy import (
    String, Integer, ForeignKey, DateTime, Date, Boolean, Enum, Text, Index,
    UniqueConstraint, Numeric,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────── Enums ───────────────────────────────

class UserRole(str, enum.Enum):
    admin       = "admin"        # full system control
    manager     = "manager"      # approves timesheets, sees team
    consultant  = "consultant"   # logs their own time
    finance     = "finance"      # downstream (Phase 2 invoicing)


class ProjectStatus(str, enum.Enum):
    active   = "active"
    on_hold  = "on_hold"
    closed   = "closed"


class TimesheetStatus(str, enum.Enum):
    draft     = "draft"
    submitted = "submitted"
    approved  = "approved"
    rejected  = "rejected"


# ─────────────────────────────── Tenant ──────────────────────────────

class Tenant(Base):
    """The customer organisation. Multi-tenant from day one even though
    the MVP only has one tenant (Third Octopus)."""
    __tablename__ = "tenants"
    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    name:       Mapped[str] = mapped_column(String(120), nullable=False)
    # Working calendar config — TS-140
    week_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                            doc="0=Monday, 6=Sunday")
    daily_hours:Mapped[float] = mapped_column(Numeric(4, 2), nullable=False, default=8.0,
                                              doc="Standard working hours per day")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    users:        Mapped[list["User"]]        = relationship(back_populates="tenant", cascade="all, delete-orphan")
    clients:      Mapped[list["Client"]]      = relationship(back_populates="tenant", cascade="all, delete-orphan")
    activities:   Mapped[list["Activity"]]    = relationship(back_populates="tenant", cascade="all, delete-orphan")
    holidays:     Mapped[list["Holiday"]]     = relationship(back_populates="tenant", cascade="all, delete-orphan")


# ─────────────────────────────── User ────────────────────────────────

class User(Base):
    """A staff member — consultant, manager, finance or admin (TS-127, TS-128)."""
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    id:           Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id:    Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    email:        Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role:         Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False, default=UserRole.consultant)
    password_hash:Mapped[str | None] = mapped_column(String(255), nullable=True)
    practice:     Mapped[str | None] = mapped_column(String(80), nullable=True,
                                                     doc="Practice / team grouping — TS-130")
    manager_id:   Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True,
                                                     doc="Reporting line — also default approver")
    is_active:    Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    tenant:       Mapped[Tenant] = relationship(back_populates="users")
    manager:      Mapped["User | None"] = relationship(remote_side="User.id")
    assignments:  Mapped[list["Assignment"]]  = relationship(back_populates="user", cascade="all, delete-orphan")
    timesheets:   Mapped[list["Timesheet"]]   = relationship(
        back_populates="user", cascade="all, delete-orphan",
        foreign_keys="Timesheet.user_id",
    )


# ─────────────────────────────── Client ──────────────────────────────

class Client(Base):
    """A client organisation (e.g. HCAL, TEMA India). TS-111."""
    __tablename__ = "clients"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_clients_tenant_code"),
    )

    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id:  Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    code:       Mapped[str] = mapped_column(String(20), nullable=False,
                                            doc="Short code, e.g. HCAL, TEMA, IMMAST")
    name:       Mapped[str] = mapped_column(String(160), nullable=False)
    is_active:  Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    tenant:     Mapped[Tenant] = relationship(back_populates="clients")
    engagements:Mapped[list["Engagement"]] = relationship(back_populates="client", cascade="all, delete-orphan")


# ──────────────────────────── Engagement / Project ────────────────────────────

class Engagement(Base):
    """An engagement / project under a client (TS-112)."""
    __tablename__ = "engagements"
    __table_args__ = (
        UniqueConstraint("client_id", "code", name="uq_engagements_client_code"),
        Index("ix_engagements_tenant_status", "tenant_id", "status"),
    )

    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id:  Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    client_id:  Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    code:       Mapped[str] = mapped_column(String(40), nullable=False,
                                            doc="Engagement code, e.g. CSCRF-2026")
    name:       Mapped[str] = mapped_column(String(200), nullable=False)
    description:Mapped[str] = mapped_column(Text, nullable=False, default="")
    status:     Mapped[ProjectStatus] = mapped_column(Enum(ProjectStatus), nullable=False, default=ProjectStatus.active)  # TS-114
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)   # TS-115
    end_date:   Mapped[date | None] = mapped_column(Date, nullable=True)
    owner_id:   Mapped[int | None]  = mapped_column(ForeignKey("users.id"), nullable=True,
                                                    doc="Engagement manager / approver — TS-123")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    client:     Mapped[Client] = relationship(back_populates="engagements")
    owner:      Mapped["User | None"] = relationship(foreign_keys=[owner_id])
    tasks:      Mapped[list["EngagementTask"]] = relationship(back_populates="engagement", cascade="all, delete-orphan")
    assignments:Mapped[list["Assignment"]]     = relationship(back_populates="engagement", cascade="all, delete-orphan")


class EngagementTask(Base):
    """A task / phase inside an engagement (TS-113)."""
    __tablename__ = "engagement_tasks"

    id:           Mapped[int] = mapped_column(Integer, primary_key=True)
    engagement_id:Mapped[int] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), nullable=False)
    name:         Mapped[str] = mapped_column(String(200), nullable=False)
    sort_order:   Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active:    Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    engagement:   Mapped[Engagement] = relationship(back_populates="tasks")


# ─────────────────────────────── Assignment ──────────────────────────

class Assignment(Base):
    """Maps a user onto an engagement they are allowed to log time
    against (TS-116, TS-117)."""
    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "engagement_id", name="uq_assignments_user_engagement"),
    )

    id:           Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id:      Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    engagement_id:Mapped[int] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), nullable=False)
    created_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    user:         Mapped[User]       = relationship(back_populates="assignments")
    engagement:   Mapped[Engagement] = relationship(back_populates="assignments")


# ─────────────────────────────── Activity ────────────────────────────

class Activity(Base):
    """Activity / work code (TS-118) — Advisory, Implementation, Travel,
    Internal, Training, etc."""
    __tablename__ = "activities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_activities_tenant_code"),
    )

    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id:  Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    code:       Mapped[str] = mapped_column(String(20), nullable=False)
    name:       Mapped[str] = mapped_column(String(80), nullable=False)
    is_billable_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                                      doc="Default billable flag when this activity is picked — TS-108")
    is_active:  Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    tenant:     Mapped[Tenant] = relationship(back_populates="activities")


# ─────────────────────────────── Timesheet period / sheet / entry ─────────────

class TimesheetPeriod(Base):
    """A weekly reporting cycle (TS-139). Weekly to start; later periods
    can be configurable."""
    __tablename__ = "timesheet_periods"
    __table_args__ = (
        UniqueConstraint("tenant_id", "start_date", name="uq_periods_tenant_start"),
    )

    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id:  Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False, doc="Monday of the week")
    end_date:   Mapped[date] = mapped_column(Date, nullable=False, doc="Sunday of the week")
    cutoff_at:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True,
                                                        doc="Hard submission deadline (Phase 2 enforces it)")
    is_locked:  Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Timesheet(Base):
    """A user's weekly timesheet for a given period."""
    __tablename__ = "timesheets"
    __table_args__ = (
        UniqueConstraint("user_id", "period_id", name="uq_timesheets_user_period"),
        Index("ix_timesheets_status", "tenant_id", "status"),
    )

    id:           Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id:    Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id:      Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    period_id:    Mapped[int] = mapped_column(ForeignKey("timesheet_periods.id", ondelete="CASCADE"), nullable=False)
    status:       Mapped[TimesheetStatus] = mapped_column(Enum(TimesheetStatus), nullable=False, default=TimesheetStatus.draft)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approver_id:  Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    user:    Mapped[User] = relationship(back_populates="timesheets", foreign_keys=[user_id])
    period:  Mapped[TimesheetPeriod] = relationship()
    entries: Mapped[list["TimeEntry"]] = relationship(back_populates="timesheet", cascade="all, delete-orphan")


class TimeEntry(Base):
    """One row of logged time on a specific day, against a project/task."""
    __tablename__ = "time_entries"
    __table_args__ = (
        Index("ix_time_entries_sheet_day", "timesheet_id", "entry_date"),
    )

    id:            Mapped[int] = mapped_column(Integer, primary_key=True)
    timesheet_id:  Mapped[int] = mapped_column(ForeignKey("timesheets.id", ondelete="CASCADE"), nullable=False)
    entry_date:    Mapped[date] = mapped_column(Date, nullable=False)
    engagement_id: Mapped[int] = mapped_column(ForeignKey("engagements.id"), nullable=False)
    task_id:       Mapped[int | None] = mapped_column(ForeignKey("engagement_tasks.id"), nullable=True)
    activity_id:   Mapped[int | None] = mapped_column(ForeignKey("activities.id"), nullable=True)
    hours:         Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    is_billable:   Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # TS-108
    note:          Mapped[str] = mapped_column(Text, nullable=False, default="")        # TS-107
    created_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    timesheet:  Mapped[Timesheet]      = relationship(back_populates="entries")
    engagement: Mapped[Engagement]     = relationship()
    task:       Mapped[EngagementTask | None] = relationship()
    activity:   Mapped[Activity | None]= relationship()


# ─────────────────────────────── Holiday (TS-142) ────────────────────

class Holiday(Base):
    """Public holidays so missing-time reports don't flag non-working days."""
    __tablename__ = "holidays"
    __table_args__ = (
        UniqueConstraint("tenant_id", "holiday_date", name="uq_holidays_tenant_date"),
    )

    id:           Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id:    Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name:         Mapped[str] = mapped_column(String(120), nullable=False)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)

    tenant:       Mapped[Tenant] = relationship(back_populates="holidays")


# ─────────────────────────────── Audit log (TS-141) ─────────────────

class AuditLog(Base):
    """Immutable trail of state-changing actions."""
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_tenant_time", "tenant_id", "created_at"),
    )

    id:          Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id:   Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    actor_id:    Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action:      Mapped[str] = mapped_column(String(80), nullable=False,
                                             doc="e.g. timesheet.submit, engagement.create")
    resource:    Mapped[str | None] = mapped_column(String(160), nullable=True,
                                                    doc="Free-form, e.g. timesheet:123")
    detail:      Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


# ═══════════════════════════════ Project Management ═══════════════════════════

class StageStatus(str, enum.Enum):
    not_started = "not_started"
    in_progress = "in_progress"
    completed   = "completed"
    skipped     = "skipped"


class MilestoneStatus(str, enum.Enum):
    planned     = "planned"
    in_progress = "in_progress"
    met         = "met"
    missed      = "missed"


class DeliverableStatus(str, enum.Enum):
    planned     = "planned"
    in_progress = "in_progress"
    delivered   = "delivered"
    accepted    = "accepted"


class RagStatus(str, enum.Enum):
    green = "green"
    amber = "amber"
    red   = "red"


class EngagementStage(Base):
    """Stages a project moves through (Proposal → Discovery → Build → Test → Close).
    Ordered list per engagement; each stage tracks its own status + dates."""
    __tablename__ = "engagement_stages"

    id:            Mapped[int] = mapped_column(Integer, primary_key=True)
    engagement_id: Mapped[int] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), nullable=False)
    name:          Mapped[str] = mapped_column(String(120), nullable=False)
    sort_order:    Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status:        Mapped[StageStatus] = mapped_column(Enum(StageStatus), nullable=False, default=StageStatus.not_started)
    start_date:    Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date:      Mapped[date | None] = mapped_column(Date, nullable=True)
    completed_at:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    engagement:    Mapped["Engagement"] = relationship()


class Milestone(Base):
    """Date-bound checkpoints — independent of stages."""
    __tablename__ = "milestones"

    id:            Mapped[int] = mapped_column(Integer, primary_key=True)
    engagement_id: Mapped[int] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), nullable=False)
    name:          Mapped[str] = mapped_column(String(160), nullable=False)
    due_date:      Mapped[date] = mapped_column(Date, nullable=False)
    status:        Mapped[MilestoneStatus] = mapped_column(Enum(MilestoneStatus), nullable=False, default=MilestoneStatus.planned)
    met_at:        Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes:         Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    engagement:    Mapped["Engagement"] = relationship()


class StatusUpdate(Base):
    """Weekly project status — RAG + summary + blockers + next week."""
    __tablename__ = "status_updates"
    __table_args__ = (Index("ix_status_eng_period", "engagement_id", "period_start"),)

    id:            Mapped[int] = mapped_column(Integer, primary_key=True)
    engagement_id: Mapped[int] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), nullable=False)
    author_id:     Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    period_start:  Mapped[date] = mapped_column(Date, nullable=False, doc="Monday of the week being reported")
    rag:           Mapped[RagStatus] = mapped_column(Enum(RagStatus), nullable=False, default=RagStatus.green)
    summary:       Mapped[str] = mapped_column(Text, nullable=False, default="")
    blockers:      Mapped[str] = mapped_column(Text, nullable=False, default="")
    next_week:     Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    engagement:    Mapped["Engagement"] = relationship()
    author:        Mapped["User"] = relationship()


class Deliverable(Base):
    """Things the engagement produces — a document, code drop, sign-off, etc."""
    __tablename__ = "deliverables"

    id:            Mapped[int] = mapped_column(Integer, primary_key=True)
    engagement_id: Mapped[int] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), nullable=False)
    name:          Mapped[str] = mapped_column(String(200), nullable=False)
    description:   Mapped[str] = mapped_column(Text, nullable=False, default="")
    due_date:      Mapped[date | None] = mapped_column(Date, nullable=True)
    status:        Mapped[DeliverableStatus] = mapped_column(Enum(DeliverableStatus), nullable=False, default=DeliverableStatus.planned)
    delivered_at:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes:         Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    engagement:    Mapped["Engagement"] = relationship()

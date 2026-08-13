import enum
import secrets
from datetime import datetime, timezone, date

from sqlalchemy import (
    String, Integer, DateTime, ForeignKey, Index, Text, Boolean, Enum, Date,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


# ---------------------------------------------------------------------------
# Phase 1 — Asset discovery
# ---------------------------------------------------------------------------

class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enrolment_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, default=_new_token)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    # Microsoft-only email address (acceptable domains: thirdoctopus.com,
    # outlook.com, hotmail.com, live.com, msn.com, or *.onmicrosoft.com)
    # used as the destination for OctoAssist notifications.
    notification_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    notification_settings: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict, server_default="'{}'::jsonb")
    # Phase 7+8: outbound mail for OctoAssist → admin notifications.
    # smtp_password and graph_client_secret are encrypted at rest via app/crypto.py.
    mail_provider: Mapped[str] = mapped_column(String(16), nullable=False, default="smtp")
    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_username: Mapped[str | None] = mapped_column(String(320), nullable=True)
    smtp_password: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted (Fernet)
    smtp_from: Mapped[str | None] = mapped_column(String(320), nullable=True)
    smtp_use_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Microsoft Graph (Phase 8): app-registration based, no password.
    graph_tenant_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    graph_client_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    graph_client_secret: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted (Fernet)
    graph_from: Mapped[str | None] = mapped_column(String(320), nullable=True)
    smtp_last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    smtp_last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    smtp_last_test_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    agents: Mapped[list["Agent"]] = relationship(back_populates="tenant")
    users: Mapped[list["User"]] = relationship(back_populates="tenant")
    categories: Mapped[list["Category"]] = relationship(back_populates="tenant")
    tickets: Mapped[list["Ticket"]] = relationship(back_populates="tenant")


class Agent(Base):
    """An endpoint (laptop/desktop) running OctoAssistAgent.exe."""
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    machine_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, default=_new_token)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    uninstall_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)




    # Phase B asset → user enrichment. Populated on every check-in by
    # services.asset_linker from the agent's reported logged_in_user. Location
    # is denormalised from the linked User so ticket routing can read it without
    # an extra join. ON DELETE SET NULL: deleting a user (e.g. someone leaves
    # the company) leaves their old laptop visible but unassigned.
    primary_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="agents")
    primary_user: Mapped["User | None"] = relationship(foreign_keys=[primary_user_id])
    snapshots: Mapped[list["AssetSnapshot"]] = relationship(back_populates="agent", cascade="all, delete-orphan")


class EntraDevice(Base):
    """A Windows endpoint discovered via Microsoft Graph /devices but not
    necessarily managed by an OctoAssist agent yet.

    Populated by services.entra_devices.sync_devices (Application permission
    Device.Read.All). Lets the dashboard show 'coverage' — which Windows
    laptops in the tenant don't have the agent installed yet. When a row
    matches an Agent by hostname (case-insensitive), the /assets view
    suppresses the Entra row in favour of the live agent data.
    """
    __tablename__ = "entra_devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    entra_device_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    operating_system: Mapped[str | None]    = mapped_column(String(60),  nullable=True)
    os_version: Mapped[str | None]          = mapped_column(String(60),  nullable=True)
    manufacturer: Mapped[str | None]        = mapped_column(String(120), nullable=True)
    model: Mapped[str | None]               = mapped_column(String(120), nullable=True)
    is_compliant: Mapped[bool | None]       = mapped_column(Boolean,     nullable=True)
    is_managed: Mapped[bool | None]         = mapped_column(Boolean,     nullable=True)
    account_enabled: Mapped[bool | None]    = mapped_column(Boolean,     nullable=True)
    # Device-level location/department, captured when a device is added
    # manually. Graph does not supply either, so sync_devices never writes
    # them — a manually entered value survives every subsequent Entra sync.
    # Displayed with the primary user's values as fallback, mirroring how
    # Agent.location falls back to User.location.
    location: Mapped[str | None]            = mapped_column(String(200), nullable=True)
    department: Mapped[str | None]          = mapped_column(String(200), nullable=True)
    approx_last_signin_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    primary_user_id: Mapped[int | None]     = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    primary_user: Mapped["User | None"] = relationship(foreign_keys=[primary_user_id])


class AssetSnapshot(Base):
    __tablename__ = "asset_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    agent: Mapped[Agent] = relationship(back_populates="snapshots")

    __table_args__ = (
        Index("ix_asset_snapshots_agent_time", "agent_id", "snapshot_at"),
    )


# ---------------------------------------------------------------------------
# Phase 4 — Patch Management
# ---------------------------------------------------------------------------

class PatchSeverity(str, enum.Enum):
    critical  = "critical"
    important = "important"
    moderate  = "moderate"
    low       = "low"
    unknown   = "unknown"


class PatchObservation(Base):
    """Append-only observation log for missing patches.

    Each (agent, package, available_version) combination gets ONE row that
    is upserted on every check-in:
      - first_seen_at — when the patch was first reported missing (audit anchor)
      - last_seen_at  — most recent check-in that still saw it as missing
      - resolved_at   — null until a check-in stops reporting it (= installed)

    Currently-missing patches → resolved_at IS NULL.
    Aging report → now() - first_seen_at, grouped into 0-7/8-30/31-90/>90 buckets.
    Audit "patch was available for N days before installation" → resolved_at - first_seen_at.
    """
    __tablename__ = "patch_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    package_name: Mapped[str] = mapped_column(String(255), nullable=False)
    current_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    available_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    severity: Mapped[PatchSeverity] = mapped_column(
        Enum(PatchSeverity, name="patch_severity"),
        nullable=False, default=PatchSeverity.unknown,
    )
    source: Mapped[str] = mapped_column(String(60), nullable=False, default="unknown")
    title: Mapped[str | None] = mapped_column(Text, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    last_seen_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent: Mapped[Agent] = relationship()

    __table_args__ = (
        Index("ix_patch_obs_agent_active",   "agent_id", "resolved_at"),
        Index("ix_patch_obs_severity_active", "severity", "resolved_at"),
        Index("ix_patch_obs_first_seen",     "first_seen_at"),
    )


class PatchWindowStatus(str, enum.Enum):
    draft        = "draft"
    scheduled    = "scheduled"
    in_progress  = "in_progress"
    completed    = "completed"
    cancelled    = "cancelled"


class PatchWindow(Base):
    """A scheduled patch-deployment window (maintenance window record).

    Phase 5 — scheduling + manual tracking.
    Phase 6 — auto_execute=True turns on agent-driven install. Server lists
    pending deployments per agent; agent pulls list, runs apt-get / Windows
    Update, posts results back. Linux is fully wired; Windows requires the
    PSWindowsUpdate PowerShell module on the endpoint.
    """
    __tablename__ = "patch_windows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[PatchWindowStatus] = mapped_column(
        Enum(PatchWindowStatus, name="patch_window_status"),
        nullable=False, default=PatchWindowStatus.draft,
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    severity_filter: Mapped[PatchSeverity | None] = mapped_column(
        Enum(PatchSeverity, name="patch_severity"),
        nullable=True,
    )
    hostname_pattern: Mapped[str] = mapped_column(String(120), nullable=False, default="%")
    group_id: Mapped[int | None] = mapped_column(ForeignKey("asset_groups.id", ondelete="SET NULL"), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Phase 6 — agent-driven auto-execution.
    auto_execute: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Subset of package names approved by the admin. Empty list = "all matching
    # the severity filter at planning time"; explicit list = only these.
    selected_packages: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime]   = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime]   = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by: Mapped["User | None"] = relationship()
    group: Mapped["AssetGroup | None"] = relationship()
    targets: Mapped[list["PatchWindowTarget"]] = relationship(
        back_populates="window", cascade="all, delete-orphan",
        order_by="PatchWindowTarget.id",
    )

    __table_args__ = (
        Index("ix_patch_windows_tenant_status", "tenant_id", "status"),
    )


class PatchWindowTargetStatus(str, enum.Enum):
    planned     = "planned"
    in_progress = "in_progress"
    succeeded   = "succeeded"
    failed      = "failed"
    skipped     = "skipped"


class PatchWindowTarget(Base):
    """One row per (window, agent). Tracks per-endpoint outcome of the window."""
    __tablename__ = "patch_window_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    window_id: Mapped[int] = mapped_column(ForeignKey("patch_windows.id", ondelete="CASCADE"), nullable=False)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[PatchWindowTargetStatus] = mapped_column(
        Enum(PatchWindowTargetStatus, name="patch_window_target_status"),
        nullable=False, default=PatchWindowTargetStatus.planned,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Snapshot of how many missing patches the agent had at planning time
    missing_at_plan: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    window: Mapped["PatchWindow"] = relationship(back_populates="targets")
    agent: Mapped["Agent"] = relationship()
    actor: Mapped["User | None"] = relationship()
    attempts: Mapped[list["PatchDeploymentAttempt"]] = relationship(
        back_populates="target", cascade="all, delete-orphan",
        order_by="PatchDeploymentAttempt.started_at.desc()",
    )

    __table_args__ = (
        Index("ix_patch_window_targets_window", "window_id"),
        Index("ix_patch_window_targets_agent_status", "agent_id", "status"),
    )


class PatchDeploymentAttempt(Base):
    """Append-only execution log — one row per agent-side install attempt."""
    __tablename__ = "patch_deployment_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("patch_window_targets.id", ondelete="CASCADE"), nullable=False)
    package_name: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    needs_reboot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stdout: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[str] = mapped_column(String(32), nullable=False, default="apt")  # apt | dnf | windows-update | manual

    target: Mapped[PatchWindowTarget] = relationship(back_populates="attempts")

    __table_args__ = (
        Index("ix_patch_attempts_target", "target_id"),
    )


# ---------------------------------------------------------------------------
# Phase 2 — Ticketing, users, SLA, audit
# ---------------------------------------------------------------------------

class UserRole(str, enum.Enum):
    admin = "admin"        # Full control: manage users, categories, tickets, assets
    agent = "agent"        # Helpdesk staff: see all tickets, assign, transition
    requester = "requester"  # End-user: see only own tickets via portal


class TicketKind(str, enum.Enum):
    incident = "incident"
    service_request = "service_request"


class TicketPriority(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class TicketStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    on_hold = "on_hold"
    resolved = "resolved"
    closed = "closed"
    cancelled = "cancelled"


class TicketEventKind(str, enum.Enum):
    created = "created"
    status_changed = "status_changed"
    priority_changed = "priority_changed"
    category_changed = "category_changed"
    assigned = "assigned"
    unassigned = "unassigned"
    comment_added = "comment_added"
    title_changed = "title_changed"
    description_changed = "description_changed"


class User(Base):
    """Human user — admin, helpdesk agent, or end-user requester."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"), nullable=False, default=UserRole.requester)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Entra ID enrichment — populated by directory sync (services.entra_directory)
    # and the SSO callback. entra_oid is the immutable Microsoft Graph user "id"
    # GUID; matches by it before falling back to email so renames survive.
    entra_oid:  Mapped[str | None] = mapped_column(String(64),  nullable=True, unique=True)
    location:   Mapped[str | None] = mapped_column(String(200), nullable=True)
    department: Mapped[str | None] = mapped_column(String(200), nullable=True)
    job_title:  Mapped[str | None] = mapped_column(String(200), nullable=True)
    synced_at:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Phase J: CAB (Change Advisory Board) membership. Members get CC'd on every
    # change submission and every new incident. Toggle from /settings/cab.
    is_cab_member: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    tenant: Mapped[Tenant] = relationship(back_populates="users")
    committees: Mapped[list["CabCommittee"]] = relationship(
        secondary="cab_committee_members",
        back_populates="members",
    )
    reported_tickets: Mapped[list["Ticket"]] = relationship(
        back_populates="reporter",
        foreign_keys="Ticket.reporter_id",
    )
    assigned_tickets: Mapped[list["Ticket"]] = relationship(
        back_populates="assignee",
        foreign_keys="Ticket.assignee_id",
    )

    @property
    def display_name(self) -> str:
        return self.full_name or self.email


class Category(Base):
    """Service catalogue / incident category — drives default priority and SLA."""
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[TicketKind] = mapped_column(Enum(TicketKind, name="ticket_kind"), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    default_priority: Mapped[TicketPriority] = mapped_column(
        Enum(TicketPriority, name="ticket_priority"),
        nullable=False,
        default=TicketPriority.medium,
    )
    sla_response_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=240)       # 4h
    sla_resolution_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=1440)    # 24h
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="categories")
    tickets: Mapped[list["Ticket"]] = relationship(back_populates="category")

    __table_args__ = (
        Index("ix_categories_tenant_kind", "tenant_id", "kind"),
    )


class ServiceCatalogItem(Base):
    __tablename__ = "service_catalog_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)  # list of field configs
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    tenant: Mapped[Tenant] = relationship()
    category: Mapped[Category] = relationship()


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    ticket_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    kind: Mapped[TicketKind] = mapped_column(Enum(TicketKind, name="ticket_kind"), nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False)
    catalog_item_id: Mapped[int | None] = mapped_column(ForeignKey("service_catalog_items.id", ondelete="SET NULL"), nullable=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    custom_fields: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sla_response_escalated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sla_resolution_escalated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    priority: Mapped[TicketPriority] = mapped_column(
        Enum(TicketPriority, name="ticket_priority"),
        nullable=False,
        default=TicketPriority.medium,
    )
    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus, name="ticket_status"),
        nullable=False,
        default=TicketStatus.open,
    )

    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Phase C: auto-filled at create-time from the reporter's primary asset
    # (or their User.location if no asset is linked). Used for location-based
    # routing — see services.location_routing. Denormalised; doesn't change
    # if the reporter relocates after the ticket is opened.
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # SLA — captured at create time so historical tickets aren't retroactively
    # changed if the category SLA gets updated later.
    due_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_resolution_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="tickets")
    category: Mapped[Category] = relationship(back_populates="tickets")
    reporter: Mapped[User] = relationship(back_populates="reported_tickets", foreign_keys=[reporter_id])
    assignee: Mapped[User | None] = relationship(back_populates="assigned_tickets", foreign_keys=[assignee_id])
    catalog_item: Mapped[ServiceCatalogItem | None] = relationship()
    asset: Mapped[Agent | None] = relationship()
    comments: Mapped[list["TicketComment"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="TicketComment.created_at",
    )
    events: Mapped[list["TicketEvent"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="TicketEvent.created_at",
    )

    __table_args__ = (
        Index("ix_tickets_tenant_status", "tenant_id", "status"),
        Index("ix_tickets_tenant_assignee", "tenant_id", "assignee_id"),
        Index("ix_tickets_tenant_reporter", "tenant_id", "reporter_id"),
        Index("ix_tickets_tenant_created", "tenant_id", "created_at"),
    )

    @property
    def is_open(self) -> bool:
        return self.status not in (TicketStatus.resolved, TicketStatus.closed, TicketStatus.cancelled)

    def response_breached(self, now: datetime | None = None) -> bool:
        if self.first_response_at is not None or self.due_response_at is None:
            return False
        return (now or _now()) > self.due_response_at

    def resolution_breached(self, now: datetime | None = None) -> bool:
        if self.resolved_at is not None or self.due_resolution_at is None:
            return False
        if not self.is_open:
            return False
        return (now or _now()) > self.due_resolution_at


class CategoryRule(Base):
    """Phase F per-category routing. When a ticket is created in a category
    that matches, auto-assign to default_assignee. Falls back AFTER location-
    based routing — so location wins if both rules match."""
    __tablename__ = "category_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    default_assignee_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    category: Mapped["Category"] = relationship(foreign_keys=[category_id])
    default_assignee: Mapped[User] = relationship(foreign_keys=[default_assignee_id])

    __table_args__ = (
        Index("ix_category_rules_tenant_cat", "tenant_id", "category_id", unique=True),
    )


class LocationRule(Base):
    """Per-office routing rule. When a ticket is created with a location that
    matches one of these rules (case-insensitive), the ticket is auto-assigned
    to the rule's default_assignee. Admin manages these via /settings/locations.
    """
    __tablename__ = "location_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    default_assignee_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    default_assignee: Mapped[User] = relationship(foreign_keys=[default_assignee_id])

    __table_args__ = (
        Index("ix_location_rules_tenant_loc", "tenant_id", "location", unique=True),
    )


class TicketComment(Base):
    __tablename__ = "ticket_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    ticket: Mapped[Ticket] = relationship(back_populates="comments")
    author: Mapped[User] = relationship()


class TicketEvent(Base):
    """Append-only audit log per ticket."""
    __tablename__ = "ticket_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[TicketEventKind] = mapped_column(Enum(TicketEventKind, name="ticket_event_kind"), nullable=False)
    before_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    ticket: Mapped[Ticket] = relationship(back_populates="events")
    actor: Mapped[User | None] = relationship()

    __table_args__ = (
        Index("ix_ticket_events_ticket_created", "ticket_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# Phase 3 — Identity providers (SSO)
# ---------------------------------------------------------------------------

class IdentityProviderKind(str, enum.Enum):
    entra = "entra"   # Microsoft Entra ID (Azure AD / M365)
    # future: ldap (on-prem AD), google, okta


class ProblemStatus(str, enum.Enum):
    investigating = "investigating"
    root_cause_identified = "root_cause_identified"
    workaround_in_place = "workaround_in_place"
    resolved = "resolved"
    closed = "closed"


class Problem(Base):
    __tablename__ = "problems"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    problem_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[ProblemStatus] = mapped_column(
        Enum(ProblemStatus, name="problem_status"),
        nullable=False, default=ProblemStatus.investigating,
    )
    priority: Mapped[TicketPriority] = mapped_column(
        Enum(TicketPriority, name="ticket_priority"),
        nullable=False, default=TicketPriority.medium,
    )
    root_cause: Mapped[str] = mapped_column(Text, nullable=False, default="")
    workaround: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant] = relationship()
    reporter: Mapped[User] = relationship(foreign_keys=[reporter_id])
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id])
    linked_tickets: Mapped[list["ProblemTicketLink"]] = relationship(
        back_populates="problem", cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_problems_tenant_status", "tenant_id", "status"),
    )


class ProblemTicketLink(Base):
    """Link table connecting problems to the incidents they explain."""
    __tablename__ = "problem_ticket_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"), nullable=False)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    linked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    problem: Mapped[Problem] = relationship(back_populates="linked_tickets")
    ticket: Mapped[Ticket] = relationship()

    __table_args__ = (
        Index("ix_pt_link_unique", "problem_id", "ticket_id", unique=True),
    )


class ChangeType(str, enum.Enum):
    normal = "normal"          # planned change requiring CAB approval
    standard = "standard"      # pre-approved low-risk change
    emergency = "emergency"    # urgent, post-implementation review


class ChangeRisk(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


class ChangeStatus(str, enum.Enum):
    draft = "draft"
    under_review = "under_review"
    approved = "approved"
    rejected = "rejected"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class Change(Base):
    __tablename__ = "changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    change_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    change_type: Mapped[ChangeType] = mapped_column(
        Enum(ChangeType, name="change_type"),
        nullable=False, default=ChangeType.normal,
    )
    risk: Mapped[ChangeRisk] = mapped_column(
        Enum(ChangeRisk, name="change_risk"),
        nullable=False, default=ChangeRisk.medium,
    )
    status: Mapped[ChangeStatus] = mapped_column(
        Enum(ChangeStatus, name="change_status"),
        nullable=False, default=ChangeStatus.draft,
    )
    rollback_plan: Mapped[str] = mapped_column(Text, nullable=False, default="")
    impact: Mapped[str] = mapped_column(Text, nullable=False, default="")
    planned_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    planned_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    implementer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    cab_approver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    cab_decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cab_decision_note: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    tenant: Mapped[Tenant] = relationship()
    requester: Mapped[User] = relationship(foreign_keys=[requester_id])
    implementer: Mapped[User | None] = relationship(foreign_keys=[implementer_id])
    cab_approver: Mapped[User | None] = relationship(foreign_keys=[cab_approver_id])
    events: Mapped[list["ChangeEvent"]] = relationship(
        back_populates="change", cascade="all, delete-orphan",
        order_by="ChangeEvent.created_at",
    )
    cab_votes: Mapped[list["CabVote"]] = relationship(back_populates="change", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_changes_tenant_status", "tenant_id", "status"),
    )

    @property
    def needs_cab_approval(self) -> bool:
        return self.change_type in (ChangeType.normal,) and self.status in (
            ChangeStatus.under_review,
        )


class CabVote(Base):
    __tablename__ = "cab_votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    change_id: Mapped[int] = mapped_column(ForeignKey("changes.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    approve: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # True = approve, False = reject, None = pending
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    voted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    change: Mapped["Change"] = relationship(back_populates="cab_votes")
    user: Mapped["User"] = relationship()


class ChangeEventKind(str, enum.Enum):
    created = "created"
    submitted_for_review = "submitted_for_review"
    approved = "approved"
    rejected = "rejected"
    started = "started"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    field_changed = "field_changed"


class ChangeEvent(Base):
    __tablename__ = "change_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    change_id: Mapped[int] = mapped_column(ForeignKey("changes.id", ondelete="CASCADE"), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[ChangeEventKind] = mapped_column(
        Enum(ChangeEventKind, name="change_event_kind"), nullable=False,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    before_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    change: Mapped[Change] = relationship(back_populates="events")
    actor: Mapped[User | None] = relationship()

    __table_args__ = (
        Index("ix_change_events_change_created", "change_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# Admin-uploaded files — installers, wallpapers, scripts
# ---------------------------------------------------------------------------
# When the admin pushes a .exe via Software → Deploy or sets a wallpaper, we
# accept a multipart upload from the browser and persist it on disk under
# /srv/octoassist/uploads/<uuid>.<ext>. The UploadedFile row holds metadata
# only — the actual bytes are on the filesystem (mounted Docker volume).
#
# Agents download via the unauthenticated /files/<uuid>.<ext> route: the UUID
# is 128 bits of entropy and only ever appears in a RemoteAction row that's
# tenant-scoped + agent-bearer-protected, so it's effectively unguessable.

class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)   # UUID hex
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False, default="installer")  # installer / wallpaper / script
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    created_by: Mapped["User | None"] = relationship()

    __table_args__ = (
        Index("ix_uploaded_files_tenant_created", "tenant_id", "created_at"),
    )


class TicketAttachment(Base):
    """Phase G: file attached to a ticket (or a comment) by reporter or staff.
    Wraps the existing UploadedFile storage so we don't reinvent the upload
    pipeline — just adds a many-to-one link to a Ticket."""
    __tablename__ = "ticket_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    file_id: Mapped[str] = mapped_column(ForeignKey("uploaded_files.id", ondelete="CASCADE"), nullable=False)
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    file: Mapped["UploadedFile"] = relationship()
    uploaded_by: Mapped["User | None"] = relationship()

    __table_args__ = (
        Index("ix_ticket_attachments_ticket", "ticket_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# Remote Action framework — admin-triggered commands the agent runs as SYSTEM
# ---------------------------------------------------------------------------
# Generic command channel that complements patch deployments. Each row =
# one command targeted at one agent. Admin creates from the dashboard,
# agent picks up on next 30-sec poll, runs the requested action, posts
# results back. Adding a new action means adding a new enum value + an
# agent-side handler — no new PowerShell on endpoints.

class RemoteActionKind(str, enum.Enum):
    run_executable    = "run_executable"     # Push + run .exe / .msi
    list_processes    = "list_processes"     # Get-Process snapshot
    kill_process      = "kill_process"       # Stop-Process by name or PID
    reboot            = "reboot"             # shutdown /r /t <s>
    lock_workstation  = "lock_workstation"   # rundll32 LockWorkStation
    send_toast        = "send_toast"         # Windows toast notification
    set_wallpaper     = "set_wallpaper"      # Per-user wallpaper push
    reset_password    = "reset_password"     # net user <name> <pw>
    custom_powershell = "custom_powershell"  # Break-glass; audit-logged
    force_refresh     = "force_refresh"      # Immediate full inventory + patch scan


class RemoteActionStatus(str, enum.Enum):
    pending     = "pending"      # waiting for agent to pick up
    in_progress = "in_progress"  # agent has started
    succeeded   = "succeeded"
    failed      = "failed"
    cancelled   = "cancelled"


class RemoteAction(Base):
    """One admin-issued command targeted at one agent."""
    __tablename__ = "remote_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    agent_id: Mapped[int]  = mapped_column(ForeignKey("agents.id",  ondelete="CASCADE"), nullable=False)
    kind: Mapped[RemoteActionKind] = mapped_column(
        Enum(RemoteActionKind, name="remote_action_kind"), nullable=False,
    )
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[RemoteActionStatus] = mapped_column(
        Enum(RemoteActionStatus, name="remote_action_status"),
        nullable=False, default=RemoteActionStatus.pending,
    )
    # Agent-reported result
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout:    Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr:    Mapped[str | None] = mapped_column(Text, nullable=True)
    result:    Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # structured payload
    # Lifecycle
    created_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    started_at:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    agent: Mapped["Agent"] = relationship()
    created_by: Mapped["User | None"] = relationship()

    __table_args__ = (
        Index("ix_remote_actions_agent_status", "agent_id", "status"),
        Index("ix_remote_actions_tenant_created", "tenant_id", "created_at"),
    )


class KbCategory(Base):
    __tablename__ = "kb_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    articles: Mapped[list["KbArticle"]] = relationship(back_populates="category")

    __table_args__ = (
        Index("ix_kb_categories_tenant", "tenant_id"),
    )


class KbArticleStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


class KbArticleVisibility(str, enum.Enum):
    internal = "internal"   # visible to admin / agent only
    portal = "portal"       # also visible to requesters in /portal/kb


class KbArticle(Base):
    __tablename__ = "kb_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("kb_categories.id", ondelete="SET NULL"), nullable=True,
    )
    slug: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[KbArticleStatus] = mapped_column(
        Enum(KbArticleStatus, name="kb_article_status"),
        nullable=False, default=KbArticleStatus.draft,
    )
    visibility: Mapped[KbArticleVisibility] = mapped_column(
        Enum(KbArticleVisibility, name="kb_article_visibility"),
        nullable=False, default=KbArticleVisibility.portal,
    )
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    helpful_yes_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    helpful_no_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    category: Mapped[KbCategory | None] = relationship(back_populates="articles")
    author: Mapped[User | None] = relationship()

    __table_args__ = (
        Index("ix_kb_articles_tenant_status", "tenant_id", "status"),
        Index("ix_kb_articles_tenant_visibility", "tenant_id", "visibility"),
    )


class IdentityProvider(Base):
    """An external identity provider configured for this tenant.

    For Entra ID, `config` carries:
      - entra_tenant_id  (AAD directory tenant — GUID or domain)
      - client_id        (app registration's Application (client) ID)
      - client_secret    (secret value — stored plain; rotate via the UI)
      - allowed_email_domains  (CSV; "" means "no domain check")
    """
    __tablename__ = "identity_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[IdentityProviderKind] = mapped_column(
        Enum(IdentityProviderKind, name="identity_provider_kind"),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="Microsoft Entra ID")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_provision: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"),
        nullable=False,
        default=UserRole.requester,
    )
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    tenant: Mapped[Tenant] = relationship()

    __table_args__ = (
        Index("ix_identity_providers_tenant_enabled", "tenant_id", "is_enabled"),
    )


# ---------------------------------------------------------------------------
# Phase J — canned reply templates for the staff ticket page
# ---------------------------------------------------------------------------
class ReplyTemplate(Base):
    """Reusable text that a technician can drop into the reply box on a
    ticket. Keeps language consistent across the team and saves typing on
    the common cases ('acknowledged, investigating', 'need more info', etc.).
    Managed under /settings/reply-templates."""
    __tablename__ = "reply_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    __table_args__ = (
        Index("ix_reply_templates_tenant_sort", "tenant_id", "sort_order"),
    )


# ---------------------------------------------------------------------------
# Software catalog — pre-curated installers admins push to endpoints.
# ---------------------------------------------------------------------------
class SoftwarePackage(Base):
    """A pre-approved installer the helpdesk can deploy from /software/deploy.

    The point: instead of pasting URL + silent args every time, the team
    curates a tenant catalog once ('Chrome 130 stable', 'Acrobat Reader DC
    24.x', 'Cisco AnyConnect 4.10') with vendor / version / install args /
    optional uninstall string — then deployment is just 'pick a package +
    pick endpoints + Queue deploy'.

    Managed under /settings/software-catalog.
    """
    __tablename__ = "software_packages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    vendor: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    version: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    installer_url: Mapped[str] = mapped_column(Text, nullable=False)
    install_args: Mapped[str] = mapped_column(Text, nullable=False, default="")
    uninstall_command: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    __table_args__ = (
        Index("ix_software_packages_tenant_active", "tenant_id", "is_active", "sort_order"),
    )

    @property
    def display_name(self) -> str:
        bits = [self.name]
        if self.version:
            bits.append(self.version)
        return " ".join(bits)


class Holiday(Base):
    """Business calendar holidays. SLA calculations skip these dates."""
    __tablename__ = "holidays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    __table_args__ = (
        Index("ix_holidays_tenant_date", "tenant_id", "holiday_date", unique=True),
    )


class CabCommittee(Base):
    """CAB Committees — e.g. Change Management, Risk Management"""
    __tablename__ = "cab_committees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    members: Mapped[list["User"]] = relationship(
        secondary="cab_committee_members",
        back_populates="committees",
    )


class CabCommitteeMember(Base):
    """Join table linking users to CAB committees"""
    __tablename__ = "cab_committee_members"

    committee_id: Mapped[int] = mapped_column(ForeignKey("cab_committees.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)


class AssetGroup(Base):
    """Asset Groups — admins curate these to target software uninstalls/updates and patch deployments in bulk"""
    __tablename__ = "asset_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class AssetGroupMember(Base):
    """Join table linking agents to asset groups"""
    __tablename__ = "asset_group_members"

    group_id: Mapped[int] = mapped_column(ForeignKey("asset_groups.id", ondelete="CASCADE"), primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class AuditLog(Base):
    """Structured record of administrative or helpdesk actions for security audit logs."""
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ip_address: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    user: Mapped["User | None"] = relationship()


class SoftwareSubscription(Base):
    """A purchased software licence / subscription the customer owns.

    Distinct from the SAM inventory in services/sam.py: that reports what is
    *installed* (discovered from endpoints), this records what has been *bought*
    — the licence key, the seat count, the PO, and when it lapses. The two
    answer different questions, and reconciling them is the point of a SAM
    audit.

    Windows OEM keys can be seeded straight from endpoint snapshots via
    services/subscriptions.import_windows_keys, so the common case does not
    need re-typing by hand.
    """
    __tablename__ = "software_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    software_name: Mapped[str] = mapped_column(String(200), nullable=False)
    vendor: Mapped[str | None] = mapped_column(String(160), nullable=True)
    license_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    seats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    po_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)

    purchased_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Drives the consolidated expiry digest. NULL means perpetual — a licence
    # that never lapses must never appear in the digest.
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # PO / licence certificate. Reuses the existing UploadedFile pipeline rather
    # than inventing a second storage path. SET NULL so deleting the file leaves
    # the subscription record intact.
    attachment_id: Mapped[str | None] = mapped_column(
        ForeignKey("uploaded_files.id", ondelete="SET NULL"), nullable=True
    )
    # Optional tie to the endpoint a per-device key belongs to (OEM Windows).
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    attachment: Mapped["UploadedFile | None"] = relationship()
    agent: Mapped["Agent | None"] = relationship()
    created_by: Mapped["User | None"] = relationship(foreign_keys=[created_by_id])

    __table_args__ = (
        Index("ix_software_subscriptions_tenant_expiry", "tenant_id", "expires_on"),
    )

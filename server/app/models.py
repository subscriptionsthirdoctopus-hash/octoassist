import enum
import secrets
from datetime import datetime, timezone

from sqlalchemy import (
    String, Integer, DateTime, ForeignKey, Index, Text, Boolean, Enum,
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

    tenant: Mapped[Tenant] = relationship(back_populates="agents")
    snapshots: Mapped[list["AssetSnapshot"]] = relationship(back_populates="agent", cascade="all, delete-orphan")


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

    tenant: Mapped[Tenant] = relationship(back_populates="users")
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


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    ticket_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    kind: Mapped[TicketKind] = mapped_column(Enum(TicketKind, name="ticket_kind"), nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False)

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

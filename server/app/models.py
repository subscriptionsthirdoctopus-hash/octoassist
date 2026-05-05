import secrets
from datetime import datetime, timezone

from sqlalchemy import String, Integer, DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enrolment_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, default=_new_token)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    agents: Mapped[list["Agent"]] = relationship(back_populates="tenant")


class Agent(Base):
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

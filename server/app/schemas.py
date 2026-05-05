from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AgentRegisterRequest(BaseModel):
    enrolment_key: str
    machine_id: str
    hostname: str


class AgentRegisterResponse(BaseModel):
    agent_id: int
    agent_token: str


class CheckinRequest(BaseModel):
    snapshot_at: datetime | None = None
    os: dict[str, Any] = Field(default_factory=dict)
    cpu: dict[str, Any] = Field(default_factory=dict)
    memory: dict[str, Any] = Field(default_factory=dict)
    disks: list[dict[str, Any]] = Field(default_factory=list)
    network: list[dict[str, Any]] = Field(default_factory=list)
    bios: dict[str, Any] = Field(default_factory=dict)
    system: dict[str, Any] = Field(default_factory=dict)
    logged_in_user: str | None = None
    software: list[dict[str, Any]] = Field(default_factory=list)


class CheckinResponse(BaseModel):
    accepted: bool
    snapshot_id: int


class AssetSummary(BaseModel):
    agent_id: int
    hostname: str
    os: str
    cpu: str
    ram_gb: float | None
    logged_in_user: str | None
    last_seen_at: datetime | None
    software_count: int

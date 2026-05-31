"""Runtime configuration, sourced from environment variables.

Each var is prefixed with OCTOFLOW_ so it cannot collide with HRMS or
OctoAssist on the same shared droplet.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OCTOFLOW_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://octoflow:octoflow@localhost:5432/octoflow"

    # Bootstrap admin — created on first boot if no users exist
    admin_email:    str = "admin@thirdoctopus.local"
    admin_password: str = "change-me-on-first-login"

    # Session cookie signing key (must be stable across restarts)
    session_secret: str = "dev-secret-change-me-in-production-64-bytes"

    # Tenant display
    tenant_name: str = "Third Octopus"

    # Public URL — used in email links once notifications are wired in
    base_url: str = "http://localhost:8089"

    # Server bind
    bind_host: str = "0.0.0.0"
    bind_port: int = 8080


settings = Settings()

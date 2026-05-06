import secrets

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="OCTOASSIST_")

    database_url: str = "postgresql+psycopg://octoassist:octoassist@localhost:5432/octoassist"
    admin_username: str = "admin"
    admin_password: str = "changeme"
    admin_email: str = "admin@thirdoctopus.local"
    bind_host: str = "0.0.0.0"
    bind_port: int = 8080
    tenant_name: str = "Third Octopus"

    # Session cookie signing key. Must be stable across restarts so existing
    # cookies remain valid. If not set in env, a fresh random key is generated
    # at process start (which invalidates existing sessions on restart).
    session_secret_key: str = secrets.token_urlsafe(48)
    session_max_age_seconds: int = 60 * 60 * 12  # 12 hours
    cookie_secure: bool = False  # set true once HTTPS is in front


settings = Settings()

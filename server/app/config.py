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
    tenant_name: str = "Third Octopus IT"
    base_url: str = "https://octoassist.thirdoctopus.com"

    # Support: people who get an email every time a ticket is CLOSED, on top
    # of the requester / assignee / tenant notification address. Comma-separated.
    # Override with OCTOASSIST_TICKET_CLOSED_NOTIFY_EMAILS in .env.
    ticket_closed_notify_emails: str = (
        "moin.shah@thirdoctopus.com,"
        "ranjit.yadav@thirdoctopus.com,"
        "arun@thirdoctopus.com"
    )

    def ticket_closed_recipients(self) -> list[str]:
        """Parsed, de-duplicated, lower-cased recipient list (order preserved)."""
        out: list[str] = []
        for raw in self.ticket_closed_notify_emails.split(","):
            e = raw.strip().lower()
            if e and "@" in e and e not in out:
                out.append(e)
        return out

    # Session cookie signing key. Must be stable across restarts so existing
    # cookies remain valid. If not set in env, a fresh random key is generated
    # at process start (which invalidates existing sessions on restart).
    session_secret_key: str = secrets.token_urlsafe(48)
    session_max_age_seconds: int = 60 * 60 * 12  # 12 hours
    cookie_secure: bool = False  # set true once HTTPS is in front


settings = Settings()

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="OCTOASSIST_")

    database_url: str = "postgresql+psycopg://octoassist:octoassist@localhost:5432/octoassist"
    admin_username: str = "admin"
    admin_password: str = "changeme"
    bind_host: str = "0.0.0.0"
    bind_port: int = 8080
    tenant_name: str = "TEMA India Pvt. Ltd."


settings = Settings()

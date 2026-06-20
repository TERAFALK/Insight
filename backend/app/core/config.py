from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Databas
    DATABASE_URL: str = "postgresql+asyncpg://insight:insight@db:5432/insight"

    # App-säkerhet
    SECRET_KEY: str = "dev-secret-byt-i-produktion"
    ENCRYPTION_KEY: str = "dev-enc-key-byt-i-produktion-32b"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    # Microsoft Graph
    GRAPH_TENANT_ID: str = ""
    GRAPH_CLIENT_ID: str = ""
    GRAPH_CLIENT_SECRET: str = ""
    GRAPH_SENDER: str = "noreply@terafalk.com"

    # Första admin
    FIRST_ADMIN_EMAIL: str = "admin@terafalk.com"
    FIRST_ADMIN_PASSWORD: str = "changeme"

    # Rapport-output
    REPORTS_OUTPUT_DIR: str = "/app/reports_output"

    # Schemaläggning (cron-format: dag i månaden, timme, minut)
    REPORT_SCHEDULE_DAY: int = 1
    REPORT_SCHEDULE_HOUR: int = 8
    REPORT_SCHEDULE_MINUTE: int = 0


settings = Settings()

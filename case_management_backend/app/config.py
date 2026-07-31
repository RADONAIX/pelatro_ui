"""Application settings, overridable via environment variables or a .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "RA Backend"
    version: str = "0.3.0"
    host: str = "0.0.0.0"
    port: int = 8001

    # Comma-separated in the environment, e.g. CORS_ORIGINS='["http://localhost:3000"]'
    # Defaults are permissive for local UI development.
    cors_origins: list[str] = ["*"]

    # --- Database ----------------------------------------------------------
    # PostgreSQL. Set the parts (DB_HOST/DB_USER/…) or override the whole URL
    # with DATABASE_URL — the latter wins when present, which is how a managed
    # deployment usually injects credentials.
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "postgres"
    db_password: str = "postgres"
    db_name: str = "rafms_db_new"
    database_url: str | None = None

    # Case management owns its own schema, matching the convention already in
    # this database (administration, air_schema, bi_reports).
    db_schema: str = "assurance"

    # Pool sizing — the API is IO-bound, so a small pool with overflow is
    # plenty and keeps idle connections off the server.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_recycle_seconds: int = 1800
    sql_echo: bool = False

    # Load the control-rule catalog and demo cases when the tables are empty.
    seed_demo_data: bool = True

    # --- Case attachments ---------------------------------------------------
    # Files are written under this directory (created on demand) and referenced
    # from assurance.case_attachments. Keep it off the repo tree in production
    # and point it at shared storage if the API runs on more than one host.
    uploads_dir: str = "uploads"
    max_upload_mb: int = 25
    # PDF only, per the case-evidence workflow. Extend deliberately: every entry
    # here is a file type the browser may be asked to render.
    allowed_upload_types: list[str] = ["application/pdf"]

    @property
    def sqlalchemy_url(self) -> str:
        """The effective connection URL."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


settings = Settings()

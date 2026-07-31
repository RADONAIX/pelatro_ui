"""Application settings, overridable via environment variables or a .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "RA Backend"
    version: str = "0.1.0"
    host: str = "0.0.0.0"
    port: int = 8001

    # Comma-separated in the environment, e.g. CORS_ORIGINS='["http://localhost:3000"]'
    # Defaults are permissive for local UI development.
    cors_origins: list[str] = ["*"]


settings = Settings()

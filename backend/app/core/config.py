"""Application configuration (master spec section 98).

All configuration comes from the environment. Secrets are held as SecretStr
and are never written to logs or to API responses.

There is deliberately no broker configuration: real-money execution is
disabled and broker credentials must never be requested or stored
(master spec section 120).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AppEnv = Literal["development", "test", "production"]
LogFormat = Literal["json", "console"]


class Settings(BaseSettings):
    """Environment-driven settings. See .env.example for the template."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- Application ----------
    app_name: str = "VIOP AI Market Intelligence"
    app_env: AppEnv = "development"
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    log_format: LogFormat = "json"

    # ---------- API ----------
    api_host: str = "0.0.0.0"  # noqa: S104 - containers bind all interfaces
    api_port: int = 8000
    api_prefix: str = "/api"
    # NoDecode stops pydantic-settings from JSON-decoding the environment
    # value before the validator below sees it; CORS_ORIGINS is a plain
    # comma-separated string, not JSON.
    cors_origins: Annotated[tuple[str, ...], NoDecode] = ("http://localhost:5173",)

    # ---------- PostgreSQL ----------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "viop"
    postgres_password: SecretStr = SecretStr("")
    postgres_db: str = "viop"
    database_url: str | None = None
    # Upper bound on a single connection attempt, in seconds. Keeps the
    # health probe responsive when the database is unroutable.
    db_connect_timeout_seconds: int = 5

    # ---------- Anthropic ----------
    # Unused in Phase 0. No AI adapter exists yet.
    anthropic_api_key: SecretStr | None = Field(default=None)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated CORS_ORIGINS environment value."""
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @field_validator("log_level")
    @classmethod
    def _normalise_log_level(cls, value: str) -> str:
        level = value.upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if level not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return level

    @property
    def sqlalchemy_url(self) -> str:
        """SQLAlchemy connection URL, with the password kept out of logs.

        DATABASE_URL, when set, wins over the individual POSTGRES_* values.
        """
        if self.database_url:
            return self.database_url
        password = self.postgres_password.get_secret_value()
        return (
            f"postgresql+psycopg://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def safe_database_target(self) -> str:
        """Host/database description that is safe to log or display."""
        return f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    def secret_values(self) -> tuple[str, ...]:
        """Every secret string that must be redacted from log output."""
        secrets: list[str] = []
        password = self.postgres_password.get_secret_value()
        if password:
            secrets.append(password)
        if self.anthropic_api_key is not None:
            key = self.anthropic_api_key.get_secret_value()
            if key:
                secrets.append(key)
        return tuple(secrets)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()

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
    # Wired in Phase 6: the Claude Vision adapter reads these. The key is a
    # SecretStr so it is scrubbed from logs by `secrets` below.
    anthropic_api_key: SecretStr | None = Field(default=None)

    vision_model: str = Field(default="")
    """Which model performs screenshot extraction.

    **Deliberately empty by default.** Master spec section 118 forbids
    inventing a fact, and a model identifier is a fact about a provider's
    catalogue that changes without notice. An unset value makes the adapter
    refuse to build with a typed CONFIGURATION failure, which is the honest
    outcome - silently defaulting to some model id would mean sending a user's
    screenshot to whatever that string happened to name.
    """

    vision_timeout_seconds: float = 60.0
    """Per-request timeout handed to the SDK client."""

    vision_max_retries: int = 2
    """Bounded retries, performed by the SDK client rather than by a second
    loop of our own - two layers would multiply into a much longer worst case
    than either intends."""

    vision_max_tokens: int = 2048
    """Response budget for one extraction. Not a business input: nothing in
    the analysis depends on token counts."""

    # Wired in Phase 7B: the Claude synthesis adapter reads these. The API key
    # is shared with vision - one Anthropic credential per deployment - while
    # the model is separate, because synthesis and screenshot extraction are
    # different jobs and a deployment may reasonably size them differently.
    synthesis_model: str = Field(default="")
    """Which model performs narrative synthesis.

    **Deliberately empty by default**, for the same reason as `vision_model`:
    §11 forbids a hidden fallback, and an unconfigured deployment returns
    NOT_CONFIGURED rather than silently choosing a model nobody verified.
    """

    synthesis_timeout_seconds: float = Field(default=90.0, gt=0)
    """Longer than vision: synthesis writes several paragraphs."""

    synthesis_max_retries: int = Field(default=2, ge=0)
    synthesis_max_output_tokens: int = Field(default=4096, gt=0)

    synthesis_context_window: int = Field(default=0, ge=0)
    """The model's hard context limit, in tokens.

    **Deliberately 0 (unset) by default.** A context window is a fact about a
    provider's catalogue that changes without notice; §118 forbids inventing
    one, so an unset value makes synthesis report NOT_CONFIGURED rather than
    budgeting against a number nobody verified.
    """

    synthesis_safety_reserve_tokens: int = Field(default=2_000, ge=0)
    """Whole-request cushion held back from the window, beyond the estimate's
    own margin. See `synthesis/tokens.py`."""

    synthesis_max_prompt_tokens: int = Field(default=0, ge=0)
    """Optional *stricter* application cap on the input, in tokens.

    0 means "no extra cap": the window minus output and reserve is the
    allowance. A cap may lower that and may never raise it.
    """

    synthesis_max_context_entries: int = Field(default=120, gt=0)

    @property
    def synthesis_is_configured(self) -> bool:
        """Whether a synthesis call could be attempted at all.

        Requires a key, a model **and** a context window. The window is part of
        the check because budgeting against an invented one is worse than not
        budgeting: it looks like a safeguard. A missing piece produces a typed
        NOT_CONFIGURED rather than a provider round trip.
        """
        key = (
            self.anthropic_api_key.get_secret_value() if self.anthropic_api_key is not None else ""
        )
        return (
            bool(key.strip())
            and bool(self.synthesis_model.strip())
            and self.synthesis_context_window > 0
        )

    @property
    def vision_is_configured(self) -> bool:
        """Whether a vision call could be attempted at all.

        Checked before building an adapter so a missing key or model produces
        a typed configuration error rather than a provider round trip.
        """
        key = (
            self.anthropic_api_key.get_secret_value() if self.anthropic_api_key is not None else ""
        )
        return bool(key.strip()) and bool(self.vision_model.strip())

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

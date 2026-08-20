"""Configuration and secret handling (master spec section 98)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings


@pytest.mark.unit
def test_database_url_is_assembled_from_parts(test_settings: Settings) -> None:
    assert test_settings.sqlalchemy_url == (
        "postgresql+psycopg://viop:fixture-password@localhost:5432/viop_test"
    )


@pytest.mark.unit
def test_explicit_database_url_overrides_parts() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://other:pw@db:5432/other",
        postgres_host="ignored",
    )
    assert settings.sqlalchemy_url == "postgresql+psycopg://other:pw@db:5432/other"


@pytest.mark.unit
def test_password_is_not_exposed_by_repr(test_settings: Settings) -> None:
    rendered = repr(test_settings)
    assert "fixture-password" not in rendered


@pytest.mark.unit
def test_safe_database_target_carries_no_credentials(test_settings: Settings) -> None:
    assert test_settings.safe_database_target == "localhost:5432/viop_test"
    assert "fixture-password" not in test_settings.safe_database_target


@pytest.mark.unit
def test_secret_values_lists_every_secret_for_redaction() -> None:
    settings = Settings(
        postgres_password=SecretStr("db-secret"),
        anthropic_api_key=SecretStr("ai-secret"),
    )
    assert set(settings.secret_values()) == {"db-secret", "ai-secret"}


@pytest.mark.unit
def test_missing_anthropic_key_is_allowed() -> None:
    """Phase 0 uses no AI provider, so the key is optional."""
    settings = Settings(anthropic_api_key=None)
    assert settings.anthropic_api_key is None
    assert settings.secret_values() == ()


@pytest.mark.unit
def test_cors_origins_accept_a_comma_separated_value() -> None:
    settings = Settings.model_validate({"cors_origins": "http://a.test, http://b.test"})
    assert settings.cors_origins == ("http://a.test", "http://b.test")


@pytest.mark.unit
def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"log_level": "CHATTY"})


@pytest.mark.unit
def test_invalid_app_env_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"app_env": "staging"})


@pytest.mark.unit
def test_no_broker_configuration_exists() -> None:
    """Master spec section 120: broker credentials are never configured."""
    forbidden = {"midas", "broker", "trading_password", "account_password"}
    fields = set(Settings.model_fields)
    assert not any(any(word in field for word in forbidden) for field in fields)


@pytest.mark.unit
def test_cors_origins_parse_from_an_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The environment path, not just model_validate.

    pydantic-settings JSON-decodes complex types from the environment before
    field validators run, which previously crashed startup inside the
    container while every in-process test still passed.
    """
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test,http://b.test")
    settings = Settings()
    assert settings.cors_origins == ("http://a.test", "http://b.test")


@pytest.mark.unit
def test_single_cors_origin_parses_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")
    assert Settings().cors_origins == ("http://localhost:5173",)

"""Calendars, provider bounds and provider configuration (Phase 15 Part 1)."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from pydantic import SecretStr, ValidationError

from app.adapters.sourcing.unavailable_calendar import UnavailableSessionCalendar
from app.application.ports.session_calendar import SessionCalendarProvider
from app.core.config import Settings
from app.core.logging import configure_logging
from app.domain.common.verification import VerificationStatus, VerifiedValue
from app.domain.sourcing.calendar import CalendarAnswer, SessionStatus
from app.domain.sourcing.limits import FailureClass, ProviderLimits, RetryPolicy
from tests.unit.sourcing.support import NOW, TEST_SOURCE

pytestmark = pytest.mark.unit


class TestNoCalendarIsInvented:
    async def test_the_composed_calendar_never_claims_to_know(self) -> None:
        calendar = UnavailableSessionCalendar()
        assert isinstance(calendar, SessionCalendarProvider)

        # A weekday afternoon, a Saturday, a New Year midnight: all unknown.
        for moment in (NOW, NOW + timedelta(days=5), NOW.replace(month=1, day=1, hour=0)):
            answer = await calendar.session_at("TEST_FIXTURE_FUT", moment)
            assert answer.status is SessionStatus.UNAVAILABLE
            assert answer.basis is None

    def test_an_in_session_answer_needs_a_verified_calendar(self) -> None:
        for basis in (
            None,
            VerifiedValue("hours", VerificationStatus.DEVELOPMENT_DEFAULT, TEST_SOURCE, NOW),
            VerifiedValue("hours", VerificationStatus.VERIFIED_CURRENT_FACT, TEST_SOURCE, None),
        ):
            with pytest.raises(ValueError, match="UNAVAILABLE"):
                CalendarAnswer("TEST_FIXTURE_FUT", NOW, SessionStatus.IN_SESSION, basis=basis)

    def test_an_unavailable_answer_cites_nothing(self) -> None:
        basis = VerifiedValue("hours", VerificationStatus.VERIFIED_CURRENT_FACT, TEST_SOURCE, NOW)
        with pytest.raises(ValueError, match="cites no calendar"):
            CalendarAnswer("TEST_FIXTURE_FUT", NOW, SessionStatus.UNAVAILABLE, basis=basis)


class TestRetryingIsBounded:
    def test_transient_failures_back_off_to_a_cap_then_stop(self) -> None:
        policy = RetryPolicy(max_attempts=5, base_delay_seconds=1, max_delay_seconds=6)

        delays = [policy.next_delay(attempt, FailureClass.TRANSIENT) for attempt in range(1, 6)]

        assert delays == [1, 2, 4, 6, None]

    @pytest.mark.parametrize(
        "failure",
        [FailureClass.AUTHENTICATION, FailureClass.NOT_LICENSED, FailureClass.INVALID_REQUEST],
    )
    def test_a_refusal_is_terminal_at_once(self, failure: FailureClass) -> None:
        assert RetryPolicy().next_delay(1, failure) is None

    def test_a_rate_limit_waits_the_longest_delay(self) -> None:
        assert RetryPolicy(max_delay_seconds=45).next_delay(1, FailureClass.RATE_LIMITED) == 45

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_connections": 0},
            {"max_queued_events": 0},
            {"metadata_cache_seconds": 0},
            {"shutdown_seconds": 0},
        ],
    )
    def test_every_bound_is_finite_and_positive(self, kwargs: dict[str, float]) -> None:
        with pytest.raises(ValueError):
            ProviderLimits(**kwargs)  # type: ignore[arg-type]


def settings_from_env(monkeypatch: pytest.MonkeyPatch, **values: str) -> Settings:
    for name in ("MARKET_DATA_PROVIDER", "MARKET_DATA_PROVIDER_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("POSTGRES_PASSWORD", "fixture-password")  # TEST_FIXTURE value
    return Settings(_env_file=None)  # type: ignore[call-arg]


class TestProviderConfiguration:
    def test_the_default_is_no_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = settings_from_env(monkeypatch)

        assert settings.market_data_provider == "none"
        assert settings.market_data_provider_token is None

    @pytest.mark.parametrize("name", ["dxfeed", "matriks", "NONE", "", "real"])
    def test_an_unknown_provider_refuses_to_start(
        self, monkeypatch: pytest.MonkeyPatch, name: str
    ) -> None:
        with pytest.raises(ValidationError):
            settings_from_env(monkeypatch, MARKET_DATA_PROVIDER=name)

    def test_a_token_without_a_provider_refuses_to_start_and_does_not_echo_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            settings_from_env(monkeypatch, MARKET_DATA_PROVIDER_TOKEN="sk-provider-do-not-leak")

        assert "sk-provider-do-not-leak" not in str(caught.value)

    def test_a_provider_token_is_redacted_from_logs(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        settings = Settings(  # type: ignore[call-arg]
            _env_file=None,
            postgres_password=SecretStr("fixture-password"),
        )
        object.__setattr__(
            settings, "market_data_provider_token", SecretStr("sk-provider-token-0001")
        )
        assert "sk-provider-token-0001" in settings.secret_values()
        assert "sk-provider-token-0001" not in repr(settings)

        configure_logging(level="INFO", log_format="json", secrets=settings.secret_values())
        logging.getLogger("provider.test").warning(
            "provider said sk-provider-token-0001 was rejected"
        )

        assert "sk-provider-token-0001" not in capsys.readouterr().err

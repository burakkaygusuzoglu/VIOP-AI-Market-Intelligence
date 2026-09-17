"""The composition root actually composes the providers (§13, §42).

Phase 8A's forensic audit found the Phase 6 screenshot route complete,
reachable, and permanently broken in production: `get_analyzer` was overridden
in exactly one place — a test — so every real call returned 503 while the
OpenAPI document advertised the endpoint.

The lesson is that a test which *installs* the dependency it is checking proves
nothing about production. So none of the tests here override anything. They
build the application the way `python -m app` does and inspect what came out.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.adapters.synthesis.claude_synthesizer import ClaudeMarketSynthesizer
from app.adapters.vision.claude_analyzer import ClaudeScreenshotAnalyzer
from app.api.providers import (
    build_screenshot_analyzer,
    build_synthesis_settings,
    build_synthesizer,
)
from app.api.routes.analysis import get_synthesis_settings, get_synthesizer
from app.api.routes.screenshots import get_analyzer
from app.core.config import Settings
from app.main import create_app


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "app_env": "test",
        "app_version": "0.0.0-test",
        "postgres_host": "localhost",
        "postgres_port": 5432,
        "postgres_user": "viop",
        "postgres_password": SecretStr("fixture-password"),  # TEST_FIXTURE value
        "postgres_db": "viop_test",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Unconfigured: nothing is constructed
# ----------------------------------------------------------------------


class TestUnconfigured:
    def test_no_vision_analyzer_is_built(self) -> None:
        assert build_screenshot_analyzer(_settings()) is None

    def test_no_synthesizer_is_built(self) -> None:
        assert build_synthesizer(_settings()) is None

    def test_a_key_alone_is_not_enough(self) -> None:
        """A credential without a model is not a configuration.

        §11 forbids guessing a model. The builder must not construct a client
        and then fail at request time with a vendor error.
        """
        settings = _settings(anthropic_api_key=SecretStr("sk-ant-fixture"))
        assert build_screenshot_analyzer(settings) is None
        assert build_synthesizer(settings) is None

    def test_a_model_alone_is_not_enough(self) -> None:
        settings = _settings(vision_model="claude-fixture", synthesis_model="claude-fixture")
        assert build_screenshot_analyzer(settings) is None
        assert build_synthesizer(settings) is None

    def test_synthesis_needs_a_context_window(self) -> None:
        """An unstated context window is treated exactly like an unstated model.

        Defaulting one would be an invented provider fact.
        """
        settings = _settings(
            anthropic_api_key=SecretStr("sk-ant-fixture"),
            synthesis_model="claude-fixture",
            synthesis_context_window=0,
        )
        assert build_synthesizer(settings) is None
        assert build_synthesis_settings(settings).tokens is None
        assert build_synthesis_settings(settings).is_configured is False

    def test_the_app_leaves_the_vision_seam_empty(self) -> None:
        """Unwired, the route's own 503 default stands.

        This is the Phase 8A state, asserted deliberately: it is correct
        behaviour for a deployment with no credential, and the next test proves
        it is not the *only* behaviour.
        """
        app = create_app(_settings())
        assert get_analyzer not in app.dependency_overrides


# ----------------------------------------------------------------------
# Configured: the real adapters are constructed, with no test override
# ----------------------------------------------------------------------


class TestConfigured:
    @pytest.fixture
    def configured(self) -> Settings:
        return _settings(
            anthropic_api_key=SecretStr("sk-ant-fixture-not-a-real-key"),
            vision_model="claude-vision-fixture",
            synthesis_model="claude-synthesis-fixture",
            synthesis_context_window=200_000,
        )

    def test_vision_analyzer_is_the_production_adapter(self, configured: Settings) -> None:
        analyzer = build_screenshot_analyzer(configured)
        assert isinstance(analyzer, ClaudeScreenshotAnalyzer)

    def test_synthesizer_is_the_production_adapter(self, configured: Settings) -> None:
        synthesizer = build_synthesizer(configured)
        assert isinstance(synthesizer, ClaudeMarketSynthesizer)

    def test_the_app_fills_the_vision_seam_without_any_override_from_a_test(
        self, configured: Settings
    ) -> None:
        """The assertion Phase 8A's defect would have failed.

        No `dependency_overrides` entry is installed by this test. The
        application itself must have put a real analyzer behind the route.
        """
        app = create_app(configured)

        assert get_analyzer in app.dependency_overrides
        resolved = app.dependency_overrides[get_analyzer]()
        assert isinstance(resolved, ClaudeScreenshotAnalyzer)

    def test_the_app_fills_the_synthesis_seam(self, configured: Settings) -> None:
        app = create_app(configured)

        assert get_synthesizer in app.dependency_overrides
        assert isinstance(app.dependency_overrides[get_synthesizer](), ClaudeMarketSynthesizer)

        settings = app.dependency_overrides[get_synthesis_settings]()
        assert settings.is_configured is True
        assert settings.tokens is not None
        assert settings.tokens.context_window == 200_000

    def test_token_budget_comes_from_configuration_not_a_default(
        self, configured: Settings
    ) -> None:
        budget = build_synthesis_settings(configured).tokens
        assert budget is not None
        assert budget.context_window == 200_000
        assert budget.max_output_tokens == configured.synthesis_max_output_tokens


# ----------------------------------------------------------------------
# The credential never escapes
# ----------------------------------------------------------------------


class TestCredentialContainment:
    def test_the_key_is_not_reachable_from_the_built_analyzer(self) -> None:
        """Nothing on the adapter exposes the credential.

        A key that can be read back off an object eventually reaches a log line
        or an error response. It is handed to the transport and kept nowhere
        else this side of the SDK.
        """
        secret = "sk-ant-fixture-not-a-real-key"
        analyzer = build_screenshot_analyzer(
            _settings(
                anthropic_api_key=SecretStr(secret),
                vision_model="claude-vision-fixture",
            )
        )
        assert analyzer is not None

        rendered = f"{analyzer!r} {vars(analyzer)}"
        assert secret not in rendered

    def test_settings_repr_does_not_leak_the_key(self) -> None:
        settings = _settings(anthropic_api_key=SecretStr("sk-ant-fixture-not-a-real-key"))
        assert "sk-ant-fixture-not-a-real-key" not in repr(settings)

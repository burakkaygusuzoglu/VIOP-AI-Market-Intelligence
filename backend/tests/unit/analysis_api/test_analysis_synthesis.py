"""Synthesis over a **real** analysis context (§20, §22, §43).

Phase 7 built the whole synthesis chain and left it unrouted because nothing
could supply it a trusted `SynthesisContext`. Phase 8's analysis endpoint is
that source, so these tests exercise the join:

    real OHLCV -> deterministic analysis -> SynthesisContext -> ActionEnvelope
      -> fake provider -> deterministic validator -> response

The provider is always a fake. No test here needs a key, a network or money.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.routes.analysis import get_synthesis_settings, get_synthesizer
from app.application.synthesis.draft import SynthesisDraft
from app.application.synthesis.errors import SynthesisFailure, SynthesisProviderError
from app.application.synthesis.schemas import SynthesisOutputSchema
from app.application.synthesis.tokens import TokenBudget
from app.application.synthesis.use_case import SynthesisSettings
from app.core.config import Settings
from app.domain.synthesis.actions import FinalAction
from app.main import create_app
from tests.unit.analysis_api.test_analysis_api import body

MODEL = "fixture-synthesis-model"
WINDOW = TokenBudget(context_window=200_000, max_output_tokens=4_096, safety_reserve=2_000)


def _payload(action: str) -> dict[str, Any]:
    """A structurally valid draft that cites nothing.

    Deliberately reference-free: these tests are about the *join* between the
    analysis and synthesis, and a citation would couple them to whichever
    reference ids the engines happened to mint for this fixture data.
    """
    return {
        "proposed_action": action,
        "summary": "Deterministik analiz özetlenmiştir.",
        "bull": {"case": "BULL", "narrative": "Yükseliş tarafı."},
        "bear": {"case": "BEAR", "narrative": "Düşüş tarafı."},
        "neutral": {"case": "NEUTRAL", "narrative": "Nötr taraf."},
        "devils_advocate": {
            "challenge": "Karşı görüş.",
            "evidence_is_limited": True,
        },
        "claims": [],
    }


@dataclass
class FakeSynthesizer:
    """A `MarketSynthesisProvider` that answers however the test needs."""

    action: FinalAction = FinalAction.WAIT
    error: SynthesisProviderError | None = None
    calls: list[Any] = field(default_factory=list)

    async def synthesize(self, request: Any) -> SynthesisDraft:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        raw = json.loads(json.dumps(_payload(self.action.value)))
        return SynthesisOutputSchema.model_validate(raw).to_draft()


def make_client(provider: Any, *, configured: bool = True) -> TestClient:
    settings = Settings(
        app_env="test",
        app_version="0.0.0-test",
        postgres_host="localhost",
        postgres_port=5432,
        postgres_user="viop",
        postgres_password=SecretStr("fixture-password"),  # TEST_FIXTURE value
        postgres_db="viop_test",
    )
    app = create_app(settings)
    app.dependency_overrides[get_synthesizer] = lambda: provider
    app.dependency_overrides[get_synthesis_settings] = lambda: SynthesisSettings(
        model=MODEL if configured else "",
        tokens=WINDOW if configured else None,
    )
    return TestClient(app)


# ----------------------------------------------------------------------
# The happy path exists at all
# ----------------------------------------------------------------------


class TestSynthesisReachesTheAnalysis:
    def test_a_configured_provider_is_actually_called(self) -> None:
        """The Phase 7 reachability gap, closed and asserted.

        Before Phase 8 there was no runtime path that reached `run_synthesis`
        with a trusted context. This proves one exists.
        """
        provider = FakeSynthesizer(action=FinalAction.NO_TRADE)
        with make_client(provider) as client:
            response = client.post("/api/analysis", json=body())

        assert response.status_code == 200
        assert provider.calls, "synthesis was never invoked"

    def test_an_accepted_synthesis_yields_a_final_action(self) -> None:
        """NO_TRADE, because that is the only action this fixture permits.

        The synthetic series is a straight line with no verified contract
        metadata, so the suitability engine raises blocking findings and the
        envelope removes everything else - including WAIT, since waiting does
        not resolve a structural blocker. Proposing WAIT here is correctly
        rejected, which is why this test proposes what the deterministic layer
        actually allows rather than what would be convenient.
        """
        provider = FakeSynthesizer(action=FinalAction.NO_TRADE)
        with make_client(provider) as client:
            synthesis = client.post("/api/analysis", json=body()).json()["synthesis"]

        assert synthesis["status"] == "SUCCESS"
        assert synthesis["final_action"] == "NO_TRADE"
        assert synthesis["allowed_actions"] == ["NO_TRADE"]

    def test_an_action_outside_the_envelope_is_refused(self) -> None:
        """Observed while writing these tests, and worth keeping.

        A model proposing WAIT against an envelope of {NO_TRADE} is rejected
        with the reason attached, not quietly downgraded.
        """
        provider = FakeSynthesizer(action=FinalAction.WAIT)
        with make_client(provider) as client:
            synthesis = client.post("/api/analysis", json=body()).json()["synthesis"]

        assert synthesis["status"] == "INVALID_OUTPUT"
        assert synthesis["final_action"] is None
        assert "WAIT" in synthesis["detail"]

    def test_the_narrative_is_carried_as_typed_segments(self) -> None:
        provider = FakeSynthesizer(action=FinalAction.NO_TRADE)
        with make_client(provider) as client:
            synthesis = client.post("/api/analysis", json=body()).json()["synthesis"]

        assert synthesis["summary"], "no summary segments"
        assert {segment["kind"] for segment in synthesis["summary"]} <= {
            "text",
            "fact",
            "observation",
        }
        assert synthesis["bull_case"] and synthesis["bear_case"]
        assert synthesis["devils_advocate"]

    def test_the_context_digest_identifies_the_inputs(self) -> None:
        provider = FakeSynthesizer(action=FinalAction.NO_TRADE)
        with make_client(provider) as client:
            first = client.post("/api/analysis", json=body()).json()
            second = client.post("/api/analysis", json=body()).json()

        assert first["synthesis"]["context_digest"]
        assert first["synthesis"]["context_digest"] == second["synthesis"]["context_digest"]


# ----------------------------------------------------------------------
# Failure never becomes a market opinion
# ----------------------------------------------------------------------


class TestFailureIsNotAnAction:
    def test_a_provider_failure_leaves_the_analysis_intact(self) -> None:
        provider = FakeSynthesizer(
            error=SynthesisProviderError(SynthesisFailure.TIMEOUT, "provider timed out")
        )
        with make_client(provider) as client:
            payload = client.post("/api/analysis", json=body()).json()

        assert payload["technical_available"] is True
        assert payload["evidence"], "the deterministic analysis was lost with the provider"
        assert payload["scenarios"]

    def test_a_provider_failure_fabricates_no_final_action(self) -> None:
        provider = FakeSynthesizer(
            error=SynthesisProviderError(SynthesisFailure.TIMEOUT, "provider timed out")
        )
        with make_client(provider) as client:
            synthesis = client.post("/api/analysis", json=body()).json()["synthesis"]

        assert synthesis["final_action"] is None
        assert synthesis["status"] not in {"WAIT", "NO_TRADE", "LONG", "SHORT"}

    def test_an_unconfigured_provider_reports_not_configured(self) -> None:
        with make_client(None, configured=False) as client:
            synthesis = client.post("/api/analysis", json=body()).json()["synthesis"]

        assert synthesis["status"] in {"NOT_CONFIGURED", "NOT_APPLICABLE"}
        assert synthesis["final_action"] is None

    def test_a_deterministic_veto_beats_a_model_that_says_long(self) -> None:
        """The Phase 7 guarantee, now over a real analysis.

        This fixture has no verified contract metadata, so sizing is
        unavailable and the envelope cannot permit a directional action. A model
        proposing LONG is rejected by the deterministic validator rather than
        believed.
        """
        provider = FakeSynthesizer(action=FinalAction.LONG)
        with make_client(provider) as client:
            synthesis = client.post("/api/analysis", json=body()).json()["synthesis"]

        assert synthesis["final_action"] != "LONG"
        assert synthesis["status"] != "SUCCESS" or synthesis["final_action"] in {
            "WAIT",
            "NO_TRADE",
        }

    def test_the_model_never_widens_the_allowed_actions(self) -> None:
        provider = FakeSynthesizer(action=FinalAction.LONG)
        with make_client(provider) as client:
            synthesis = client.post("/api/analysis", json=body()).json()["synthesis"]

        assert "LONG" not in synthesis["allowed_actions"]


# ----------------------------------------------------------------------
# No paid call, ever, in a normal test
# ----------------------------------------------------------------------


class TestNoRealProviderCall:
    def test_the_default_application_builds_no_client(self) -> None:
        """Without credentials nothing vendor-shaped is constructed."""
        with make_client(None, configured=False) as client:
            payload = client.post("/api/analysis", json=body()).json()

        assert payload["synthesis"]["final_action"] is None

    @pytest.mark.parametrize("action", [FinalAction.WAIT, FinalAction.NO_TRADE])
    def test_every_path_uses_the_fake(self, action: FinalAction) -> None:
        provider = FakeSynthesizer(action=action)
        with make_client(provider) as client:
            client.post("/api/analysis", json=body())

        assert len(provider.calls) == 1

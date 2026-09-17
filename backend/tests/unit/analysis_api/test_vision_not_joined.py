"""Vision observations take no part in the analysis (micro-closeout §3).

Phase 6 built a screenshot pipeline; Phase 8 built an analysis endpoint. They
were never connected, and that is the correct state for this phase: a trusted
join between an inferred reading and a calculated one is a design decision with
its own safety rules, and inventing it at the end of a UI phase would be the
worst possible place to make it.

What must be true is that the non-join is **structural**, not merely current.
`test_vision_trust_boundary.py` already proves a client cannot forge a
provenance. These tests prove the stronger and simpler thing: there is nowhere
to put one. No request field accepts a Vision result, no response field carries
one, the analysis identity cannot notice that a screenshot was ever uploaded,
and the synthesis context the LLM would read contains none of it.

The UI half of the same claim lives in the frontend suite - the screen states
the non-join rather than leaving a user to infer it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.schemas.analysis import AnalysisRequestBody, AnalysisResponse
from app.core.config import Settings
from app.main import create_app
from tests.unit.analysis_api.test_analysis_api import body, dataset

VISION_WORDS = (
    "vision",
    "screenshot",
    "observation",
    "ocr",
    "extracted",
    "confidence",
    "image",
)
"""Vocabulary that would have to appear somewhere for a join to exist."""


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(
        app_env="test",
        app_version="0.0.0-test",
        postgres_host="localhost",
        postgres_port=5432,
        postgres_user="viop",
        postgres_password=SecretStr("fixture-password"),  # TEST_FIXTURE value
        postgres_db="viop_test",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


class TestThereIsNowhereToPutOne:
    def test_the_request_model_has_no_vision_field(self) -> None:
        """Not "rejected" - absent. A forged field has no home to be rejected from."""
        fields = set(AnalysisRequestBody.model_fields)

        assert fields == {"symbol", "datasets", "account", "risk", "entry_price", "stop_price"}
        for field in fields:
            assert not any(word in field.lower() for word in VISION_WORDS), field

    def test_the_response_model_has_no_vision_field(self) -> None:
        for field in AnalysisResponse.model_fields:
            assert not any(word in field.lower() for word in VISION_WORDS), field

    def test_a_dataset_carries_only_candles(self) -> None:
        """A screenshot cannot travel disguised as a timeframe dataset."""
        dataset_model = AnalysisRequestBody.model_fields["datasets"]
        inner = dataset_model.annotation

        assert "TimeframeDatasetBody" in str(inner)
        from app.api.schemas.analysis import TimeframeDatasetBody

        assert set(TimeframeDatasetBody.model_fields) == {
            "timeframe",
            "content",
            "source_name",
        }


class TestAClientCannotSendOne:
    @pytest.mark.parametrize(
        "forged",
        [
            {"observations": [{"field": "last_price", "value": "1"}]},
            {"vision_observations": [{"field": "last_price", "value": "1"}]},
            {"screenshot_results": [{"symbol": "X"}]},
            {"vision": {"provenance": "SCREENSHOT_EXTRACTED"}},
            {"provenance": "SCREENSHOT_EXTRACTED"},
            {"confidence": 1.0},
            {"source": "AI_VISUAL_INFERENCE"},
            {"verification": "VERIFIED_CURRENT_FACT"},
        ],
    )
    def test_every_shape_of_vision_input_is_refused(
        self, client: TestClient, forged: dict[str, Any]
    ) -> None:
        response = client.post("/api/analysis", json={**body(), **forged})

        assert response.status_code == 422
        assert "extra_forbidden" in response.text

    def test_a_corrected_observation_cannot_become_analysis_authority(
        self, client: TestClient
    ) -> None:
        """A user-corrected reading is still a reading, not a measurement.

        The correction flow exists and is useful - it is how a mis-read value is
        put right before a human looks at it. What it cannot do is enter the
        analysis, because the analysis has no input for it.
        """
        corrected = {
            "corrections": [{"field": "last_price", "value": "999999", "verified_by_user": True}]
        }
        response = client.post("/api/analysis", json={**body(), **corrected})

        assert response.status_code == 422
        assert "999999" not in json.dumps(response.json())


class TestTheAnalysisCannotNotice:
    def test_the_deterministic_values_are_unchanged_by_any_ui_state(
        self, client: TestClient
    ) -> None:
        """There is no per-session state a screenshot could have altered.

        Two identical requests give identical analyses, so whatever the browser
        did between them - upload, correct, crop, re-analyse - reached nothing.
        """
        first = client.post("/api/analysis", json=body()).json()
        second = client.post("/api/analysis", json=body()).json()

        # `identity` carries the generation time, which differs by design; the
        # id inside it is compared in the next test.
        first.pop("identity")
        second.pop("identity")

        assert first == second, "the endpoint carried state between requests"

    def test_the_analysis_id_is_a_function_of_the_supplied_data_only(
        self, client: TestClient
    ) -> None:
        base = client.post("/api/analysis", json=body()).json()
        again = client.post("/api/analysis", json=body()).json()
        different = client.post(
            "/api/analysis", json=body(datasets=[dataset("1H", count=200)])
        ).json()

        assert base["identity"]["analysis_id"] == again["identity"]["analysis_id"]
        assert base["identity"]["analysis_id"] != different["identity"]["analysis_id"]

    def test_no_response_value_claims_a_visual_origin(self, client: TestClient) -> None:
        """Every provenance in the result is a calculated or supplied one."""
        payload = client.post("/api/analysis", json=body()).json()
        sources = {fact["source"] for fact in payload["facts"]}

        assert sources
        assert "SCREENSHOT_EXTRACTED" not in sources
        assert "AI_VISUAL_INFERENCE" not in sources

    def test_the_word_never_appears_in_a_result(self, client: TestClient) -> None:
        """Matched on word boundaries.

        A first version searched for substrings and flagged `vision` inside
        `reliability=provisional` - a false positive from the probe, not a
        finding.
        """
        import re

        text = json.dumps(client.post("/api/analysis", json=body()).json()).lower()

        for word in ("screenshot", "vision", "ocr", "visual"):
            assert not re.search(rf"{word}", text), word


async def _built_context() -> Any:
    """The synthesis context, assembled exactly as the analysis route assembles it."""
    from app.adapters.market_data.csv_provider import CsvCandleTextParser
    from app.adapters.system.clock import SystemClock
    from app.application.analysis.orchestrator import run_analysis
    from app.application.analysis.request import AnalysisRequest, TimeframeDataset
    from app.application.synthesis.context import build_synthesis_context
    from app.domain.analysis.evidence import EvidenceDirection
    from app.domain.analysis.scenarios import ScenarioCase
    from app.domain.common.enums import Timeframe
    from tests.unit.analysis_api.test_analysis_api import candles_csv

    datasets = tuple(
        TimeframeDataset(tf, candles_csv(label), f"{label}.csv")
        for label, tf in (
            ("1D", Timeframe.D1),
            ("1H", Timeframe.H1),
            ("15M", Timeframe.M15),
            ("5M", Timeframe.M5),
        )
    )
    outcome = await run_analysis(
        AnalysisRequest(symbol="TEST_FIXTURE_FUT", datasets=datasets),
        parser=CsvCandleTextParser(),
        clock=SystemClock(),
    )
    analysis = outcome.analysis
    assert analysis is not None

    direction = EvidenceDirection.BULLISH
    scenario = analysis.scenarios.case(ScenarioCase.BULL)
    return build_synthesis_context(
        analysis,
        direction,
        dict(outcome.suitability)[direction],
        contradictions=analysis.contradictions,
        setup_quality=scenario.quality,
        entry_quality=scenario.entry,
        scenario_set=analysis.scenarios,
        sizing=outcome.risk.sizing,
    )


class TestTheSynthesisContextCarriesNone:
    """What Phase 7 would send to the model is deterministic output only."""

    async def test_no_vision_observation_reaches_the_llm_context(self) -> None:
        import re

        text = json.dumps(await _built_context(), default=str).lower()

        for word in ("screenshot", "vision", "ocr", "visual"):
            assert not re.search(rf"{word}", text), word

    async def test_no_context_fact_carries_an_inferred_provenance(self) -> None:
        """Stronger than a word search: check the provenance of every number.

        `DataSourcePriority` is ordered worst-last, so the two ranks a Vision
        reading can hold - `SCREENSHOT_EXTRACTED` (4) and `AI_VISUAL_INFERENCE`
        (5) - are exactly the ones that must not appear in anything the model is
        shown.
        """
        from app.domain.common.enums import DataSourcePriority

        context = await _built_context()
        text = json.dumps(context, default=str)

        for rank in (
            DataSourcePriority.SCREENSHOT_EXTRACTED,
            DataSourcePriority.AI_VISUAL_INFERENCE,
        ):
            assert rank.name not in text, rank.name


class TestFailureIsolation:
    def test_a_screenshot_endpoint_failure_does_not_touch_analysis(
        self, client: TestClient
    ) -> None:
        """Vision is unconfigured here, so its endpoint refuses - and analysis
        is entirely unaffected, because the two share no path."""
        shot = client.post(
            "/api/screenshots",
            files={"file": ("chart.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png")},
        )
        assert shot.status_code >= 400

        analysis = client.post("/api/analysis", json=body())
        assert analysis.status_code == 200
        assert analysis.json()["technical_available"] is True

    def test_analysis_needs_no_screenshot_at_all(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=body()).json()

        assert payload["technical_available"] is True
        assert not any("ekran" in note.lower() for note in payload["missing"])

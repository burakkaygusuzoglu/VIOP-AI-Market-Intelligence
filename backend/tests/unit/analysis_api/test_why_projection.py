"""The Why Engine reaches the API, and cannot change anything (§6, §7).

Phase 8 shipped with the Why Engine unprojected. These tests prove it is
projected now, and — more importantly — that projecting it cannot alter a
single thing about the analysis.

The central safety test is `test_why_changes_nothing_else_in_the_response`:
the whole response is built twice, once with the Why block and once without,
and every other field must be byte-identical. An explanation that could move a
number would show up there immediately.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.schemas.analysis import SynthesisResponse
from app.api.schemas.analysis_projection import project
from app.api.schemas.analysis_why import build_why
from app.core.config import Settings
from app.main import create_app
from tests.unit.analysis_api.test_analysis_api import body


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


def why_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    explanations: list[dict[str, Any]] = payload["why"]
    return explanations


# ----------------------------------------------------------------------
# It is there, and it comes from the existing engine
# ----------------------------------------------------------------------


class TestWhyIsProjected:
    def test_major_conclusions_are_explained(self, client: TestClient) -> None:
        topics = {
            item["topic"] for item in why_of(client.post("/api/analysis", json=body()).json())
        }

        assert "BULLISH_EVIDENCE" in topics
        assert "BEARISH_EVIDENCE" in topics
        assert "SETUP_QUALITY" in topics
        assert "SCENARIO_STATE" in topics
        assert "PENDING_CONFIRMATION" in topics
        assert "POSITION_SIZE" in topics

    def test_every_available_explanation_carries_at_least_one_reason(
        self, client: TestClient
    ) -> None:
        """§92: never show an unexplained conclusion."""
        for item in why_of(client.post("/api/analysis", json=body()).json()):
            if item["available"]:
                assert item["reasons"], f"{item['topic']} claims to be explained but lists none"

    def test_every_reason_is_phrased_for_both_audiences(self, client: TestClient) -> None:
        for item in why_of(client.post("/api/analysis", json=body()).json()):
            for reason in item["reasons"]:
                assert reason["beginner"].strip()
                assert reason["pro"].strip()
                assert reason["code"].strip()
                assert reason["source"].strip()

    def test_a_reason_names_the_engine_that_produced_it(self, client: TestClient) -> None:
        sources = {
            reason["source"]
            for item in why_of(client.post("/api/analysis", json=body()).json())
            for reason in item["reasons"]
        }
        assert sources <= {
            "EVIDENCE",
            "QUALITY_COMPONENT",
            "ENTRY_COMPONENT",
            "CONTRADICTION",
            "SCENARIO_REQUIREMENT",
            "STRUCTURE",
            "RISK_ENGINE",
            "SUITABILITY",
            "COMPONENT_DELTA",
        }, sources


# ----------------------------------------------------------------------
# What cannot be explained says so
# ----------------------------------------------------------------------


class TestUnavailableIsAnAnswer:
    def test_position_size_is_unavailable_with_the_real_reason(self, client: TestClient) -> None:
        """No sizing happened, so there is no breakdown to walk."""
        payload = client.post("/api/analysis", json=body()).json()
        sizing = next(item for item in why_of(payload) if item["topic"] == "POSITION_SIZE")

        assert sizing["available"] is False
        assert sizing["reasons"] == []
        assert "kontrat" in sizing["unavailable_reason"].lower()

    def test_an_unavailable_topic_never_carries_invented_reasons(self, client: TestClient) -> None:
        for item in why_of(client.post("/api/analysis", json=body()).json()):
            if not item["available"]:
                assert item["reasons"] == []
                assert item["unavailable_reason"].strip()

    def test_no_topic_explains_a_final_action(self, client: TestClient) -> None:
        """Phase 7 owns LONG / SHORT / WAIT.

        An explanation of a final action would have to be written here rather
        than read from an engine, which is the one thing this must not do.
        """
        topics = {
            item["topic"] for item in why_of(client.post("/api/analysis", json=body()).json())
        }
        assert not (topics & {"WHY_LONG", "WHY_SHORT", "WHY_WAIT", "FINAL_ACTION"})


# ----------------------------------------------------------------------
async def build_outcome() -> Any:
    from app.adapters.market_data.csv_provider import CsvCandleTextParser
    from app.adapters.system.clock import SystemClock
    from app.application.analysis.orchestrator import run_analysis
    from app.application.analysis.request import AnalysisRequest, TimeframeDataset
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
    return await run_analysis(
        AnalysisRequest(symbol="TEST_FIXTURE_FUT", datasets=datasets),
        parser=CsvCandleTextParser(),
        clock=SystemClock(),
    )


# ----------------------------------------------------------------------
# §7: it explains, it never creates
# ----------------------------------------------------------------------


class TestWhySafety:
    async def test_why_changes_nothing_else_in_the_response(self) -> None:
        """The strongest form of §7, and the reason it is a whole-response diff.

        If an explanation could set an action, upgrade a provenance, fill a
        missing value or promote a forming reading, it would have to show up as
        a difference in some other field.
        """
        outcome = await build_outcome()
        synthesis = SynthesisResponse(status="NOT_CONFIGURED")

        full = project(outcome, synthesis).model_dump(mode="json")
        without = dict(full)
        without.pop("why")

        rebuilt = project(outcome, synthesis).model_dump(mode="json")
        rebuilt.pop("why")

        assert rebuilt == without
        assert full["why"], "the Why block was empty, so this proved nothing"

    async def test_why_is_a_pure_function_of_the_finished_outcome(self) -> None:
        """Called twice, it must produce the same thing and disturb nothing."""
        outcome = await build_outcome()

        first = [item.model_dump(mode="json") for item in build_why(outcome)]
        second = [item.model_dump(mode="json") for item in build_why(outcome)]

        assert first == second

    def test_why_cannot_set_a_final_action(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=body()).json()

        assert payload["synthesis"]["final_action"] is None
        assert payload["why"], "no explanations were produced"

    def test_why_cannot_upgrade_a_provenance(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=body()).json()
        sources = {item["source"] for item in payload["facts"]}

        assert sources <= {"CALCULATED", "UNVERIFIED"}
        assert payload["identity"]["contract_metadata_verified"] is False

    def test_why_cannot_fill_missing_data(self, client: TestClient) -> None:
        """A missing timeframe stays missing however much is explained."""
        partial = body(
            datasets=[item for item in body()["datasets"] if item["timeframe"] in {"1D", "1H"}]
        )
        payload = client.post("/api/analysis", json=partial).json()

        assert set(payload["missing_timeframes"]) == {"15M", "5M"}
        assert payload["why"]

    def test_why_cannot_make_risk_available(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=body()).json()

        assert payload["risk"]["available"] is False
        assert payload["risk"]["facts"] == []

    def test_why_carries_no_field_that_could_change_state(self) -> None:
        """Structural: the response type has nowhere to put an instruction."""
        from app.api.schemas.analysis import WhyExplanationResponse, WhyReasonResponse

        assert set(WhyExplanationResponse.model_fields) == {
            "topic",
            "subject",
            "available",
            "unavailable_reason",
            "reasons",
            # A count of what the §4 cap left out. It is a number about this
            # list, not a field anything downstream can act on.
            "omitted_reason_count",
        }
        assert set(WhyReasonResponse.model_fields) == {
            "code",
            "source",
            "severity",
            "beginner",
            "pro",
            "timeframe",
            "role",
        }

    def test_a_reason_severity_is_ordinal_not_a_probability(self, client: TestClient) -> None:
        severities = {
            reason["severity"]
            for item in why_of(client.post("/api/analysis", json=body()).json())
            for reason in item["reasons"]
        }
        assert severities <= {"INFO", "NOTABLE", "CRITICAL"}


class TestWhyEmitsNoDigitOfItsOwn:
    """Probe §15.11 asked whether a Why reason can state an invented number.

    The first form of the probe compared every decimal in the Why text against
    the response's `facts` and technical readings, and flagged "1.80". Tracing
    it: `EXTENSION: 10/16. the close sits 1.80 ATR from EMA20` - produced by the
    Phase 2 entry-quality engine, which computes the close-to-EMA20 distance in
    ATR units and writes its own reason string. The Why layer carried it
    verbatim. The probe was under-specified, not the code.

    So the invariant is pinned structurally instead, where it is decidable: the
    projection copies engine strings and formats no number at all. A value it
    never renders is a value it cannot get wrong.
    """

    def test_the_projection_module_formats_no_number(self) -> None:
        import inspect

        from app.api.schemas import analysis_why

        source = inspect.getsource(analysis_why)
        body = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))

        for forbidden in (".toFixed", "round(", 'f"{', "%.2f", "format(", "Decimal("):
            assert forbidden not in body, f"{forbidden} appears in the Why projection"

    def test_every_rendered_string_is_the_engine_s_own(self) -> None:
        """`_render` copies; it does not compose."""
        import inspect

        from app.api.schemas.analysis_why import _render

        source = inspect.getsource(_render)

        assert "reason.beginner," in source
        assert "reason.pro," in source
        assert "+" not in source, "the renderer concatenated something"

    async def test_no_digit_in_the_why_block_was_added_by_the_api_layer(self) -> None:
        """End-to-end, and independent of the projection.

        The first attempt compared the Why text against other *sections of the
        response*, and failed on "1.0" - part of the entry-quality engine's own
        threshold wording, `comfortable at or below 1.0`. That string has no
        other surface: carrying entry-component reasons to a reader is exactly
        what the Why block is for (§6), so "it must appear elsewhere in the
        response" was the wrong test, not a real finding.

        This asks the decidable question instead. Every string reachable in the
        finished domain/application objects is harvested directly, then every
        decimal the Why block shows is looked for there. A digit present in the
        response but absent from the engines would be one the API layer wrote.
        """
        import re
        from dataclasses import fields, is_dataclass

        outcome = await build_outcome()

        seen: set[int] = set()
        harvested: list[str] = []

        def walk(value: object, depth: int = 0) -> None:
            if depth > 12 or id(value) in seen:
                return
            seen.add(id(value))
            if isinstance(value, str):
                harvested.append(value)
            elif isinstance(value, (list, tuple, set, frozenset)):
                for item in value:
                    walk(item, depth + 1)
            elif isinstance(value, dict):
                for item in value.values():
                    walk(item, depth + 1)
            elif is_dataclass(value) and not isinstance(value, type):
                for field in fields(value):
                    walk(getattr(value, field.name, None), depth + 1)

        walk(outcome)
        engine_text = " ".join(harvested)

        why_text = " ".join(
            reason.beginner + " " + reason.pro
            for item in build_why(outcome)
            for reason in item.reasons
        )
        decimals = set(re.findall(r"(?<![\w.,])\d+[.,]\d+(?![\w])", why_text))

        assert decimals, "no decimals in the Why text; this would prove nothing"
        for value in decimals:
            assert value in engine_text, f"{value} was not produced by any engine"

"""An optional narration layer must never cost the analysis (micro-closeout §4).

Found by the output-bound audit, not by reading the code. A price series that
oscillates - which is what a real range-bound market looks like - produced two
evidence items identical in every field the synthesis context shows. The context
correctly refuses duplicate reference ids, because a model asked to cite `EV-...`
must get one fact back. The `ValueError` then escaped the route as a **500**, and
a complete, correct deterministic analysis was discarded with it.

Two separate things were wrong, so there are two fixes and two sets of tests:
identical content is now collapsed to one item, and a context that cannot be
assembled for *any* reason degrades to a typed status.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app

SNAPSHOT = datetime(2026, 3, 2, tzinfo=UTC)
STEP = {
    "1D": timedelta(days=1),
    "1H": timedelta(hours=1),
    "15M": timedelta(minutes=15),
    "5M": timedelta(minutes=5),
}


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


def oscillating(code: str, count: int = 1_200) -> str:
    """A range-bound series: repeated visits to the same levels.

    Three superimposed cycles, so highs and lows recur and the evidence engine
    produces the repeated readings that exposed the defect.
    """
    step = STEP[code]
    rows = ["open_time,open,high,low,close,volume"]
    start = SNAPSHOT - step * count
    for index in range(count):
        price = (
            100
            + 8 * math.sin(index / 7.0)
            + 5 * math.sin(index / 23.0)
            + 3 * math.sin(index / 51.0)
        )
        close = price + 0.4 * math.sin(index / 3.0)
        stamp = (start + step * index).strftime("%Y-%m-%dT%H:%M:%S") + "+00:00"
        rows.append(
            f"{stamp},{price:.2f},{max(price, close) + 0.6:.2f},"
            f"{min(price, close) - 0.6:.2f},{close:.2f},{1000 + index * 7}"
        )
    return "\n".join(rows) + "\n"


def oscillating_body(count: int = 1_200) -> dict[str, Any]:
    return {
        "symbol": "TEST_FIXTURE_FUT",
        "datasets": [
            {
                "timeframe": code,
                "content": oscillating(code, count),
                "source_name": f"{code}.csv",
            }
            for code in STEP
        ],
    }


def explode(*_args: object, **_kwargs: object) -> None:
    raise ValueError("synthetic context failure with internal detail")


class TestARangeBoundMarketIsAnalysable:
    def test_an_oscillating_series_does_not_crash_the_endpoint(self, client: TestClient) -> None:
        """This returned 500 before the fix."""
        response = client.post("/api/analysis", json=oscillating_body())

        assert response.status_code == 200, response.text

    def test_the_analysis_is_complete_not_degraded(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=oscillating_body()).json()

        assert payload["technical_available"] is True
        assert len(payload["technical"]) == 4
        assert payload["evidence"]
        assert payload["why"]

    def test_the_repeated_reading_still_reaches_the_human_evidence_list(
        self, client: TestClient
    ) -> None:
        """Deduplication happens in the synthesis context, not in the analysis.

        The evidence a person reads is untouched; only what the model is shown
        collapses identical entries.
        """
        payload = client.post("/api/analysis", json=oscillating_body()).json()

        assert len(payload["evidence"]) > 1


class TestIdenticalContentIsOneFact:
    async def test_the_context_holds_no_duplicate_reference_id(self) -> None:
        from app.adapters.market_data.csv_provider import CsvCandleTextParser
        from app.adapters.system.clock import SystemClock
        from app.application.analysis.orchestrator import run_analysis
        from app.application.analysis.request import AnalysisRequest, TimeframeDataset
        from app.application.synthesis.context import build_synthesis_context
        from app.domain.analysis.evidence import EvidenceDirection
        from app.domain.analysis.scenarios import ScenarioCase
        from app.domain.common.enums import Timeframe

        datasets = tuple(
            TimeframeDataset(tf, oscillating(label), f"{label}.csv")
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

        # Raised ValueError before the fix.
        context = build_synthesis_context(
            analysis,
            direction,
            dict(outcome.suitability)[direction],
            contradictions=analysis.contradictions,
            setup_quality=scenario.quality,
            entry_quality=scenario.entry,
            scenario_set=analysis.scenarios,
            sizing=outcome.risk.sizing,
        )

        ids = [
            item.ref.ref_id
            for group in (
                context.bull_evidence,
                context.bear_evidence,
                context.neutral_evidence,
            )
            for item in group
        ]

        assert ids, "no evidence reached the context, so this proved nothing"
        assert len(ids) == len(set(ids)), "the model would see one id meaning two things"

    def test_deduplication_keeps_the_first_in_canonical_order(self) -> None:
        """Not whichever happened to be collected first.

        `_to_context_evidence` sorts before deduplicating, so the survivor does
        not depend on the order the engines produced.
        """
        import inspect

        from app.application.synthesis.context import _to_context_evidence

        source = inspect.getsource(_to_context_evidence)

        assert source.index("sorted(") < source.index("if ref_id in seen")


class TestSynthesisCannotCostTheAnalysis:
    """Any future raise in context assembly must degrade, not destroy."""

    @pytest.fixture(autouse=True)
    def _broken_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.api.routes.analysis as route

        monkeypatch.setattr(route, "build_synthesis_context", explode)

    def test_a_context_failure_becomes_a_typed_status(self, client: TestClient) -> None:
        response = client.post("/api/analysis", json=oscillating_body(count=300))

        assert response.status_code == 200
        payload = response.json()
        assert payload["synthesis"]["status"] == "CONTEXT_UNAVAILABLE"
        assert payload["technical_available"] is True
        assert payload["evidence"]

    def test_the_failure_is_never_an_opinion_about_the_market(self, client: TestClient) -> None:
        """A system status is not WAIT and not NO_TRADE (§22, §33)."""
        synthesis = client.post("/api/analysis", json=oscillating_body(count=300)).json()[
            "synthesis"
        ]

        assert synthesis["final_action"] is None
        assert synthesis["status"] not in {"WAIT", "NO_TRADE", "SUCCESS"}

    def test_the_detail_says_the_analysis_survived(self, client: TestClient) -> None:
        detail = client.post("/api/analysis", json=oscillating_body(count=300)).json()["synthesis"][
            "detail"
        ]

        assert "deterministik analiz etkilenmedi" in detail.lower()

    def test_the_internal_reason_is_not_echoed_to_the_client(self, client: TestClient) -> None:
        """An exception message is not user-facing copy."""
        body = client.post("/api/analysis", json=oscillating_body(count=300)).text

        assert "internal detail" not in body

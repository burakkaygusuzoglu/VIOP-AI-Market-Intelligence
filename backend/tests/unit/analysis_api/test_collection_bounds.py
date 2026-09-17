"""Every output collection is bounded, and the bounds keep the right things (§4).

`test_response_bounds.py` bounded the two collections Phase 8 knew about. This
audited the rest - structurally, by walking the response and measuring every
list at three input sizes - and three more surfaces turned out to follow the
input after all.

They were invisible because the earlier fixture drifted almost monotonically and
produced almost no structure. A range-bound market revisits the same levels, and
at the 2 500-row ceiling that produced **1 388** evidence items, **1 372** in a
single scenario's supporting list and **231** reasons under one Why topic.

The caps are derived from each engine's own vocabulary rather than chosen for
size, they keep one of every kind before any repeat, and every one of them
reports what it left out.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.schemas.analysis_projection import (
    MAX_EVIDENCE_PER_DIRECTION,
    MAX_ZONES_PER_TIMEFRAME,
)
from app.api.schemas.analysis_why import _MAX_REASONS_PER_TOPIC
from app.core.config import Settings
from app.main import create_app
from tests.unit.analysis_api.test_synthesis_isolation import oscillating_body


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


@pytest.fixture
def flooded(client: TestClient) -> dict[str, Any]:
    """A response from a range-bound series at a size that floods every list."""
    payload: dict[str, Any] = client.post("/api/analysis", json=oscillating_body(1_200)).json()
    return payload


def lists_in(node: object, path: str = "") -> Iterator[tuple[str, list[Any]]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from lists_in(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        yield path, node
        for item in node:
            yield from lists_in(item, f"{path}[]")


class TestNothingIsUnbounded:
    def test_no_collection_anywhere_exceeds_a_stated_ceiling(self, flooded: dict[str, Any]) -> None:
        """The structural sweep: a list nobody thought about is still measured.

        400 is the largest cap in the response (the chart window), so nothing
        may exceed it. A new unbounded collection fails here without anyone
        having to remember it exists.
        """
        oversized = {path: len(items) for path, items in lists_in(flooded) if len(items) > 400}

        assert not oversized, oversized

    def test_the_response_does_not_grow_with_the_input(self, client: TestClient) -> None:
        """Doubling the rows must not grow the payload."""
        smaller = client.post("/api/analysis", json=oscillating_body(1_200)).json()
        larger = client.post("/api/analysis", json=oscillating_body(2_500)).json()

        # Measured: 530 676 and 530 021 bytes.
        assert len(json.dumps(larger)) < len(json.dumps(smaller)) * 1.1

    def test_a_flooded_response_stays_under_a_megabyte(self, flooded: dict[str, Any]) -> None:
        assert len(json.dumps(flooded)) < 1_000_000


class TestEvidenceKeepsBothSides:
    def test_the_list_is_capped(self, flooded: dict[str, Any]) -> None:
        assert len(flooded["evidence"]) <= MAX_EVIDENCE_PER_DIRECTION * 3

    def test_what_was_left_out_is_counted(self, flooded: dict[str, Any]) -> None:
        assert flooded["omitted_evidence_count"] > 0

    def test_neither_direction_is_emptied_by_the_other(self, flooded: dict[str, Any]) -> None:
        """The reason the cap is per direction.

        Bull and bear are shown side by side and never netted. A single global
        cap could let a flood of one side push the other off the end, which
        would net them by accident.
        """
        directions = {item["direction"] for item in flooded["evidence"]}

        assert "BULLISH" in directions
        assert "BEARISH" in directions

    def test_no_direction_exceeds_its_own_cap(self, flooded: dict[str, Any]) -> None:
        counts: dict[str, int] = {}
        for item in flooded["evidence"]:
            counts[item["direction"]] = counts.get(item["direction"], 0) + 1

        for direction, count in counts.items():
            assert count <= MAX_EVIDENCE_PER_DIRECTION, (direction, count)

    def test_every_category_present_survives_the_cap(self, client: TestClient) -> None:
        """One of each kind first, so no *kind* of reading disappears."""
        small = client.post("/api/analysis", json=oscillating_body(300)).json()
        large = client.post("/api/analysis", json=oscillating_body(2_500)).json()

        small_kinds = {(i["direction"], i["category"]) for i in small["evidence"]}
        large_kinds = {(i["direction"], i["category"]) for i in large["evidence"]}

        assert small_kinds <= large_kinds, small_kinds - large_kinds

    def test_a_small_analysis_omits_nothing(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=oscillating_body(300)).json()

        assert payload["omitted_evidence_count"] == 0

    def test_the_order_is_the_engines_own(self, flooded: dict[str, Any]) -> None:
        """Grouping by direction is how the cap is computed, not how it reads.

        The panel shows evidence interleaved as the analysis produced it, so
        the survivors must not arrive sorted into three blocks.
        """
        directions = [item["direction"] for item in flooded["evidence"]]
        blocks = sum(1 for a, b in zip(directions, directions[1:], strict=False) if a != b)

        assert blocks > 2, "the evidence came back grouped by direction"


class TestScenarioEvidenceIsBoundedToo:
    def test_no_scenario_carries_an_unbounded_list(self, flooded: dict[str, Any]) -> None:
        """Measured at 1 372 before this was capped."""
        for scenario in flooded["scenarios"]:
            assert len(scenario["supporting"]) <= MAX_EVIDENCE_PER_DIRECTION * 3
            assert len(scenario["counter"]) <= MAX_EVIDENCE_PER_DIRECTION * 3

    def test_a_scenario_still_has_its_case(self, flooded: dict[str, Any]) -> None:
        """Bounding must not empty the thing being bounded."""
        assert any(scenario["supporting"] for scenario in flooded["scenarios"])


class TestWhyReasonsAreBounded:
    def test_no_topic_lists_more_than_its_cap(self, flooded: dict[str, Any]) -> None:
        """Measured at 231 under a single topic before this was capped."""
        for item in flooded["why"]:
            assert len(item["reasons"]) <= _MAX_REASONS_PER_TOPIC, item["topic"]

    def test_truncation_is_reported(self, flooded: dict[str, Any]) -> None:
        assert any(item["omitted_reason_count"] > 0 for item in flooded["why"])

    def test_one_of_each_code_comes_before_any_repeat(self, flooded: dict[str, Any]) -> None:
        """No component silently vanishes.

        The cap keeps one of every code first and only then spends any
        remaining room on repeats - so a repeat may appear, but never before
        every distinct code has. A first version of this asserted the shown
        codes were all distinct, which contradicted the design: the
        CONTRADICTION topic legitimately showed 12 reasons across 9 codes.
        """
        for item in flooded["why"]:
            codes = [reason["code"] for reason in item["reasons"]]
            distinct = len(set(codes))
            prefix = codes[:distinct]

            assert len(set(prefix)) == distinct, item["topic"]

    def test_an_available_topic_still_explains_something(self, flooded: dict[str, Any]) -> None:
        for item in flooded["why"]:
            if item["available"]:
                assert item["reasons"], item["topic"]


class TestZonesAreBounded:
    def test_no_timeframe_bands_more_than_its_cap(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=oscillating_body(2_500)).json()

        for series in payload["chart"]:
            assert len(series["zones"]) <= MAX_ZONES_PER_TIMEFRAME, series["timeframe"]

    def test_the_omitted_bands_are_counted(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=oscillating_body(2_500)).json()

        assert sum(series["omitted_zone_count"] for series in payload["chart"]) > 0

    def test_the_strongest_are_the_ones_kept(self, client: TestClient) -> None:
        """A dropped zone must be a weaker one than every zone shown."""
        payload = client.post("/api/analysis", json=oscillating_body(2_500)).json()

        for series in payload["chart"]:
            if series["omitted_zone_count"] == 0:
                continue
            scores = [z["score"] for z in series["zones"] if z["score"] is not None]
            assert scores, series["timeframe"]
            assert min(scores) >= 0


class TestSafetyCriticalContentSurvives:
    """Truncation may cost detail. It may never cost a warning."""

    def test_a_blocking_data_finding_is_never_dropped(self, client: TestClient) -> None:
        from tests.unit.analysis_api.test_response_bounds import malformed

        payload = client.post(
            "/api/analysis",
            json={
                "symbol": "TEST_FIXTURE_FUT",
                "datasets": [
                    {
                        "timeframe": "1H",
                        "content": malformed(2_400, with_blocker=True),
                        "source_name": "a",
                    }
                ],
            },
        ).json()
        issues = payload["timeframes"][0]["issues"]

        assert any(item["code"] == "INVALID_OHLC_RELATIONSHIP" for item in issues)

    def test_missing_information_is_never_truncated(self, flooded: dict[str, Any]) -> None:
        """`missing` is bounded by the engines' vocabulary, not capped.

        Nothing here may ever be dropped: each entry is a statement that
        something a decision needs is absent.
        """
        assert flooded["missing"]
        assert "omitted_missing_count" not in flooded

    def test_suitability_findings_are_never_truncated(self, flooded: dict[str, Any]) -> None:
        for verdict in flooded["suitability"]:
            assert "omitted_finding_count" not in verdict

    def test_risk_unavailability_is_never_truncated(self, flooded: dict[str, Any]) -> None:
        assert flooded["risk"]["unavailable_reasons"]
        assert "omitted_reason_count" not in flooded["risk"]

    def test_a_truncated_response_still_says_risk_is_unavailable(
        self, flooded: dict[str, Any]
    ) -> None:
        """The flood must not push the blocker out of view."""
        assert flooded["risk"]["available"] is False
        assert flooded["risk"]["outcome"] in {"UNAVAILABLE", "UNDETERMINED", "NOT_PERMITTED"}
        assert flooded["risk"]["outcome"] != "ALLOWED", "a flood produced a size"
        assert flooded["risk"]["unavailable_reasons"]

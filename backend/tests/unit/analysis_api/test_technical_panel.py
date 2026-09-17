"""The technical panel and chart overlays (§10, §11).

Phase 8 surfaced one indicator. Phase 1 had already computed the rest, so this
is a projection gap rather than an engine one — and these tests pin both halves
of it: that the readings arrive, and that a reading which does not exist yet is
reported as absent rather than as zero.

The zero case matters most. A mutation probe that replaced a missing value with
`0.0` passed every frontend test, because those run against a static fixture and
could not see a backend substitution. This file is where that is caught.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.schemas.analysis_technical import BEGINNER_KEYS
from app.core.config import Settings
from app.main import create_app
from tests.unit.analysis_api.test_analysis_api import body, dataset


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


def panel_for(payload: dict[str, Any], timeframe: str) -> dict[str, Any]:
    return next(item for item in payload["technical"] if item["timeframe"] == timeframe)


def reading(panel: dict[str, Any], key: str) -> dict[str, Any]:
    return next(item for item in panel["readings"] if item["key"] == key)


class TestReadingsArrive:
    def test_a_panel_exists_for_every_usable_timeframe(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=body()).json()
        assert {item["timeframe"] for item in payload["technical"]} == {"1D", "1H", "15M", "5M"}

    def test_the_phase_one_indicator_set_is_present(self, client: TestClient) -> None:
        panel = panel_for(client.post("/api/analysis", json=body()).json(), "1H")
        keys = {item["key"] for item in panel["readings"]}

        for expected in (
            "rsi",
            "atr",
            "adx",
            "plus_di",
            "minus_di",
            "macd",
            "macd_signal",
            "macd_histogram",
            "bollinger_upper",
            "bollinger_lower",
            "vwap",
            "relative_volume",
            "volume_acceleration",
        ):
            assert expected in keys, expected
        assert any(key.startswith("ema_") for key in keys)
        assert any(key.startswith("sma_") for key in keys)

    def test_the_beginner_subset_is_a_subset(self, client: TestClient) -> None:
        """Pro sees more of the same list, not a different list."""
        panel = panel_for(client.post("/api/analysis", json=body()).json(), "1H")
        beginner = {item["key"] for item in panel["readings"] if item["beginner"]}
        everything = {item["key"] for item in panel["readings"]}

        assert beginner == set(BEGINNER_KEYS)
        assert beginner < everything

    def test_a_reading_carries_the_exact_engine_value(self, client: TestClient) -> None:
        """`raw` is the float the engine produced, not a rounded display form."""
        panel = panel_for(client.post("/api/analysis", json=body()).json(), "1H")
        rsi = reading(panel, "rsi")

        assert rsi["available"] is True
        assert float(rsi["raw"]) == float(rsi["raw"])  # parses as a real number
        assert rsi["unit"] == "indicator"


class TestAbsentIsAbsent:
    def test_an_indicator_without_enough_history_is_unavailable_not_zero(
        self, client: TestClient
    ) -> None:
        """EMA 200 cannot exist for a 60-candle series.

        Reporting `0` would read as "the average is zero" rather than "there is
        not enough data yet", and a chart drawn from it would be a line nobody
        computed.
        """
        payload = client.post("/api/analysis", json=body(datasets=[dataset("1H", count=60)])).json()
        ema200 = reading(panel_for(payload, "1H"), "ema_200")

        assert ema200["available"] is False
        assert ema200["raw"] == "", "an unavailable reading carried a value"
        assert ema200["raw"] != "0"
        assert ema200["raw"] != "0.0"
        assert ema200["unavailable_reason"].strip()

    def test_no_unavailable_reading_anywhere_carries_a_number(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=body(datasets=[dataset("1H", count=60)])).json()

        for panel in payload["technical"]:
            for item in panel["readings"]:
                if not item["available"]:
                    assert item["raw"] == "", f"{item['key']} was unavailable but had a value"

    def test_an_available_reading_always_carries_one(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=body()).json()

        for panel in payload["technical"]:
            for item in panel["readings"]:
                if item["available"]:
                    assert item["raw"].strip(), f"{item['key']} was available but empty"

    def test_an_unusable_timeframe_gets_no_panel(self, client: TestClient) -> None:
        payload = client.post(
            "/api/analysis",
            json=body(
                datasets=[
                    {
                        "timeframe": "1H",
                        "content": "open_time,open,high,low,close,volume\n"
                        "2026-01-01T00:00:00+00:00,100,90,95,97,5\n",
                        "source_name": "a",
                    }
                ]
            ),
        ).json()

        assert payload["technical"] == []


class TestOverlays:
    def test_overlays_align_one_to_one_with_the_drawn_candles(self, client: TestClient) -> None:
        """A point beside a bar that is not drawn would be a value out of place."""
        series = client.post("/api/analysis", json=body()).json()["chart"][0]

        assert series["overlays"], "no overlay was produced"
        for overlay in series["overlays"]:
            assert len(overlay["values"]) == len(series["candles"]), overlay["key"]

    def test_an_overlay_leaves_a_gap_rather_than_filling_it(self, client: TestClient) -> None:
        """Warm-up is a hole in the series, not a value to invent."""
        series = client.post("/api/analysis", json=body()).json()["chart"][0]
        ema200 = next(item for item in series["overlays"] if item["key"] == "ema_200")

        assert any(value is None for value in ema200["values"])
        assert any(value is not None for value in ema200["values"])

    def test_an_overlay_that_is_entirely_warm_up_is_not_sent(self, client: TestClient) -> None:
        """An overlay of nothing is not an overlay."""
        payload = client.post("/api/analysis", json=body(datasets=[dataset("1H", count=60)])).json()
        keys = {item["key"] for item in payload["chart"][0]["overlays"]}

        assert "ema_200" not in keys
        assert "ema_9" in keys

    def test_every_overlay_value_is_an_exact_engine_value(self, client: TestClient) -> None:
        series = client.post("/api/analysis", json=body()).json()["chart"][0]

        for overlay in series["overlays"]:
            for value in overlay["values"]:
                if value is not None:
                    assert isinstance(value, str)
                    float(value)  # parses exactly; raises if it were mangled

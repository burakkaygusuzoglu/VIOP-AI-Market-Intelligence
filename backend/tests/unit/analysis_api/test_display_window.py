"""The analytical dataset and the display dataset are different sizes (§4).

Every candle supplied is analysed. Only the most recent `MAX_CHART_CANDLES` are
sent for drawing, because the chart costs one DOM node per bar. That separation
is only honest if the payload says so, and if an omitted bar is *absent* rather
than summarised into a synthetic candle nobody measured.

These tests also pin the **measured** row limit. It was 60 000 on the reasoning
that it was "about seven months" of 5M candles; profiling showed the real path
is roughly quadratic (Phase 2 rebuilds zones once per historical step), so
60 000 rows is on the order of twenty-five minutes of CPU for a ~3 MB upload.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.schemas.analysis_projection import CHART_WINDOW_POLICY, MAX_CHART_CANDLES
from app.application.analysis.limits import InputLimits
from app.core.config import Settings
from app.main import create_app

STEP = timedelta(minutes=5)
END = datetime(2026, 3, 2, tzinfo=UTC)


def csv_rows(count: int, *, first_close: str | None = None) -> str:
    last_open = END - STEP
    rows = ["open_time,open,high,low,close,volume"]
    price = Decimal("100")
    for index in range(count):
        moment = last_open - STEP * (count - 1 - index)
        open_price = price
        close_price = price + (Decimal("0.01") if index % 3 else Decimal("-0.01"))
        if index == 0 and first_close is not None:
            close_price = Decimal(first_close)
        rows.append(
            f"{moment.isoformat()},{open_price},{max(open_price, close_price) + Decimal('1')},"
            f"{min(open_price, close_price) - Decimal('1')},{close_price},{1000 + index}"
        )
        price = close_price
    return "\n".join(rows) + "\n"


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


def body(content: str) -> dict[str, Any]:
    return {
        "symbol": "TEST_FIXTURE_FUT",
        "datasets": [{"timeframe": "5M", "content": content, "source_name": "5M.csv"}],
    }


class TestTheWindow:
    def test_a_large_dataset_is_analysed_in_full_but_drawn_in_part(
        self, client: TestClient
    ) -> None:
        rows = MAX_CHART_CANDLES * 3
        payload = client.post("/api/analysis", json=body(csv_rows(rows))).json()

        assert payload["timeframes"][0]["candle_count"] == rows, "the analysis lost candles"
        series = payload["chart"][0]
        assert len(series["candles"]) == MAX_CHART_CANDLES, "the chart drew the whole dataset"

    def test_the_payload_states_what_it_omitted(self, client: TestClient) -> None:
        """A chart captioned "400 mum" while 1 200 were analysed describes
        itself, not the data."""
        rows = MAX_CHART_CANDLES * 3
        series = client.post("/api/analysis", json=body(csv_rows(rows))).json()["chart"][0]

        assert series["analysed_count"] == rows
        assert series["omitted_count"] == rows - MAX_CHART_CANDLES
        assert series["window_policy"] == CHART_WINDOW_POLICY

    def test_a_small_dataset_omits_nothing(self, client: TestClient) -> None:
        series = client.post("/api/analysis", json=body(csv_rows(50))).json()["chart"][0]

        assert series["analysed_count"] == 50
        assert series["omitted_count"] == 0
        assert len(series["candles"]) == 50

    def test_drawn_candles_are_the_most_recent_and_unmodified(self, client: TestClient) -> None:
        """Latest-N of the authoritative bars. Not a downsample, not an average.

        Every drawn close must appear in the supplied CSV verbatim; a
        summarised OHLC would be a price nobody measured.
        """
        content = csv_rows(MAX_CHART_CANDLES * 2)
        series = client.post("/api/analysis", json=body(content)).json()["chart"][0]

        supplied = [line.split(",") for line in content.strip().splitlines()[1:]]
        supplied_closes = {row[4] for row in supplied}
        drawn_closes = {item["close"] for item in series["candles"]}
        assert drawn_closes <= supplied_closes

        # And they are the tail, not an arbitrary slice.
        assert series["candles"][-1]["open_time"] == supplied[-1][0]
        assert series["candles"][0]["open_time"] == supplied[-MAX_CHART_CANDLES][0]

    def test_an_omitted_candle_still_moves_the_analysis(self, client: TestClient) -> None:
        """The separation is display-only.

        Changing a bar too old to be drawn must still change the analysis, or
        the window would be quietly deciding what counts.
        """
        rows = MAX_CHART_CANDLES * 3
        plain = client.post("/api/analysis", json=body(csv_rows(rows))).json()
        altered = client.post(
            "/api/analysis", json=body(csv_rows(rows, first_close="250.0"))
        ).json()

        assert altered["identity"]["analysis_id"] != plain["identity"]["analysis_id"]
        # The changed bar is outside the drawn window.
        assert (
            altered["chart"][0]["candles"][0]["open_time"]
            == plain["chart"][0]["candles"][0]["open_time"]
        )

    def test_the_response_stays_a_sane_size(self, client: TestClient) -> None:
        response = client.post("/api/analysis", json=body(csv_rows(MAX_CHART_CANDLES * 5)))
        assert len(response.content) < 1_500_000, f"response was {len(response.content)} bytes"


class TestTheMeasuredRowLimit:
    def test_the_row_limit_is_the_measured_one_not_the_guessed_one(self) -> None:
        limits = InputLimits()
        assert limits.max_rows_per_timeframe == 2_500
        assert limits.max_rows_per_timeframe < 60_000, (
            "the 60 000 row limit permitted ~25 minutes of CPU for a 3 MB upload"
        )

    def test_the_total_permits_exactly_four_timeframes_at_the_cap(self) -> None:
        """Set to the legitimate maximum, not accidentally below it."""
        limits = InputLimits()
        assert limits.max_total_rows == limits.max_rows_per_timeframe * 4

    def test_a_dataset_over_the_row_limit_is_refused_not_truncated(
        self, client: TestClient
    ) -> None:
        payload = client.post(
            "/api/analysis", json=body(csv_rows(InputLimits().max_rows_per_timeframe + 10))
        ).json()

        assert payload["input_errors"], "an oversized dataset was silently accepted"
        assert payload["technical_available"] is False

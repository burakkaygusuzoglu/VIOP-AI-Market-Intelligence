"""The loading use case: the seam that makes validation unavoidable."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.adapters.market_data import (
    CsvHistoricalMarketDataProvider,
    SyntheticDefect,
    SyntheticHistoricalMarketDataProvider,
)
from app.application.use_cases.load_market_data import LoadValidatedCandles
from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle
from app.domain.market.quality import (
    DataQualityCode,
    DataQualityEngine,
    DataQualityPolicy,
    DataQualityVerdict,
)
from app.domain.technical.engine import compute_technicals
from tests.factories import candle, candles_from_closes

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "market_data"
WINDOW_START = datetime(2020, 1, 1, tzinfo=UTC)
WINDOW_END = datetime(2030, 1, 1, tzinfo=UTC)
ORIGIN = datetime(2026, 1, 2, 9, 0, tzinfo=UTC)


class StubProvider:
    """A minimal provider that returns exactly what it was given."""

    def __init__(self, candles: Sequence[Candle]) -> None:
        self._candles = tuple(candles)

    async def get_candles(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> Sequence[Candle]:
        return self._candles


@pytest.mark.unit
async def test_clean_data_yields_a_usable_series() -> None:
    use_case = LoadValidatedCandles(StubProvider(candles_from_closes(["100", "101", "102"])))
    assessment = await use_case.execute(
        "TEST_FIXTURE_SYMBOL", Timeframe.M15, WINDOW_START, WINDOW_END
    )
    assert assessment.report.verdict is DataQualityVerdict.ACCEPTED
    assert assessment.series is not None
    assert len(assessment.series) == 3


@pytest.mark.unit
async def test_bad_data_is_reported_rather_than_raised() -> None:
    """A blocked dataset is an expected outcome the caller must be able to show."""
    use_case = LoadValidatedCandles(StubProvider((candle(index=0), candle(index=1, volume="-5"))))
    assessment = await use_case.execute(
        "TEST_FIXTURE_SYMBOL", Timeframe.M15, WINDOW_START, WINDOW_END
    )
    assert assessment.report.is_blocked
    assert assessment.series is None
    assert assessment.report.has(DataQualityCode.NEGATIVE_VOLUME)


@pytest.mark.unit
async def test_an_empty_provider_response_is_blocked_not_silently_empty() -> None:
    use_case = LoadValidatedCandles(StubProvider(()))
    assessment = await use_case.execute(
        "TEST_FIXTURE_SYMBOL", Timeframe.M15, WINDOW_START, WINDOW_END
    )
    assert assessment.report.has(DataQualityCode.EMPTY_SERIES)
    assert assessment.series is None


@pytest.mark.unit
async def test_a_plain_provider_still_gets_validated() -> None:
    """The stub implements only ``get_candles`` and is assessed all the same."""
    provider = SyntheticHistoricalMarketDataProvider(
        origin=ORIGIN, defects=frozenset({SyntheticDefect.FORMING_LAST})
    )
    use_case = LoadValidatedCandles(provider)
    assessment = await use_case.execute(
        "SYNTH-MOCK", Timeframe.M15, ORIGIN, datetime(2026, 1, 3, tzinfo=UTC)
    )
    assert assessment.report.has(DataQualityCode.FORMING_CANDLE)
    assert assessment.series is None


@pytest.mark.unit
async def test_provider_parse_issues_reach_the_same_report() -> None:
    """A malformed CSV row and the domain rules land in one place."""
    use_case = LoadValidatedCandles(CsvHistoricalMarketDataProvider(FIXTURES))
    assessment = await use_case.execute(
        "FIXTURE_MALFORMED", Timeframe.M15, WINDOW_START, WINDOW_END
    )
    assert assessment.report.has(DataQualityCode.MALFORMED_ROW)
    assert assessment.report.is_blocked
    assert assessment.series is None


@pytest.mark.unit
async def test_a_clean_csv_file_loads_end_to_end() -> None:
    use_case = LoadValidatedCandles(CsvHistoricalMarketDataProvider(FIXTURES))
    assessment = await use_case.execute("FIXTURE_CLEAN", Timeframe.M15, WINDOW_START, WINDOW_END)
    assert assessment.series is not None
    assert len(assessment.series) == 5


@pytest.mark.unit
async def test_the_policy_is_honoured() -> None:
    use_case = LoadValidatedCandles(
        StubProvider(candles_from_closes(["100", "101"])),
        DataQualityEngine(DataQualityPolicy(minimum_candles=500)),
    )
    assessment = await use_case.execute(
        "TEST_FIXTURE_SYMBOL", Timeframe.M15, WINDOW_START, WINDOW_END
    )
    assert assessment.report.has(DataQualityCode.INSUFFICIENT_HISTORY)


@pytest.mark.unit
async def test_the_full_phase_one_pipeline_runs_from_a_provider_to_indicators() -> None:
    """Provider -> data quality -> validated series -> deterministic engine.

    The vertical slice Phase 1 delivers. Only the first stage would change for
    replay, backtest or a live feed (master spec section 74).
    """
    provider = SyntheticHistoricalMarketDataProvider(origin=ORIGIN)
    use_case = LoadValidatedCandles(provider)
    assessment = await use_case.execute(
        "SYNTH-MOCK", Timeframe.M15, ORIGIN, datetime(2026, 1, 4, tzinfo=UTC)
    )

    assert assessment.series is not None
    snapshot = compute_technicals(assessment.series)

    assert snapshot.candle_count == len(assessment.series)
    assert snapshot.rsi[-1] is not None
    assert snapshot.atr[-1] is not None
    assert snapshot.adx[-1] is not None
    assert snapshot.vwap[-1] is not None


@pytest.mark.unit
async def test_two_providers_reach_the_same_engine_unchanged() -> None:
    """Section 74 parity, demonstrated rather than asserted in a comment.

    The same candles delivered by two different provider implementations
    produce identical indicator values, because the mathematics does not know
    which provider it came from.
    """
    synthetic = SyntheticHistoricalMarketDataProvider(origin=ORIGIN)
    candles = synthetic.generate("SYNTH-MOCK", Timeframe.M15, count=80)

    from_synthetic = await LoadValidatedCandles(synthetic).execute(
        "SYNTH-MOCK", Timeframe.M15, ORIGIN, ORIGIN + 80 * timedelta(minutes=15)
    )
    from_stub = await LoadValidatedCandles(StubProvider(candles)).execute(
        "SYNTH-MOCK", Timeframe.M15, WINDOW_START, WINDOW_END
    )

    assert from_synthetic.series is not None
    assert from_stub.series is not None
    assert compute_technicals(from_synthetic.series) == compute_technicals(from_stub.series)

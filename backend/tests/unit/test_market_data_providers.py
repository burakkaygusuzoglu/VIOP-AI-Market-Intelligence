"""CSV and synthetic historical providers.

Every CSV under ``tests/fixtures/market_data`` is TEST_FIXTURE data. The prices
are invented and describe no instrument.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.market_data import (
    CsvHistoricalMarketDataProvider,
    CsvSchemaError,
    SyntheticDefect,
    SyntheticHistoricalMarketDataProvider,
)
from app.application.ports.market_data import (
    DiagnosticHistoricalMarketDataProvider,
    HistoricalMarketDataProvider,
)
from app.domain.common.enums import Timeframe
from app.domain.market.quality import DataQualityCode, DataQualityEngine
from app.domain.market.series import CandleSeries

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "market_data"
WINDOW_START = datetime(2020, 1, 1, tzinfo=UTC)
WINDOW_END = datetime(2030, 1, 1, tzinfo=UTC)
ORIGIN = datetime(2026, 1, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def csv_provider() -> CsvHistoricalMarketDataProvider:
    return CsvHistoricalMarketDataProvider(FIXTURES)


# ----------------------------------------------------------------------
# Both providers satisfy the port
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_both_providers_satisfy_the_historical_port(
    csv_provider: CsvHistoricalMarketDataProvider,
) -> None:
    synthetic = SyntheticHistoricalMarketDataProvider(origin=ORIGIN)
    assert isinstance(csv_provider, HistoricalMarketDataProvider)
    assert isinstance(synthetic, HistoricalMarketDataProvider)


@pytest.mark.unit
def test_only_the_csv_provider_claims_the_diagnostic_capability(
    csv_provider: CsvHistoricalMarketDataProvider,
) -> None:
    """The synthetic provider has no read failures to report, so it does not."""
    synthetic = SyntheticHistoricalMarketDataProvider(origin=ORIGIN)
    assert isinstance(csv_provider, DiagnosticHistoricalMarketDataProvider)
    assert not isinstance(synthetic, DiagnosticHistoricalMarketDataProvider)


# ----------------------------------------------------------------------
# CSV parsing
# ----------------------------------------------------------------------


@pytest.mark.unit
async def test_csv_parses_a_clean_file(csv_provider: CsvHistoricalMarketDataProvider) -> None:
    candles = await csv_provider.get_candles(
        "FIXTURE_CLEAN", Timeframe.M15, WINDOW_START, WINDOW_END
    )
    assert len(candles) == 5
    assert candles[0].open_time == datetime(2026, 1, 2, 9, 0, tzinfo=UTC)
    assert candles[0].symbol == "FIXTURE_CLEAN"
    assert candles[0].timeframe is Timeframe.M15


@pytest.mark.unit
async def test_csv_prices_never_pass_through_float(
    csv_provider: CsvHistoricalMarketDataProvider,
) -> None:
    """Parsed straight from text to Decimal, so the input is not pre-rounded."""
    candles = await csv_provider.get_candles(
        "FIXTURE_CLEAN", Timeframe.M15, WINDOW_START, WINDOW_END
    )
    assert candles[0].close == Decimal("100.40")
    assert candles[0].close - candles[0].open == Decimal("0.30")


@pytest.mark.unit
async def test_csv_reads_optional_columns(
    csv_provider: CsvHistoricalMarketDataProvider,
) -> None:
    candles = await csv_provider.get_candles(
        "FIXTURE_RICH", Timeframe.M15, WINDOW_START, WINDOW_END
    )
    assert candles[0].open_interest == Decimal("24500")
    assert candles[2].open_interest is None  # blank stays missing, never zero


@pytest.mark.unit
async def test_csv_preserves_an_explicitly_forming_candle(
    csv_provider: CsvHistoricalMarketDataProvider,
) -> None:
    """The adapter does not decide; it reports what the file said."""
    candles = await csv_provider.get_candles(
        "FIXTURE_RICH", Timeframe.M15, WINDOW_START, WINDOW_END
    )
    assert candles[0].is_closed is True
    assert candles[2].is_closed is False


@pytest.mark.unit
async def test_a_forming_candle_from_a_file_is_blocked_downstream(
    csv_provider: CsvHistoricalMarketDataProvider,
) -> None:
    """End to end: the file's unclosed bar cannot become a historical fact."""
    fetch = await csv_provider.fetch("FIXTURE_RICH", Timeframe.M15, WINDOW_START, WINDOW_END)
    assessment = DataQualityEngine().assess(CandleSeries.of(fetch.candles))
    assert assessment.report.has(DataQualityCode.FORMING_CANDLE)
    assert assessment.series is None


@pytest.mark.unit
async def test_csv_window_is_half_open(
    csv_provider: CsvHistoricalMarketDataProvider,
) -> None:
    """``start <= open_time < end``, as the port specifies."""
    candles = await csv_provider.get_candles(
        "FIXTURE_CLEAN",
        Timeframe.M15,
        datetime(2026, 1, 2, 9, 15, tzinfo=UTC),
        datetime(2026, 1, 2, 9, 45, tzinfo=UTC),
    )
    assert [candle.open_time.minute for candle in candles] == [15, 30]


@pytest.mark.unit
async def test_malformed_rows_are_reported_not_raised(
    csv_provider: CsvHistoricalMarketDataProvider,
) -> None:
    """Three bad rows, three findings, and the good rows still come through."""
    fetch = await csv_provider.fetch("FIXTURE_MALFORMED", Timeframe.M15, WINDOW_START, WINDOW_END)
    assert len(fetch.candles) == 3
    assert len(fetch.issues) == 3
    assert all(issue.code is DataQualityCode.MALFORMED_ROW for issue in fetch.issues)

    messages = " ".join(issue.message for issue in fetch.issues)
    assert "line 3" in messages and "not a number" in messages
    assert "line 5" in messages and "ISO-8601" in messages
    assert "line 6" in messages and "empty" in messages


@pytest.mark.unit
async def test_malformed_rows_block_the_dataset(
    csv_provider: CsvHistoricalMarketDataProvider,
) -> None:
    """A silently dropped row would leave a gap nobody could explain."""
    fetch = await csv_provider.fetch("FIXTURE_MALFORMED", Timeframe.M15, WINDOW_START, WINDOW_END)
    assessment = DataQualityEngine().assess(
        CandleSeries.of(fetch.candles), extra_issues=fetch.issues
    )
    assert assessment.report.is_blocked
    assert assessment.series is None


@pytest.mark.unit
async def test_naive_timestamps_take_the_configured_default_zone() -> None:
    provider = CsvHistoricalMarketDataProvider(FIXTURES, default_tz=timezone(timedelta(hours=3)))
    candles = await provider.get_candles(
        "FIXTURE_NAIVE_TIME", Timeframe.M15, WINDOW_START, WINDOW_END
    )
    assert candles[0].open_time == datetime(2026, 1, 2, 6, 0, tzinfo=UTC)


@pytest.mark.unit
async def test_naive_timestamps_are_refused_when_no_default_zone_is_set() -> None:
    """Guessing UTC would shift every candle in the file without saying so."""
    provider = CsvHistoricalMarketDataProvider(FIXTURES, default_tz=None)
    fetch = await provider.fetch("FIXTURE_NAIVE_TIME", Timeframe.M15, WINDOW_START, WINDOW_END)
    assert fetch.candles == ()
    assert len(fetch.issues) == 2
    assert "no UTC offset" in fetch.issues[0].message


@pytest.mark.unit
async def test_a_missing_column_is_a_schema_error_not_a_finding(
    csv_provider: CsvHistoricalMarketDataProvider,
) -> None:
    """There is no dataset to weigh up, so this is raised rather than reported."""
    with pytest.raises(CsvSchemaError, match="close"):
        await csv_provider.get_candles(
            "FIXTURE_NO_HEADER_COLUMN", Timeframe.M15, WINDOW_START, WINDOW_END
        )


@pytest.mark.unit
async def test_a_missing_file_is_a_schema_error(
    csv_provider: CsvHistoricalMarketDataProvider,
) -> None:
    with pytest.raises(CsvSchemaError, match="no market data file"):
        await csv_provider.get_candles("FIXTURE_ABSENT", Timeframe.M15, WINDOW_START, WINDOW_END)


@pytest.mark.unit
async def test_csv_reads_are_repeatable(
    csv_provider: CsvHistoricalMarketDataProvider,
) -> None:
    first = await csv_provider.get_candles("FIXTURE_CLEAN", Timeframe.M15, WINDOW_START, WINDOW_END)
    second = await csv_provider.get_candles(
        "FIXTURE_CLEAN", Timeframe.M15, WINDOW_START, WINDOW_END
    )
    assert first == second


# ----------------------------------------------------------------------
# Synthetic provider
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_synthetic_output_is_reproducible() -> None:
    first = SyntheticHistoricalMarketDataProvider(origin=ORIGIN)
    second = SyntheticHistoricalMarketDataProvider(origin=ORIGIN)
    assert first.generate("SYNTH-MOCK", Timeframe.M15, count=50) == second.generate(
        "SYNTH-MOCK", Timeframe.M15, count=50
    )


@pytest.mark.unit
def test_synthetic_candle_n_does_not_depend_on_how_many_were_asked_for() -> None:
    """The property a replay harness needs: no dependence on request size."""
    provider = SyntheticHistoricalMarketDataProvider(origin=ORIGIN)
    short = provider.generate("SYNTH-MOCK", Timeframe.M15, count=10)
    long = provider.generate("SYNTH-MOCK", Timeframe.M15, count=200)
    assert short == long[:10]

    offset = provider.generate("SYNTH-MOCK", Timeframe.M15, count=5, offset=100)
    assert offset == long[100:105]


@pytest.mark.unit
def test_synthetic_data_passes_its_own_quality_engine() -> None:
    provider = SyntheticHistoricalMarketDataProvider(origin=ORIGIN)
    candles = provider.generate("SYNTH-MOCK", Timeframe.M15, count=120)
    assessment = DataQualityEngine().assess(CandleSeries.of(candles))
    assert not assessment.report.is_blocked, assessment.report.issues
    assert assessment.series is not None


@pytest.mark.unit
def test_synthetic_candles_have_a_valid_ohlc_envelope() -> None:
    provider = SyntheticHistoricalMarketDataProvider(origin=ORIGIN)
    for candle in provider.generate("SYNTH-MOCK", Timeframe.M15, count=100):
        assert candle.low <= min(candle.open, candle.close)
        assert candle.high >= max(candle.open, candle.close)
        assert candle.volume > 0


@pytest.mark.unit
def test_synthetic_provider_invents_no_exchange_facts() -> None:
    """Master spec section 118: nothing here describes a real contract."""
    provider = SyntheticHistoricalMarketDataProvider(origin=ORIGIN)
    candle = provider.generate("SYNTH-MOCK", Timeframe.M15, count=1)[0]
    assert candle.symbol == "SYNTH-MOCK"
    assert candle.open_interest is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("defect", "expected"),
    (
        (SyntheticDefect.GAP, DataQualityCode.MISSING_CANDLES),
        (SyntheticDefect.DUPLICATE, DataQualityCode.DUPLICATE_CANDLE),
        (SyntheticDefect.OUT_OF_ORDER, DataQualityCode.OUT_OF_ORDER),
        (SyntheticDefect.FORMING_LAST, DataQualityCode.FORMING_CANDLE),
        (SyntheticDefect.ZERO_VOLUME, DataQualityCode.ZERO_VOLUME),
    ),
)
def test_injected_defects_are_detected(defect: SyntheticDefect, expected: DataQualityCode) -> None:
    """The engine is exercised against a provider, not only hand-built fixtures."""
    provider = SyntheticHistoricalMarketDataProvider(origin=ORIGIN, defects=frozenset({defect}))
    candles = provider.generate("SYNTH-MOCK", Timeframe.M15, count=40)
    assessment = DataQualityEngine().assess(CandleSeries.of(candles))
    assert assessment.report.has(expected), assessment.report.codes


@pytest.mark.unit
async def test_synthetic_window_is_half_open() -> None:
    provider = SyntheticHistoricalMarketDataProvider(origin=ORIGIN)
    candles = await provider.get_candles(
        "SYNTH-MOCK",
        Timeframe.M15,
        ORIGIN,
        ORIGIN + timedelta(minutes=45),
    )
    assert len(candles) == 3
    assert candles[0].open_time == ORIGIN


@pytest.mark.unit
async def test_synthetic_empty_window() -> None:
    provider = SyntheticHistoricalMarketDataProvider(origin=ORIGIN)
    assert await provider.get_candles("SYNTH-MOCK", Timeframe.M15, ORIGIN, ORIGIN) == ()


@pytest.mark.unit
def test_synthetic_origin_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        SyntheticHistoricalMarketDataProvider(origin=datetime(2026, 1, 2, 9, 0))  # noqa: DTZ001

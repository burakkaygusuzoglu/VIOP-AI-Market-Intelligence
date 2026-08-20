"""The Data Quality Engine (master spec section 40).

Every detectable condition gets a test, and every blocking condition is checked
to actually block - a rule that is merely reported would let corrupt data reach
a calculation.

Fixture values only; none of this is market data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle
from app.domain.market.quality import (
    DataQualityAssessment,
    DataQualityCode,
    DataQualityEngine,
    DataQualityIssue,
    DataQualityPolicy,
    DataQualitySeverity,
    DataQualityVerdict,
)
from app.domain.market.series import CandleSeries
from tests.factories import FIXTURE_ORIGIN, candle, candles_from_closes

ENGINE = DataQualityEngine()


def _assess(*candles: Candle) -> DataQualityAssessment:
    return ENGINE.assess(CandleSeries.of(candles))


# ----------------------------------------------------------------------
# Accepting good data
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_clean_series_is_accepted_with_no_issues() -> None:
    assessment = ENGINE.assess(CandleSeries.of(candles_from_closes(["100", "101", "102"])))
    assert assessment.report.verdict is DataQualityVerdict.ACCEPTED
    assert assessment.report.issues == ()
    assert assessment.series is not None
    assert len(assessment.series) == 3


@pytest.mark.unit
def test_the_accepted_series_is_what_the_indicators_receive() -> None:
    """The type is the guarantee: only this path produces a usable series."""
    assessment = ENGINE.assess(CandleSeries.of(candles_from_closes(["100", "101"])))
    assert assessment.is_usable
    assert assessment.series is not None
    assert assessment.series.symbol == "TEST_FIXTURE_SYMBOL"
    assert assessment.series.timeframe is Timeframe.M15


# ----------------------------------------------------------------------
# Blocking conditions
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_empty_input_is_blocked() -> None:
    assessment = ENGINE.assess(CandleSeries.of(()))
    assert assessment.report.verdict is DataQualityVerdict.BLOCKED
    assert assessment.report.has(DataQualityCode.EMPTY_SERIES)
    assert assessment.series is None


@pytest.mark.unit
def test_naive_timestamp_is_blocked() -> None:
    naive = candle(index=0, origin=datetime(2026, 1, 2, 9, 0))  # noqa: DTZ001
    assessment = _assess(naive)
    assert assessment.report.has(DataQualityCode.NAIVE_TIMESTAMP)
    assert assessment.series is None


@pytest.mark.unit
@pytest.mark.parametrize("bad", ("NaN", "Infinity", "-Infinity"))
def test_non_finite_values_are_blocked(bad: str) -> None:
    # Bounds are given explicitly: deriving them would compare against NaN,
    # which the decimal module refuses to do before the engine ever sees it.
    assessment = _assess(candle(index=0, open="100", high="101", low="99", close=Decimal(bad)))
    assert assessment.report.has(DataQualityCode.NON_FINITE_VALUE)
    assert assessment.series is None


@pytest.mark.unit
def test_negative_price_is_blocked() -> None:
    bad = candle(index=0, open="-5", high="10", low="-6", close="-5")
    assessment = _assess(bad)
    assert assessment.report.has(DataQualityCode.NEGATIVE_PRICE)


@pytest.mark.unit
def test_zero_price_is_blocked() -> None:
    bad = candle(index=0, open="0", high="1", low="0", close="0")
    assessment = _assess(bad)
    assert assessment.report.has(DataQualityCode.ZERO_PRICE)


@pytest.mark.unit
def test_high_below_low_is_blocked() -> None:
    bad = candle(index=0, open="100", high="99", low="101", close="100")
    assessment = _assess(bad)
    assert assessment.report.has(DataQualityCode.INVALID_OHLC_RELATIONSHIP)


@pytest.mark.unit
def test_close_above_the_high_is_blocked() -> None:
    bad = candle(index=0, open="100", high="101", low="99", close="105")
    assessment = _assess(bad)
    assert assessment.report.has(DataQualityCode.INVALID_OHLC_RELATIONSHIP)


@pytest.mark.unit
def test_open_below_the_low_is_blocked() -> None:
    bad = candle(index=0, open="95", high="101", low="99", close="100")
    assessment = _assess(bad)
    assert assessment.report.has(DataQualityCode.INVALID_OHLC_RELATIONSHIP)


@pytest.mark.unit
def test_negative_volume_is_blocked() -> None:
    assessment = _assess(candle(index=0, volume="-1"))
    assert assessment.report.has(DataQualityCode.NEGATIVE_VOLUME)


@pytest.mark.unit
def test_negative_open_interest_is_blocked() -> None:
    assessment = _assess(candle(index=0, open_interest="-10"))
    assert assessment.report.has(DataQualityCode.NEGATIVE_VOLUME)


@pytest.mark.unit
def test_forming_candle_is_blocked_from_historical_data() -> None:
    """The rule that keeps an unclosed bar from becoming a settled fact."""
    assessment = _assess(candle(index=0), candle(index=1, is_closed=False))
    assert assessment.report.has(DataQualityCode.FORMING_CANDLE)
    assert assessment.series is None


@pytest.mark.unit
def test_symbol_mismatch_is_blocked() -> None:
    assessment = _assess(candle(index=0), candle(index=1, symbol="OTHER_FIXTURE"))
    assert assessment.report.has(DataQualityCode.SYMBOL_MISMATCH)


@pytest.mark.unit
def test_timeframe_mismatch_is_blocked() -> None:
    assessment = _assess(candle(index=0), candle(index=1, timeframe=Timeframe.H1))
    assert assessment.report.has(DataQualityCode.TIMEFRAME_MISMATCH)


@pytest.mark.unit
def test_identical_duplicate_is_blocked_and_named_as_a_duplicate() -> None:
    one = candle(index=0, close="100")
    assessment = _assess(one, one)
    assert assessment.report.has(DataQualityCode.DUPLICATE_CANDLE)
    assert assessment.series is None


@pytest.mark.unit
def test_conflicting_duplicate_is_distinguished_from_an_identical_one() -> None:
    """Two different candles for one timestamp is a worse problem than a repeat.

    Deduplicating either case silently would destroy the evidence that a
    provider is emitting contradictory data.
    """
    assessment = _assess(candle(index=0, close="100"), candle(index=0, close="101"))
    assert assessment.report.has(DataQualityCode.CONFLICTING_DUPLICATE)
    assert not assessment.report.has(DataQualityCode.DUPLICATE_CANDLE)


@pytest.mark.unit
def test_out_of_order_candles_are_blocked_not_sorted() -> None:
    assessment = _assess(candle(index=3), candle(index=1))
    assert assessment.report.has(DataQualityCode.OUT_OF_ORDER)
    assert assessment.series is None


@pytest.mark.unit
def test_spacing_that_is_not_a_multiple_of_the_interval_is_blocked() -> None:
    """A 15M series whose candles are 7 minutes apart is not a 15M series."""
    first = candle(index=0)
    second = candle(index=0, origin=FIXTURE_ORIGIN + timedelta(minutes=7))
    assessment = _assess(first, second)
    assert assessment.report.has(DataQualityCode.IRREGULAR_INTERVAL)
    assert assessment.series is None


# ----------------------------------------------------------------------
# Advisory conditions
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_gap_warns_but_does_not_block() -> None:
    """Missing data and a closed market are indistinguishable without verified
    session hours (master spec section 118), so this cannot be a block."""
    assessment = _assess(candle(index=0), candle(index=1), candle(index=5))
    report = assessment.report
    assert report.verdict is DataQualityVerdict.ACCEPTED_WITH_WARNINGS
    assert report.has(DataQualityCode.MISSING_CANDLES)
    assert assessment.series is not None
    issue = next(i for i in report.issues if i.code is DataQualityCode.MISSING_CANDLES)
    assert "3 candle interval(s)" in issue.message


@pytest.mark.unit
def test_zero_volume_warns_but_does_not_block() -> None:
    """Legitimate in an illiquid contract - surfaced, never hidden."""
    assessment = _assess(candle(index=0, volume="0"), candle(index=1))
    report = assessment.report
    assert report.has(DataQualityCode.ZERO_VOLUME)
    assert report.verdict is DataQualityVerdict.ACCEPTED_WITH_WARNINGS
    assert assessment.series is not None


@pytest.mark.unit
def test_zero_volume_flagging_can_be_turned_off() -> None:
    engine = DataQualityEngine(DataQualityPolicy(flag_zero_volume=False))
    assessment = engine.assess(
        CandleSeries.of((candle(index=0, volume="0"), candle(index=1, volume="0")))
    )
    assert not assessment.report.has(DataQualityCode.ZERO_VOLUME)
    assert assessment.report.verdict is DataQualityVerdict.ACCEPTED


@pytest.mark.unit
def test_an_extreme_move_warns() -> None:
    assessment = _assess(
        candle(index=0, close="100"),
        candle(index=1, open="100", high="200", low="99", close="180"),
    )
    report = assessment.report
    assert report.has(DataQualityCode.EXTREME_MOVE)
    assert assessment.series is not None


@pytest.mark.unit
def test_the_extreme_move_threshold_is_configurable() -> None:
    engine = DataQualityEngine(DataQualityPolicy(extreme_move_ratio=Decimal("0.9")))
    assessment = engine.assess(
        CandleSeries.of(
            (
                candle(index=0, close="100"),
                candle(index=1, open="100", high="200", low="99", close="180"),
            )
        )
    )
    assert not assessment.report.has(DataQualityCode.EXTREME_MOVE)


@pytest.mark.unit
def test_insufficient_history_warns_against_the_configured_minimum() -> None:
    engine = DataQualityEngine(DataQualityPolicy(minimum_candles=200))
    assessment = engine.assess(CandleSeries.of(candles_from_closes(["100", "101"])))
    assert assessment.report.has(DataQualityCode.INSUFFICIENT_HISTORY)
    assert assessment.series is not None


# ----------------------------------------------------------------------
# Normalization
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_timestamps_are_normalized_to_utc_and_the_change_is_recorded() -> None:
    """The one normalization performed, and it preserves the instant exactly."""
    istanbul = timezone(timedelta(hours=3))
    candles = (
        candle(index=0, origin=datetime(2026, 1, 2, 12, 0, tzinfo=istanbul)),
        candle(index=1, origin=datetime(2026, 1, 2, 12, 0, tzinfo=istanbul)),
    )
    assessment = ENGINE.assess(CandleSeries.of(candles))
    assert assessment.report.has(DataQualityCode.TIMEZONE_NORMALIZED)
    assert assessment.series is not None
    assert assessment.series[0].open_time == datetime(2026, 1, 2, 9, 0, tzinfo=UTC)
    assert assessment.series[0].open_time == candles[0].open_time  # same instant


@pytest.mark.unit
def test_normalization_is_informational_not_a_warning() -> None:
    istanbul = timezone(timedelta(hours=3))
    origin = datetime(2026, 1, 2, 12, 0, tzinfo=istanbul)
    assessment = ENGINE.assess(
        CandleSeries.of((candle(index=0, origin=origin), candle(index=1, origin=origin)))
    )
    issue = next(
        i for i in assessment.report.issues if i.code is DataQualityCode.TIMEZONE_NORMALIZED
    )
    assert issue.severity is DataQualitySeverity.INFO
    assert assessment.report.verdict is DataQualityVerdict.ACCEPTED


@pytest.mark.unit
def test_utc_input_is_not_rewritten() -> None:
    assessment = ENGINE.assess(CandleSeries.of(candles_from_closes(["100", "101"])))
    assert not assessment.report.has(DataQualityCode.TIMEZONE_NORMALIZED)


# ----------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_issues_carry_the_evidence_needed_to_locate_them() -> None:
    assessment = _assess(candle(index=0), candle(index=1, volume="-5"))
    issue = next(i for i in assessment.report.issues if i.code is DataQualityCode.NEGATIVE_VOLUME)
    assert issue.candle_index == 1
    assert issue.open_time is not None
    assert "-5" in issue.message


@pytest.mark.unit
def test_provider_issues_are_folded_into_the_same_report() -> None:
    """A malformed CSV row and a domain violation appear together."""
    parse_issue = DataQualityIssue(
        code=DataQualityCode.MALFORMED_ROW,
        severity=DataQualitySeverity.BLOCK,
        message="prices.csv line 4: close 'abc' is not a number",
    )
    assessment = ENGINE.assess(
        CandleSeries.of(candles_from_closes(["100", "101"])),
        extra_issues=[parse_issue],
    )
    assert assessment.report.has(DataQualityCode.MALFORMED_ROW)
    assert assessment.report.verdict is DataQualityVerdict.BLOCKED
    assert assessment.series is None


@pytest.mark.unit
def test_report_separates_blocking_issues_from_warnings() -> None:
    assessment = _assess(
        candle(index=0, volume="0"),
        candle(index=1, volume="-5"),
    )
    report = assessment.report
    assert {issue.code for issue in report.blocking_issues} == {DataQualityCode.NEGATIVE_VOLUME}
    assert {issue.code for issue in report.warnings} == {DataQualityCode.ZERO_VOLUME}


@pytest.mark.unit
def test_report_records_the_dataset_it_describes() -> None:
    assessment = ENGINE.assess(CandleSeries.of(candles_from_closes(["100", "101", "102"])))
    report = assessment.report
    assert report.candle_count == 3
    assert report.symbol == "TEST_FIXTURE_SYMBOL"
    assert report.timeframe is Timeframe.M15


@pytest.mark.unit
def test_a_blocked_dataset_still_reports_every_reason_found() -> None:
    """One fix at a time would make a corrupt file a long game of whack-a-mole."""
    assessment = _assess(
        candle(index=0, volume="-1"),
        candle(index=1, open="100", high="99", low="101", close="100"),
        candle(index=2, is_closed=False),
    )
    codes = assessment.report.codes
    assert DataQualityCode.NEGATIVE_VOLUME in codes
    assert DataQualityCode.INVALID_OHLC_RELATIONSHIP in codes
    assert DataQualityCode.FORMING_CANDLE in codes


@pytest.mark.unit
def test_assessment_is_deterministic() -> None:
    candles = candles_from_closes(["100", "101", "102"])
    first = ENGINE.assess(CandleSeries.of(candles)).report
    second = ENGINE.assess(CandleSeries.of(candles)).report
    assert first == second

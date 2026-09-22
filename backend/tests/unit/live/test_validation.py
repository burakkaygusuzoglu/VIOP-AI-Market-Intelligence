"""Nothing a provider says becomes a candle without passing here (Phase 13).

Every test sends one malformed or hostile event and expects a *refusal* - not
a clamped value, not a moved timestamp, not a filled-in price. Golden cases L
(invalid OHLC) and M (huge Decimal) live here.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.adapters.live.mock_stream import candle_event
from app.domain.live.events import Observation, RawCandleEvent, StreamKey
from app.domain.live.limits import LiveLimits
from app.domain.live.validation import Rejection, RejectionCode, validate
from tests.unit.live.support import M5, RECEIVE_START, SYMBOL, at

pytestmark = pytest.mark.unit

KEY = StreamKey(SYMBOL, M5)
LIMITS = LiveLimits()


def good(**changes: object) -> RawCandleEvent:
    event = candle_event(SYMBOL, M5, at(10), "100", "101", "99", "100.5", sequence=2)
    return replace(event, **changes)


def check(event: object) -> Observation | Rejection:
    return validate(event, key=KEY, received_at=RECEIVE_START, limits=LIMITS)  # type: ignore[arg-type]


def code_of(result: Observation | Rejection) -> RejectionCode:
    assert isinstance(result, Rejection), f"expected a refusal, got {result!r}"
    return result.code


class TestAWellFormedEventIsAccepted:
    def test_it_becomes_an_observation_with_both_clocks(self) -> None:
        result = check(good())

        assert isinstance(result, Observation)
        assert result.event_time == at(15)
        assert result.received_at == RECEIVE_START
        assert result.event_time != result.received_at
        assert result.candle.close == Decimal("100.5")
        assert result.candle.is_closed

    def test_values_are_kept_exactly(self) -> None:
        result = check(good(close=Decimal("100.12345")))

        assert isinstance(result, Observation)
        assert result.candle.close == Decimal("100.12345")


class TestLInvalidOhlcIsRefusedNotClamped:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("high", Decimal("99.5")),  # below the close
            ("low", Decimal("100.75")),  # above the close
            ("high", Decimal("98")),  # below the low
        ],
    )
    def test_inconsistent_prices_are_refused(self, field: str, value: Decimal) -> None:
        assert code_of(check(good(**{field: value}))) is RejectionCode.INCONSISTENT_OHLC

    @pytest.mark.parametrize("value", [Decimal("0"), Decimal("-1")])
    def test_a_non_positive_price_is_refused(self, value: Decimal) -> None:
        assert code_of(check(good(low=value))) is RejectionCode.NON_POSITIVE_PRICE

    def test_a_negative_volume_is_refused(self) -> None:
        assert code_of(check(good(volume=Decimal("-1")))) is RejectionCode.NEGATIVE_VOLUME

    @pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
    def test_a_missing_value_is_not_invented(self, field: str) -> None:
        assert code_of(check(good(**{field: None}))) is RejectionCode.NON_DECIMAL


class TestMHugeOrUnrepresentableDecimals:
    @pytest.mark.parametrize(
        "value",
        [
            Decimal("1E+30"),
            Decimal("1000000000.01"),
            Decimal("1E-9999"),
            Decimal("100.12345678901"),
        ],
    )
    def test_out_of_range_values_are_refused(self, value: Decimal) -> None:
        assert code_of(check(good(open=value, low=Decimal("0.0001")))) in (
            RejectionCode.OUT_OF_BOUNDS,
            RejectionCode.INCONSISTENT_OHLC,
        )

    def test_a_huge_price_is_refused_on_its_own_terms(self) -> None:
        huge = Decimal("1E+30")
        event = good(open=huge, high=huge, low=huge, close=huge)

        assert code_of(check(event)) is RejectionCode.OUT_OF_BOUNDS

    @pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
    def test_non_finite_values_are_refused(self, value: Decimal) -> None:
        assert code_of(check(good(close=value))) is RejectionCode.NON_FINITE

    def test_a_float_is_refused_rather_than_converted(self) -> None:
        """0.1 as a float is not a price anybody quoted."""
        assert code_of(check(good(close=100.5))) is RejectionCode.NON_DECIMAL


class TestTimestampsAreNeverRepaired:
    def test_a_naive_open_time_is_refused(self) -> None:
        naive = at(10).replace(tzinfo=None)

        assert code_of(check(good(open_time=naive))) is RejectionCode.NAIVE_TIMESTAMP

    def test_a_naive_event_time_is_refused(self) -> None:
        naive = at(15).replace(tzinfo=None)

        assert code_of(check(good(event_time=naive))) is RejectionCode.NAIVE_TIMESTAMP

    def test_a_timestamp_that_is_not_a_timestamp_is_refused(self) -> None:
        assert code_of(check(good(open_time="2026-03-02T09:10"))) is RejectionCode.MALFORMED

    def test_an_off_grid_open_time_is_refused_not_moved(self) -> None:
        assert (
            code_of(check(good(open_time=at(10) + timedelta(minutes=2))))
            is RejectionCode.MISALIGNED_OPEN_TIME
        )

    def test_a_non_utc_offset_on_the_grid_is_accepted(self) -> None:
        """Istanbul is UTC+3: a whole-hour offset keeps the 5M grid intact."""
        istanbul = timezone(timedelta(hours=3))
        result = check(
            good(open_time=at(10).astimezone(istanbul), event_time=at(15).astimezone(istanbul))
        )

        assert isinstance(result, Observation)

    def test_a_candle_reported_closed_before_its_interval_ended_is_refused(self) -> None:
        assert code_of(check(good(event_time=at(14)))) is RejectionCode.PREMATURE_CLOSE

    def test_a_forming_candle_outside_its_own_interval_is_refused(self) -> None:
        result = check(good(closed=False, event_time=at(20)))

        assert code_of(result) is RejectionCode.FORMING_OUTSIDE_INTERVAL

    def test_a_forged_future_event_is_refused(self) -> None:
        """Ahead of receive time by more than the permitted skew."""
        future_open = RECEIVE_START + timedelta(hours=1)
        event = good(open_time=future_open, event_time=future_open + timedelta(minutes=5))

        assert code_of(check(event)) is RejectionCode.FUTURE_EVENT


class TestHostileIdentity:
    @pytest.mark.parametrize(
        "symbol",
        ["'; DROP TABLE backtest_runs;--", "<script>", "../../etc/passwd", "X" * 100, "", "a b"],
    )
    def test_a_hostile_symbol_is_refused(self, symbol: str) -> None:
        assert code_of(check(good(symbol=symbol))) is RejectionCode.INVALID_SYMBOL

    def test_a_refusal_never_echoes_the_hostile_text(self) -> None:
        hostile = "<script>alert(1)</script>"
        result = check(good(symbol=hostile))

        assert isinstance(result, Rejection)
        assert hostile not in result.detail

    def test_another_symbol_is_the_wrong_stream(self) -> None:
        assert code_of(check(good(symbol="OTHER"))) is RejectionCode.WRONG_STREAM

    def test_another_timeframe_is_the_wrong_stream(self) -> None:
        assert code_of(check(good(timeframe="1H"))) is RejectionCode.WRONG_STREAM

    @pytest.mark.parametrize("value", ["yes", 1, None])
    def test_closed_must_be_a_real_boolean(self, value: object) -> None:
        assert code_of(check(good(closed=value))) is RejectionCode.MALFORMED

    @pytest.mark.parametrize("value", [-1, "3", 2.0, True])
    def test_an_invalid_sequence_is_refused(self, value: object) -> None:
        assert code_of(check(good(sequence=value))) is RejectionCode.INVALID_SEQUENCE

    def test_a_forming_update_keeps_no_sequence(self) -> None:
        """Sequences number closed candles; on a forming update they mean nothing."""
        result = check(good(closed=False, event_time=at(12), sequence=9))

        assert isinstance(result, Observation)
        assert result.sequence is None


def test_the_receive_clock_itself_must_be_aware() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        validate(good(), key=KEY, received_at=datetime(2026, 1, 1), limits=LIMITS)  # noqa: DTZ001


def test_received_at_utc_is_not_confused_with_event_time() -> None:
    result = check(good())

    assert isinstance(result, Observation)
    assert result.received_at.tzinfo is UTC or result.received_at.utcoffset() == timedelta(0)

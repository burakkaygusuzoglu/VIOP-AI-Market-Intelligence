"""Market regime classification.

Markets are built whole and run through the *real* Phase 1 indicators, because
the property under test is that several independent measurements agree.
Stubbing ADX or the EMA stack would test the branch table and nothing else.

Prices are TEST_FIXTURE data and describe no instrument.
"""

from __future__ import annotations

import pytest

from app.domain.structure.engine import StructureConfig, analyse_structure
from app.domain.structure.market_structure import StructureBias
from app.domain.structure.regime import (
    EmaStack,
    MarketRegime,
    RegimeAssessment,
    RegimeConfig,
    VolatilityState,
)
from app.domain.structure.swings import SwingConfig
from app.domain.technical.engine import TechnicalConfig, compute_technicals
from tests.factories import ohlcv_series

CONFIG = StructureConfig(swings=SwingConfig(left=2, right=2))
TECHNICALS = TechnicalConfig(ema_periods=(9, 20, 50), sma_periods=(20,))

Market = tuple[list[float], list[float], list[float]]


def zigzag(size: int, direction: int, advance: float, pullback: float) -> Market:
    """A trend that actually retraces.

    Four candles with the trend, two against. The two-candle pullback matters:
    with a ``right=2`` confirmation window a one-candle dip never leaves a peak
    standing as the highest for long enough, so a market with shallow pullbacks
    has no swing structure at all - which the engine correctly reports as
    INSUFFICIENT rather than inventing a trend from the EMA stack alone.
    """
    closes: list[float] = []
    price = 200.0
    for index in range(size):
        price += -direction * pullback if index % 6 in (4, 5) else direction * advance
        closes.append(round(price, 2))
    return [v + 0.8 for v in closes], [v - 0.8 for v in closes], closes


def oscillating(size: int, amplitude: float, base: float = 200.0) -> Market:
    closes = [base + (amplitude if i % 4 in (1, 2) else -amplitude) for i in range(size)]
    return (
        [v + amplitude * 0.3 for v in closes],
        [v - amplitude * 0.3 for v in closes],
        closes,
    )


def joined(first: Market, second: Market) -> Market:
    return (first[0] + second[0], first[1] + second[1], first[2] + second[2])


def widening(size: int, base: float = 200.0) -> Market:
    """Higher highs and lower lows at once, expanding hard."""
    highs, lows, closes = [], [], []
    for index in range(size):
        amplitude = 4.0 + index * 1.6
        value = base + (amplitude if index % 4 in (1, 2) else -amplitude)
        closes.append(round(value, 2))
        highs.append(round(value + amplitude * 0.5, 2))
        lows.append(round(value - amplitude * 0.5, 2))
    return highs, lows, closes


def assess(market: Market, config: StructureConfig = CONFIG) -> RegimeAssessment:
    highs, lows, closes = market
    series = ohlcv_series(highs, lows, closes, [1000.0] * len(closes))
    technicals = compute_technicals(series, TECHNICALS)
    return analyse_structure(series, technicals, config).regime


STRONG_UP = zigzag(150, 1, advance=2.0, pullback=0.8)
STRONG_DOWN = zigzag(150, -1, advance=2.0, pullback=0.8)
WEAK_UP = zigzag(150, 1, advance=1.0, pullback=1.2)
WEAK_DOWN = zigzag(150, -1, advance=1.0, pullback=1.2)
RANGE = oscillating(150, 2.0)
QUIET_AFTER_LOUD = joined(oscillating(120, 6.0), oscillating(60, 0.2))
LOUD_AFTER_QUIET = joined(oscillating(120, 0.5), oscillating(60, 8.0))
CHAOS = joined(oscillating(120, 1.0), widening(30))


def _extend(market: Market, high: float, low: float, close: float, count: int) -> Market:
    return (
        market[0] + [high] * count,
        market[1] + [low] * count,
        market[2] + [close] * count,
    )


BREAKOUT = _extend(oscillating(120, 2.0), 230.0, 220.0, 228.0, 6)
BREAKDOWN = _extend(oscillating(120, 2.0), 172.0, 162.0, 164.0, 6)


# ----------------------------------------------------------------------
# Every regime is reachable
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("market", "expected"),
    (
        (STRONG_UP, MarketRegime.STRONG_UPTREND),
        (STRONG_DOWN, MarketRegime.STRONG_DOWNTREND),
        (WEAK_UP, MarketRegime.WEAK_UPTREND),
        (WEAK_DOWN, MarketRegime.WEAK_DOWNTREND),
        (RANGE, MarketRegime.RANGE),
        (QUIET_AFTER_LOUD, MarketRegime.LOW_VOLATILITY_RANGE),
        (LOUD_AFTER_QUIET, MarketRegime.HIGH_VOLATILITY_RANGE),
        (BREAKOUT, MarketRegime.BREAKOUT),
        (BREAKDOWN, MarketRegime.BREAKDOWN),
        (CHAOS, MarketRegime.CHAOTIC),
    ),
    ids=lambda value: value.value if isinstance(value, MarketRegime) else "",
)
def test_each_regime_is_produced_by_a_market_that_deserves_it(
    market: Market, expected: MarketRegime
) -> None:
    assert assess(market).regime is expected


@pytest.mark.unit
def test_a_warming_up_series_is_uncertain_rather_than_guessed() -> None:
    assessment = assess(zigzag(30, 1, advance=2.0, pullback=0.8))
    assert assessment.regime is MarketRegime.UNCERTAIN
    assert "warming up" in assessment.reason


# ----------------------------------------------------------------------
# No single indicator decides
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_strong_trend_is_demoted_when_adx_does_not_confirm_it() -> None:
    """Structure and the EMA stack alone are not enough to say 'strong'."""
    strict = StructureConfig(
        swings=SwingConfig(left=2, right=2),
        regime=RegimeConfig(strong_adx=99.0, range_adx_max=98.0),
    )
    assert assess(STRONG_UP, strict).regime is MarketRegime.WEAK_UPTREND


@pytest.mark.unit
def test_a_bullish_ema_stack_alone_does_not_produce_an_uptrend() -> None:
    """The oscillating range drifts the EMAs bullish, but structure disagrees."""
    assessment = assess(RANGE)
    assert assessment.evidence.ema_stack is EmaStack.BULLISH
    assert assessment.evidence.structure_bias is StructureBias.AMBIGUOUS
    assert not assessment.regime.is_trending


@pytest.mark.unit
def test_chaotic_requires_expanding_structure_as_well_as_high_volatility() -> None:
    chaotic = assess(CHAOS)
    merely_loud = assess(LOUD_AFTER_QUIET)
    assert chaotic.evidence.structure_bias is StructureBias.EXPANDING
    assert merely_loud.evidence.volatility_state is VolatilityState.HIGH
    assert merely_loud.regime is MarketRegime.HIGH_VOLATILITY_RANGE


# ----------------------------------------------------------------------
# Volatility is relative to the instrument's own history
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_volatility_state_is_measured_against_the_instrument_itself() -> None:
    """No absolute 'ATR above 3%' constant, which would be instrument-specific."""
    assert assess(QUIET_AFTER_LOUD).evidence.volatility_state is VolatilityState.LOW
    assert assess(LOUD_AFTER_QUIET).evidence.volatility_state is VolatilityState.HIGH


@pytest.mark.unit
def test_the_same_shape_at_a_different_price_scale_reads_the_same() -> None:
    scaled = tuple([value * 10.0 for value in leg] for leg in STRONG_UP)
    assert assess(scaled).regime is assess(STRONG_UP).regime  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Evidence, configuration, determinism
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_every_classification_records_the_inputs_it_used() -> None:
    evidence = assess(STRONG_UP).evidence
    assert evidence.adx is not None
    assert evidence.atr is not None
    assert evidence.atr_ratio is not None
    assert evidence.atr_ratio_median is not None
    assert evidence.range_width_ratio is not None
    assert evidence.historical_volatility is not None


@pytest.mark.unit
def test_every_classification_explains_which_rule_fired() -> None:
    for market in (STRONG_UP, RANGE, CHAOS, BREAKOUT):
        assert assess(market).reason


@pytest.mark.unit
def test_the_config_used_travels_with_the_verdict() -> None:
    config = StructureConfig(
        swings=SwingConfig(left=2, right=2), regime=RegimeConfig(strong_adx=30.0)
    )
    assert assess(STRONG_UP, config).config.strong_adx == 30.0


@pytest.mark.unit
def test_breakout_recency_is_configurable() -> None:
    stale = _extend(oscillating(120, 2.0), 230.0, 220.0, 228.0, 20)
    config = StructureConfig(
        swings=SwingConfig(left=2, right=2), regime=RegimeConfig(breakout_recency=1)
    )
    assert assess(stale, config).regime is not MarketRegime.BREAKOUT


@pytest.mark.unit
def test_classification_is_reproducible() -> None:
    assert assess(STRONG_UP) == assess(STRONG_UP)


# ----------------------------------------------------------------------
# What the regime must never become
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_regime_never_becomes_a_recommendation() -> None:
    """Section 15 strategy routing belongs to a later phase and different code."""
    fields = set(type(assess(STRONG_UP)).__slots__)
    assert fields == {"regime", "evidence", "reason", "config"}
    forbidden = {"action", "signal", "recommendation", "entry", "decision", "trade"}
    assert not (fields & forbidden)


@pytest.mark.unit
def test_uncertain_and_chaotic_remain_first_class_outputs() -> None:
    assert not MarketRegime.CHAOTIC.is_trending
    assert not MarketRegime.CHAOTIC.is_ranging
    assert not MarketRegime.UNCERTAIN.is_trending
    assert not MarketRegime.UNCERTAIN.is_ranging


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    (
        {"strong_adx": 10.0, "range_adx_max": 20.0},
        {"volatility_reference_period": 1},
        {"low_volatility_multiple": 2.0, "high_volatility_multiple": 1.5},
        {"breakout_recency": 0},
    ),
)
def test_incoherent_regime_configuration_is_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        RegimeConfig(**kwargs)  # type: ignore[arg-type]


@pytest.mark.unit
def test_an_ema_period_the_snapshot_never_computed_leaves_the_stack_unknown() -> None:
    highs, lows, closes = STRONG_UP
    series = ohlcv_series(highs, lows, closes, [1000.0] * len(closes))
    technicals = compute_technicals(series, TechnicalConfig(ema_periods=(9,), sma_periods=(20,)))
    assessment = analyse_structure(series, technicals, CONFIG).regime
    assert assessment.evidence.ema_stack is EmaStack.UNKNOWN
    assert assessment.regime is MarketRegime.UNCERTAIN

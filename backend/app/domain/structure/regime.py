"""Market regime classification (master spec section 14).

The regime is a description of the market's current character. It is **not** a
trade recommendation, and nothing here outputs one - master spec section 15
routes strategies by regime, but that routing belongs to a later phase and to
different code.

Two rules shape the design.

**No single indicator decides.** Every branch below requires agreement between
at least two independent sources - confirmed structure, the EMA stack, ADX,
volatility, breakout state. An ADX above 25 on its own says a trend exists, not
which way; a bullish EMA stack on its own lags badly at turns. Letting either
one alone name the regime produces confident answers to questions the evidence
did not settle.

**Volatility is measured against the instrument's own history**, not an
absolute threshold. "ATR above 3% of price" is meaningful for one contract and
nonsense for another, and picking a number would be inventing an
instrument-specific constant. Instead the current ATR-to-price ratio is
compared with its own trailing median, which is self-normalising, needs no
exchange fact, and cannot look ahead.

``UNCERTAIN`` and ``CHAOTIC`` are first-class outputs. A market that has not
made up its mind is a real state, and the honest answer to "what regime is
this?" is sometimes "not one I can name".
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from enum import StrEnum, unique

from app.domain.common.enums import Direction
from app.domain.market.series import ValidatedCandleSeries
from app.domain.structure.breakouts import BreakoutEvent, BreakoutEventType
from app.domain.structure.market_structure import MarketStructure, StructureBias
from app.domain.technical.engine import TechnicalSnapshot
from app.domain.technical.types import IndicatorValues


@unique
class MarketRegime(StrEnum):
    """The regimes named by master spec section 14."""

    STRONG_UPTREND = "STRONG_UPTREND"
    WEAK_UPTREND = "WEAK_UPTREND"
    RANGE = "RANGE"
    LOW_VOLATILITY_RANGE = "LOW_VOLATILITY_RANGE"
    HIGH_VOLATILITY_RANGE = "HIGH_VOLATILITY_RANGE"
    WEAK_DOWNTREND = "WEAK_DOWNTREND"
    STRONG_DOWNTREND = "STRONG_DOWNTREND"
    BREAKOUT = "BREAKOUT"
    BREAKDOWN = "BREAKDOWN"
    CHAOTIC = "CHAOTIC"
    UNCERTAIN = "UNCERTAIN"

    @property
    def is_trending(self) -> bool:
        return self in (
            MarketRegime.STRONG_UPTREND,
            MarketRegime.WEAK_UPTREND,
            MarketRegime.WEAK_DOWNTREND,
            MarketRegime.STRONG_DOWNTREND,
        )

    @property
    def is_ranging(self) -> bool:
        return self in (
            MarketRegime.RANGE,
            MarketRegime.LOW_VOLATILITY_RANGE,
            MarketRegime.HIGH_VOLATILITY_RANGE,
        )


@unique
class VolatilityState(StrEnum):
    """Current volatility relative to this instrument's own recent history."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"
    """Not enough history to establish a reference."""


@unique
class EmaStack(StrEnum):
    """Ordering of the moving averages."""

    BULLISH = "BULLISH"
    """Fast above medium above slow."""

    BEARISH = "BEARISH"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"
    """One or more of the required averages is still warming up."""


@dataclass(frozen=True, slots=True)
class RegimeConfig:
    """Thresholds. Every one a documented project heuristic, not a probability.

    None of these describes VIOP, a contract or a session, so none is a section
    118 exchange fact. They are analysis parameters, and different settings
    produce a different - equally deterministic - reading of the same market.
    """

    strong_adx: float = 25.0
    """ADX at or above which a directional market counts as *strong*. Wilder's
    own rule of thumb, and the most widely used setting."""

    range_adx_max: float = 20.0
    """ADX below which there is no usable trend. The gap between this and
    ``strong_adx`` is deliberate: 20-25 is genuinely undecided, and a market
    sitting there should not be forced into either camp."""

    volatility_reference_period: int = 100
    """Candles of ATR-to-price history used as the volatility baseline."""

    high_volatility_multiple: float = 1.5
    low_volatility_multiple: float = 0.7
    """Current ATR ratio versus its trailing median."""

    breakout_recency: int = 5
    """A confirmed breakout this recently still characterises the regime."""

    ema_fast: int = 9
    ema_medium: int = 20
    ema_slow: int = 50
    """Which of the Phase 1 EMA periods form the stack. They must be present in
    the ``TechnicalConfig`` that produced the snapshot."""

    def __post_init__(self) -> None:
        if self.range_adx_max > self.strong_adx:
            raise ValueError("range_adx_max must not exceed strong_adx")
        if self.volatility_reference_period < 2:
            raise ValueError("volatility_reference_period must be >= 2")
        if not 0 < self.low_volatility_multiple < self.high_volatility_multiple:
            raise ValueError("expected 0 < low_volatility_multiple < high_volatility_multiple")
        if self.breakout_recency < 1:
            raise ValueError("breakout_recency must be >= 1")


@dataclass(frozen=True, slots=True)
class RegimeEvidence:
    """Every input the classification actually used, kept for audit.

    Master spec section 67 requires an analysis to be reconstructable. Storing
    the inputs alongside the verdict means a reader can check the reasoning
    rather than trust the label.
    """

    structure_bias: StructureBias
    ema_stack: EmaStack
    adx: float | None
    atr: float | None
    atr_ratio: float | None
    """ATR as a fraction of the closing price."""

    atr_ratio_median: float | None
    volatility_state: VolatilityState
    historical_volatility: float | None
    range_width_ratio: float | None
    """Highest high minus lowest low over the reference window, over price."""

    recent_breakout: Direction | None


@dataclass(frozen=True, slots=True)
class RegimeAssessment:
    """The regime, the evidence behind it, and which rule produced it."""

    regime: MarketRegime
    evidence: RegimeEvidence
    reason: str
    config: RegimeConfig


def classify_regime(
    series: ValidatedCandleSeries,
    technicals: TechnicalSnapshot,
    structure: MarketStructure,
    breakouts: tuple[BreakoutEvent, ...] = (),
    historical_volatility: IndicatorValues = (),
    config: RegimeConfig | None = None,
) -> RegimeAssessment:
    """Classify the regime as of the last candle of ``series``.

    Rules are evaluated in the order below; the first that matches wins, and
    the reason records which one it was.

    1. **Insufficient evidence** - ADX or the EMA stack still warming up
       -> ``UNCERTAIN``.
    2. **Recent confirmed breakout** within ``breakout_recency`` candles
       -> ``BREAKOUT`` / ``BREAKDOWN``. A market that just broke is neither
       trending nor ranging yet; it is resolving.
    3. **Strong trend** - directional structure *and* ``adx >= strong_adx``
       *and* an agreeing EMA stack -> ``STRONG_UPTREND`` / ``STRONG_DOWNTREND``.
    4. **Weak trend** - structure and EMA stack agree on direction, but ADX has
       not confirmed it -> ``WEAK_UPTREND`` / ``WEAK_DOWNTREND``.
    5. **Chaotic** - high volatility, no trend strength, and structure that is
       *expanding*: higher highs and lower lows at once. That combination is
       whipsaw, and it is worth its own name because it is the one regime where
       the correct action is usually none.
    6. **Range family** - no trend strength and non-directional structure, split
       by volatility state into ``LOW_VOLATILITY_RANGE``, ``RANGE`` or
       ``HIGH_VOLATILITY_RANGE``.
    7. Anything left -> ``UNCERTAIN``. Reaching here means the inputs disagreed
       in a way no rule claims to interpret, and saying so is the point.
    """
    settings = config if config is not None else RegimeConfig()
    evidence = _gather_evidence(
        series, technicals, structure, breakouts, historical_volatility, settings
    )

    if evidence.adx is None or evidence.ema_stack is EmaStack.UNKNOWN:
        return _assess(
            MarketRegime.UNCERTAIN,
            evidence,
            settings,
            "ADX or the EMA stack is still warming up; no regime can be read yet",
        )

    if evidence.recent_breakout is not None:
        bullish = evidence.recent_breakout is Direction.LONG
        return _assess(
            MarketRegime.BREAKOUT if bullish else MarketRegime.BREAKDOWN,
            evidence,
            settings,
            f"a confirmed {'upward' if bullish else 'downward'} breakout occurred within "
            f"the last {settings.breakout_recency} candles",
        )

    bias = evidence.structure_bias
    stack = evidence.ema_stack
    strong = evidence.adx >= settings.strong_adx

    bullish_agreement = bias is StructureBias.BULLISH and stack is EmaStack.BULLISH
    bearish_agreement = bias is StructureBias.BEARISH and stack is EmaStack.BEARISH

    if bullish_agreement and strong:
        return _assess(
            MarketRegime.STRONG_UPTREND,
            evidence,
            settings,
            f"bullish structure and a bullish EMA stack, with ADX {evidence.adx:.1f} "
            f">= {settings.strong_adx}",
        )
    if bearish_agreement and strong:
        return _assess(
            MarketRegime.STRONG_DOWNTREND,
            evidence,
            settings,
            f"bearish structure and a bearish EMA stack, with ADX {evidence.adx:.1f} "
            f">= {settings.strong_adx}",
        )
    if bullish_agreement:
        return _assess(
            MarketRegime.WEAK_UPTREND,
            evidence,
            settings,
            f"bullish structure and EMA stack, but ADX {evidence.adx:.1f} has not "
            f"reached {settings.strong_adx}",
        )
    if bearish_agreement:
        return _assess(
            MarketRegime.WEAK_DOWNTREND,
            evidence,
            settings,
            f"bearish structure and EMA stack, but ADX {evidence.adx:.1f} has not "
            f"reached {settings.strong_adx}",
        )

    trendless = evidence.adx < settings.range_adx_max

    if (
        trendless
        and evidence.volatility_state is VolatilityState.HIGH
        and bias is StructureBias.EXPANDING
    ):
        return _assess(
            MarketRegime.CHAOTIC,
            evidence,
            settings,
            f"expanding structure (higher highs and lower lows) with high volatility "
            f"and ADX {evidence.adx:.1f} below {settings.range_adx_max}",
        )

    if trendless and not bias.is_directional:
        match evidence.volatility_state:
            case VolatilityState.LOW:
                regime = MarketRegime.LOW_VOLATILITY_RANGE
            case VolatilityState.HIGH:
                regime = MarketRegime.HIGH_VOLATILITY_RANGE
            case _:
                regime = MarketRegime.RANGE
        return _assess(
            regime,
            evidence,
            settings,
            f"no directional structure ({bias.value}) and ADX {evidence.adx:.1f} below "
            f"{settings.range_adx_max}, volatility {evidence.volatility_state.value}",
        )

    return _assess(
        MarketRegime.UNCERTAIN,
        evidence,
        settings,
        f"structure ({bias.value}), EMA stack ({stack.value}) and ADX "
        f"{evidence.adx:.1f} do not agree on any regime",
    )


def _gather_evidence(
    series: ValidatedCandleSeries,
    technicals: TechnicalSnapshot,
    structure: MarketStructure,
    breakouts: tuple[BreakoutEvent, ...],
    historical_volatility: IndicatorValues,
    settings: RegimeConfig,
) -> RegimeEvidence:
    last = len(series) - 1
    close = float(series.closes[last]) if last >= 0 else None

    adx = _at(technicals.adx, last)
    atr = _at(technicals.atr, last)
    atr_ratio = atr / close if atr is not None and close else None

    ratios = _atr_ratio_history(series, technicals.atr, settings.volatility_reference_period)
    median = statistics.median(ratios) if ratios else None
    state = _volatility_state(atr_ratio, median, settings)

    return RegimeEvidence(
        structure_bias=structure.bias,
        ema_stack=_ema_stack(technicals, last, settings),
        adx=adx,
        atr=atr,
        atr_ratio=atr_ratio,
        atr_ratio_median=median,
        volatility_state=state,
        historical_volatility=_at(historical_volatility, last),
        range_width_ratio=_range_width_ratio(series, settings.volatility_reference_period),
        recent_breakout=_recent_breakout(breakouts, last, settings.breakout_recency),
    )


def _ema_stack(technicals: TechnicalSnapshot, index: int, settings: RegimeConfig) -> EmaStack:
    periods = (settings.ema_fast, settings.ema_medium, settings.ema_slow)
    values: list[float] = []
    for period in periods:
        series = technicals.ema.get(period)
        if series is None:
            return EmaStack.UNKNOWN
        value = _at(series, index)
        if value is None:
            return EmaStack.UNKNOWN
        values.append(value)

    fast, medium, slow = values
    if fast > medium > slow:
        return EmaStack.BULLISH
    if fast < medium < slow:
        return EmaStack.BEARISH
    return EmaStack.MIXED


def _atr_ratio_history(
    series: ValidatedCandleSeries, atr: IndicatorValues, period: int
) -> list[float]:
    """Trailing ATR-to-price ratios, used as the instrument's own baseline.

    Only candles at or before the last one are read, so the baseline cannot
    contain information from the future.
    """
    last = len(series) - 1
    closes = series.closes
    ratios: list[float] = []
    for index in range(max(0, last - period + 1), last + 1):
        value = _at(atr, index)
        close = float(closes[index])
        if value is not None and close > 0:
            ratios.append(value / close)
    return ratios


def _volatility_state(
    ratio: float | None, median: float | None, settings: RegimeConfig
) -> VolatilityState:
    if ratio is None or median is None or median <= 0:
        return VolatilityState.UNKNOWN
    if ratio >= median * settings.high_volatility_multiple:
        return VolatilityState.HIGH
    if ratio <= median * settings.low_volatility_multiple:
        return VolatilityState.LOW
    return VolatilityState.NORMAL


def _range_width_ratio(series: ValidatedCandleSeries, period: int) -> float | None:
    last = len(series) - 1
    if last < 0:
        return None
    start = max(0, last - period + 1)
    highest = max(series.highs[start : last + 1])
    lowest = min(series.lows[start : last + 1])
    close = float(series.closes[last])
    if close <= 0:
        return None
    return float(highest - lowest) / close


def _recent_breakout(
    breakouts: tuple[BreakoutEvent, ...], index: int, recency: int
) -> Direction | None:
    latest: BreakoutEvent | None = None
    for event in breakouts:
        if event.event_type is not BreakoutEventType.CONFIRMED:
            continue
        if event.confirmed_index > index or index - event.confirmed_index >= recency:
            continue
        if latest is None or event.confirmed_index > latest.confirmed_index:
            latest = event
    return latest.direction if latest is not None else None


def _at(values: IndicatorValues, index: int) -> float | None:
    if index < 0 or index >= len(values):
        return None
    return values[index]


def _assess(
    regime: MarketRegime,
    evidence: RegimeEvidence,
    settings: RegimeConfig,
    reason: str,
) -> RegimeAssessment:
    return RegimeAssessment(regime=regime, evidence=evidence, reason=reason, config=settings)

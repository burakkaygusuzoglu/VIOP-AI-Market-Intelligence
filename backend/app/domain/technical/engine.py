"""Batch computation of the Phase 1 indicator set.

This is the reusable primitive master spec section 74 demands: live, replay,
backtest and shadow mode all call *this*, and only the provider that supplied
the candles differs. Nothing here interprets a number - no trend label, no
score, no signal. Classification is the market structure and regime work of
Phase 2 and is deliberately absent.

The function takes a ``ValidatedCandleSeries``, so it is impossible to compute
indicators over data the Data Quality Engine has not cleared.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from app.domain.market.series import ValidatedCandleSeries
from app.domain.technical.momentum import MacdResult, macd, rsi
from app.domain.technical.smoothing import ema, sma
from app.domain.technical.trend_strength import DirectionalMovement, adx
from app.domain.technical.types import IndicatorValues
from app.domain.technical.volatility import BollingerBands, atr, bollinger_bands, true_range
from app.domain.technical.volume import (
    relative_volume,
    volume_acceleration,
    volume_moving_average,
)
from app.domain.technical.vwap import daily_anchors, vwap


@unique
class VwapAnchoring(StrEnum):
    """Where the VWAP accumulation restarts."""

    DAILY = "DAILY"
    """Restart on each UTC calendar date. A development default until VIOP
    session hours are verified - see ``vwap.daily_anchors``."""

    WHOLE_SERIES = "WHOLE_SERIES"
    """One accumulation across every candle supplied."""


@dataclass(frozen=True, slots=True)
class TechnicalConfig:
    """Periods for the Phase 1 indicator set.

    Defaults are the values named in master spec section 11. They live in one
    frozen object so that a backtest, a replay and a live run can be proven to
    have used identical settings rather than each hard-coding its own.
    """

    ema_periods: tuple[int, ...] = (9, 20, 50, 200)
    sma_periods: tuple[int, ...] = (20, 50, 200)
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    atr_period: int = 14
    adx_period: int = 14
    bollinger_period: int = 20
    bollinger_multiplier: float = 2.0
    volume_period: int = 20
    vwap_anchoring: VwapAnchoring = VwapAnchoring.DAILY


@dataclass(frozen=True, slots=True)
class TechnicalSnapshot:
    """Every Phase 1 indicator, aligned candle-for-candle with its source.

    Purely numerical. Any reading of these numbers belongs to a later phase.
    """

    symbol: str
    config: TechnicalConfig
    candle_count: int

    ema: dict[int, IndicatorValues]
    sma: dict[int, IndicatorValues]
    rsi: IndicatorValues
    macd: MacdResult
    true_range: IndicatorValues
    atr: IndicatorValues
    bollinger: BollingerBands
    directional: DirectionalMovement
    vwap: IndicatorValues
    volume_ma: IndicatorValues
    relative_volume: IndicatorValues
    volume_acceleration: IndicatorValues

    @property
    def adx(self) -> IndicatorValues:
        """Convenience accessor for the most-referenced directional value."""
        return self.directional.adx


def compute_technicals(
    series: ValidatedCandleSeries,
    config: TechnicalConfig | None = None,
) -> TechnicalSnapshot:
    """Compute the whole Phase 1 indicator set over a validated series.

    Every value at index ``i`` derives from candles ``0..i`` only. The
    conversion from ``Decimal`` to ``float`` happens here, once, through the
    series' float accessors.
    """
    settings = config if config is not None else TechnicalConfig()

    highs = series.float_highs()
    lows = series.float_lows()
    closes = series.float_closes()
    volumes = series.float_volumes()

    anchors = (
        daily_anchors(series.open_times) if settings.vwap_anchoring is VwapAnchoring.DAILY else (0,)
    )

    return TechnicalSnapshot(
        symbol=series.symbol,
        config=settings,
        candle_count=len(series),
        ema={period: ema(closes, period) for period in settings.ema_periods},
        sma={period: sma(closes, period) for period in settings.sma_periods},
        rsi=rsi(closes, settings.rsi_period),
        macd=macd(closes, settings.macd_fast, settings.macd_slow, settings.macd_signal),
        true_range=true_range(highs, lows, closes),
        atr=atr(highs, lows, closes, settings.atr_period),
        bollinger=bollinger_bands(closes, settings.bollinger_period, settings.bollinger_multiplier),
        directional=adx(highs, lows, closes, settings.adx_period),
        vwap=vwap(highs, lows, closes, volumes, anchors) if len(series) else (),
        volume_ma=volume_moving_average(volumes, settings.volume_period),
        relative_volume=relative_volume(volumes, settings.volume_period),
        volume_acceleration=volume_acceleration(volumes, settings.volume_period),
    )

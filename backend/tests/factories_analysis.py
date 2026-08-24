"""Timeframe views for Phase 4A tests.

Every series here is TEST_FIXTURE data: synthetic shapes chosen to make one
structural reading unambiguous, never a recording of any market.

The markets are built as zigzags rather than straight ramps, because a
monotonic ramp has no pivots at all - it produces no swings, so no structure,
no zones and no regime worth the name. A zigzag with a net drift gives the
Phase 2 engines something real to label.
"""

from __future__ import annotations

from app.domain.analysis.timeframes import TimeframeRole, TimeframeRolePolicy, TimeframeView
from app.domain.common.enums import Timeframe
from app.domain.market.series import ValidatedCandleSeries
from app.domain.structure.engine import StructureConfig, analyse_structure
from app.domain.structure.swings import SwingConfig
from app.domain.technical.engine import TechnicalConfig, compute_technicals
from tests.factories import FIXTURE_SYMBOL, ohlcv_series

STRUCTURE = StructureConfig(swings=SwingConfig(left=2, right=2))
TECHNICALS = TechnicalConfig(ema_periods=(9, 20, 50), sma_periods=(20,))
POLICY = TimeframeRolePolicy()


def zigzag(
    *,
    drift: float,
    size: int = 140,
    start: float = 200.0,
    timeframe: Timeframe = Timeframe.M15,
    symbol: str = FIXTURE_SYMBOL,
) -> ValidatedCandleSeries:
    """A market that swings while drifting.

    ``drift`` is the net move per four-candle cycle: positive trends up,
    negative trends down, zero ranges. The swing amplitude is fixed, so the
    same shape produces comparable structure at every timeframe.
    """
    closes: list[float] = []
    price = start
    for index in range(size):
        price += 3.0 if index % 4 in (0, 1) else -3.0
        price += drift / 4.0
        closes.append(round(price, 2))
    highs = [value + 1.0 for value in closes]
    lows = [value - 1.0 for value in closes]
    volumes = [float(700 + (index * 53) % 800) for index in range(size)]
    return ohlcv_series(highs, lows, closes, volumes, timeframe=timeframe, symbol=symbol)


def retracement(
    *,
    drift: float = 3.0,
    turn: int = 20,
    size: int = 140,
    start: float = 200.0,
    timeframe: Timeframe = Timeframe.M5,
    symbol: str = FIXTURE_SYMBOL,
) -> ValidatedCandleSeries:
    """A market that trended one way and has been correcting for ``turn`` candles.

    The realistic shape of a retracement, and the one the pullback exemption is
    about: a bearish *structure* on the entry timeframe without a bearish trend
    regime behind it.
    """
    closes: list[float] = []
    price = start
    for index in range(size):
        price += 3.0 if index % 4 in (0, 1) else -3.0
        price += (drift / 4.0) if index < size - turn else (-drift / 4.0)
        closes.append(round(price, 2))
    highs = [value + 1.0 for value in closes]
    lows = [value - 1.0 for value in closes]
    volumes = [float(700 + (index * 53) % 800) for index in range(size)]
    return ohlcv_series(highs, lows, closes, volumes, timeframe=timeframe, symbol=symbol)


def view(
    role: TimeframeRole,
    series: ValidatedCandleSeries,
    *,
    structure: StructureConfig = STRUCTURE,
    technicals: TechnicalConfig = TECHNICALS,
) -> TimeframeView:
    """Run the Phase 1-3 engines and bind the result to a role."""
    computed = compute_technicals(series, technicals)
    return TimeframeView(
        role=role,
        series=series,
        technicals=computed,
        structure=analyse_structure(series, computed, structure),
    )


def market_view(
    role: TimeframeRole,
    *,
    drift: float,
    size: int = 140,
    policy: TimeframeRolePolicy = POLICY,
    symbol: str = FIXTURE_SYMBOL,
) -> TimeframeView:
    """A view for ``role``, on the timeframe that role expects."""
    return view(
        role,
        zigzag(drift=drift, size=size, timeframe=policy.timeframe_for(role), symbol=symbol),
    )


BULLISH_DRIFT = 3.0
BEARISH_DRIFT = -3.0
FLAT_DRIFT = 0.0

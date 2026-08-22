"""The assembled Phase 2 view: composition, reuse and phase boundaries."""

from __future__ import annotations

import pytest

from app.domain.common.enums import Timeframe
from app.domain.market.series import ValidatedCandleSeries
from app.domain.structure.engine import (
    StructureConfig,
    StructureSnapshot,
    analyse_structure,
)
from app.domain.structure.swings import SwingConfig
from app.domain.technical.engine import TechnicalConfig, compute_technicals
from tests.factories import FIXTURE_SYMBOL, ohlcv_series

CONFIG = StructureConfig(swings=SwingConfig(left=2, right=2))
TECHNICALS = TechnicalConfig(ema_periods=(9, 20, 50), sma_periods=(20,))


def market(size: int = 160) -> ValidatedCandleSeries:
    """A trend that later ranges.

    The ranging half matters: a market that only ever makes new highs never
    revisits a level, so it legitimately produces no repeated-touch zones. To
    exercise the zone, breakout and retest engines the price has to come back
    to somewhere it has been.
    """
    closes: list[float] = []
    price = 200.0
    for index in range(size):
        if index < size // 2:
            price += 1.4 if index % 6 not in (4, 5) else -1.1
        else:
            price += 3.0 if index % 4 in (1, 2) else -3.0
        closes.append(round(price, 2))
    highs = [value + 1.0 for value in closes]
    lows = [value - 1.0 for value in closes]
    volumes = [float(700 + (index * 53) % 800) for index in range(size)]
    return ohlcv_series(highs, lows, closes, volumes)


def analyse(series: ValidatedCandleSeries, config: StructureConfig = CONFIG) -> StructureSnapshot:
    return analyse_structure(series, compute_technicals(series, TECHNICALS), config)


@pytest.mark.unit
def test_the_snapshot_carries_every_phase_two_engine() -> None:
    snapshot = analyse(market())
    assert snapshot.swings
    assert snapshot.structure.labelled
    assert snapshot.structural_events
    assert snapshot.support_zones or snapshot.resistance_zones
    assert snapshot.regime is not None
    assert snapshot.historical_volatility


@pytest.mark.unit
def test_mismatched_technicals_are_refused_rather_than_misaligned() -> None:
    """Indicators are read positionally; a mismatch would silently misalign."""
    series = market(120)
    other = compute_technicals(market(80), TECHNICALS)
    with pytest.raises(ValueError, match="same candles"):
        analyse_structure(series, other, CONFIG)


@pytest.mark.unit
def test_the_engine_recomputes_no_phase_one_formula() -> None:
    """Reuse, not reimplementation: one ATR in the codebase, one volume average."""
    import app.domain.structure.engine as engine_module
    import app.domain.structure.regime as regime_module
    import app.domain.structure.zones as zones_module

    source = "".join(
        open(module.__file__, encoding="utf-8").read()  # noqa: SIM115, PTH123
        for module in (engine_module, regime_module, zones_module)
        if module.__file__
    )
    for formula in ("def atr(", "def ema(", "def sma(", "def wilder(", "def rsi("):
        assert formula not in source


@pytest.mark.unit
def test_the_config_used_travels_with_the_snapshot() -> None:
    config = StructureConfig(swings=SwingConfig(left=3, right=3))
    snapshot = analyse(market(), config)
    assert snapshot.config is config
    assert all(swing.confirmation_lag == 3 for swing in snapshot.swings)


@pytest.mark.unit
def test_an_empty_series_produces_an_empty_view_rather_than_failing() -> None:
    empty = ValidatedCandleSeries(candles=(), symbol=FIXTURE_SYMBOL, timeframe=Timeframe.M15)
    snapshot = analyse_structure(empty, compute_technicals(empty, TECHNICALS), CONFIG)
    assert snapshot.candle_count == 0
    assert snapshot.swings == ()
    assert snapshot.structural_events == ()
    assert snapshot.support_zones == () and snapshot.resistance_zones == ()


@pytest.mark.unit
def test_a_series_too_short_for_structure_reports_nothing_invented() -> None:
    """Eight candles can confirm one pivot and see it broken - and no more.

    A break of a single swing is a real observation, so it is reported. What
    must not appear is anything needing repeated touches or a warmed-up
    indicator: no zones, no breakout lifecycle, no retests, and a regime that
    admits it does not know.
    """
    from app.domain.structure.market_structure import StructureBias
    from app.domain.structure.regime import MarketRegime

    snapshot = analyse(market(8))
    assert snapshot.support_zones == () and snapshot.resistance_zones == ()
    assert snapshot.breakout_events == ()
    assert snapshot.retest_events == ()
    assert snapshot.divergences == ()
    assert snapshot.structure.bias is StructureBias.INSUFFICIENT
    assert snapshot.regime.regime is MarketRegime.UNCERTAIN


@pytest.mark.unit
def test_the_combined_zone_view_is_ordered_by_price() -> None:
    snapshot = analyse(market())
    lows = [zone.low for zone in snapshot.zones]
    assert lows == sorted(lows)


@pytest.mark.unit
def test_analysis_is_reproducible() -> None:
    series = market()
    assert analyse(series) == analyse(series)


@pytest.mark.unit
def test_the_snapshot_carries_no_trade_decision() -> None:
    """Phase 2 describes structure. Setups, scores and directions are later.

    A field named for an action here would mean the phase boundary had leaked.
    """
    from app.domain.structure.engine import StructureSnapshot

    fields = set(StructureSnapshot.__slots__)
    forbidden = {
        "signal",
        "setup",
        "score",
        "recommendation",
        "decision",
        "entry",
        "stop",
        "target",
        "position_size",
        "risk",
    }
    assert not (fields & forbidden)


@pytest.mark.unit
def test_phase_three_concepts_are_absent_from_the_package() -> None:
    """No contract metadata, margin, sizing or P&L reached Phase 2."""
    from pathlib import Path

    package = Path(__file__).resolve().parents[3] / "app" / "domain" / "structure"
    forbidden = (
        "multiplier",
        "tick_size",
        "margin",
        "position_size",
        "ContractMetadata",
        "OrderExecution",
    )
    for path in package.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in source, f"{path.name} mentions {needle}"

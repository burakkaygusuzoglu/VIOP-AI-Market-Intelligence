"""Assembles the Phase 2 structural view from a validated series.

The one entry point a caller needs. It takes candles the Data Quality Engine
cleared and the Phase 1 indicators computed from them, and returns every
structural fact the phase can establish.

It calculates no indicator of its own. ATR, ADX, EMA and relative volume all
arrive from ``TechnicalSnapshot``; historical volatility comes from the Phase 1
volatility module. There is exactly one implementation of each formula in the
codebase, and this is not it.

Nothing here interprets a regime into an action. Section 15 strategy routing,
setup scoring and any notion of LONG or SHORT belong to later phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.domain.market.series import ValidatedCandleSeries
from app.domain.structure.breakouts import (
    BreakoutConfig,
    BreakoutEvent,
    RetestEvent,
    detect_breakouts,
    detect_retests,
)
from app.domain.structure.divergence import (
    DivergenceConfig,
    DivergenceEvent,
    detect_volume_divergence,
)
from app.domain.structure.events import (
    StructuralEvent,
    StructuralEventConfig,
    detect_structural_events,
)
from app.domain.structure.market_structure import (
    MarketStructure,
    StructureLabelConfig,
    label_swings,
)
from app.domain.structure.regime import RegimeAssessment, RegimeConfig, classify_regime
from app.domain.structure.swings import SwingConfig, SwingPoint, detect_swings
from app.domain.structure.zones import Zone, ZoneConfig, build_zones
from app.domain.technical.engine import TechnicalSnapshot
from app.domain.technical.types import IndicatorValues
from app.domain.technical.volatility import historical_volatility


@dataclass(frozen=True, slots=True)
class StructureConfig:
    """Every Phase 2 setting in one frozen object.

    Held together so a replay, a backtest and a live run can be shown to have
    used identical structural rules - the same reason ``TechnicalConfig``
    exists in Phase 1.
    """

    swings: SwingConfig = SwingConfig()
    labels: StructureLabelConfig = StructureLabelConfig()
    events: StructuralEventConfig = field(default_factory=StructuralEventConfig)
    zones: ZoneConfig = field(default_factory=ZoneConfig)
    breakouts: BreakoutConfig = BreakoutConfig()
    divergence: DivergenceConfig = field(default_factory=DivergenceConfig)
    regime: RegimeConfig = RegimeConfig()
    historical_volatility_period: int = 20


@dataclass(frozen=True, slots=True)
class StructureSnapshot:
    """The complete structural picture as of the last candle supplied.

    Historical items - swings, structural events, breakouts, retests,
    divergences - each carry their own ``confirmed_index``, so a reader can
    reconstruct what was knowable at any earlier candle. Point-in-time items -
    the structure labels, the zones, the regime - describe the final candle
    only; to see them at an earlier point, analyse a shorter series.
    """

    symbol: str
    config: StructureConfig
    candle_count: int

    swings: tuple[SwingPoint, ...]
    structure: MarketStructure
    structural_events: tuple[StructuralEvent, ...]
    support_zones: tuple[Zone, ...]
    resistance_zones: tuple[Zone, ...]
    breakout_events: tuple[BreakoutEvent, ...]
    retest_events: tuple[RetestEvent, ...]
    divergences: tuple[DivergenceEvent, ...]
    historical_volatility: IndicatorValues
    regime: RegimeAssessment

    @property
    def zones(self) -> tuple[Zone, ...]:
        """Support and resistance together, ordered by price."""
        return tuple(sorted(self.support_zones + self.resistance_zones, key=lambda z: z.low))


def analyse_structure(
    series: ValidatedCandleSeries,
    technicals: TechnicalSnapshot,
    config: StructureConfig | None = None,
) -> StructureSnapshot:
    """Build the structural view of ``series``.

    ``technicals`` must have been computed from the *same* series; the
    indicators are read positionally, so a mismatch would silently misalign
    every volume and volatility reading. ``analyse_structure`` checks the
    lengths and refuses rather than producing a plausible wrong answer.

    The order of work is the dependency order, and each stage consumes only
    confirmed output of the one before it: swings, then labels, then structural
    events, then zones, then breakouts, then retests, then divergence, and
    finally the regime, which sees all of it.
    """
    settings = config if config is not None else StructureConfig()

    if technicals.candle_count != len(series):
        raise ValueError(
            f"technicals describe {technicals.candle_count} candles but the series has "
            f"{len(series)}; they must be computed from the same candles"
        )

    swings = detect_swings(series, settings.swings)
    structure = label_swings(swings, settings.labels)
    structural_events = detect_structural_events(series, swings, settings.events)

    support_zones, resistance_zones = build_zones(
        series,
        swings,
        technicals.atr,
        technicals.relative_volume,
        settings.zones,
    )

    breakout_events = detect_breakouts(
        series,
        _historical_zones(series, swings, technicals, settings),
        technicals.relative_volume,
        settings.breakouts,
    )
    retest_events = detect_retests(series, breakout_events, settings.breakouts)
    divergences = detect_volume_divergence(swings, technicals.volume_ma, settings.divergence)

    volatility = historical_volatility(series.float_closes(), settings.historical_volatility_period)
    regime = classify_regime(
        series,
        technicals,
        structure,
        breakout_events,
        volatility,
        settings.regime,
    )

    return StructureSnapshot(
        symbol=series.symbol,
        config=settings,
        candle_count=len(series),
        swings=swings,
        structure=structure,
        structural_events=structural_events,
        support_zones=support_zones,
        resistance_zones=resistance_zones,
        breakout_events=breakout_events,
        retest_events=retest_events,
        divergences=divergences,
        historical_volatility=volatility,
        regime=regime,
    )


def _historical_zones(
    series: ValidatedCandleSeries,
    swings: tuple[SwingPoint, ...],
    technicals: TechnicalSnapshot,
    settings: StructureConfig,
) -> tuple[Zone, ...]:
    """Every zone as it looked when it first became knowable.

    ``support_zones`` and ``resistance_zones`` on the snapshot answer "where are
    the levels *now*", computed as of the final candle. That view is the wrong
    input for scanning history: a breach at candle 40 must be measured against
    the band that existed at candle 40, not against one whose edges were fixed
    by swings that confirmed at candle 200.

    So the zone set is rebuilt at each candle where a swing confirmed - the only
    moments a zone can change - and each distinct band is kept the first time it
    appears, with the boundaries it had then. A cluster that later gains a third
    touch becomes a *different*, wider band born at that later candle; both are
    scanned, each only from the point it existed. Nothing is revised in place.

    Rebuilding only at confirmations rather than every candle keeps this
    proportional to the number of swings instead of the number of candles, and
    loses nothing: between two confirmations no new structural information has
    arrived.

    Each surviving band is stamped with **the checkpoint that produced it**,
    not with the confirmation of its newest touch. Those usually coincide, but
    not always: the clustering tolerance is a multiple of ATR, so a later
    checkpoint with a different ATR can group swings that all confirmed long
    ago into a band that had never existed before. Dating that band by its
    touches would claim it was available months of candles earlier than it
    was - which is exactly the backdating this phase is built to prevent, and
    is what ``test_breakout_events_are_not_backdated`` caught.
    """
    checkpoints = sorted({swing.confirmed_index for swing in swings})
    seen: set[tuple[str, str, str]] = set()
    historical: list[Zone] = []

    for checkpoint in checkpoints:
        support, resistance = build_zones(
            series,
            swings,
            technicals.atr,
            technicals.relative_volume,
            settings.zones,
            as_of=checkpoint,
        )
        for zone in support + resistance:
            key = (zone.kind.value, str(zone.low), str(zone.high))
            if key not in seen:
                seen.add(key)
                historical.append(replace(zone, confirmed_index=checkpoint))

    return tuple(historical)

"""Turning Phase 1-3 output into evidence (master spec §16).

Every item produced here is a **translation**, not a calculation. The EMA
stack, the structure bias, the regime, the breakouts, the retests, the
divergences, the zones, the basis and the open-interest reading all arrive
already computed by the engines that own those formulas. This module maps them
into the evidence vocabulary and dates each one to the moment it became
knowable.

Nothing is invented. If an engine reported that it could not measure something,
the evidence says `UNAVAILABLE`; there is no branch anywhere below that
substitutes a plausible value for an absent one.

**Direction is only claimed where the owning engine claims one.** Basis (§32)
and open interest (§33) both state that their readings are context and not
direction, and Phases 3 tests enforce that. Their evidence is therefore
`NEUTRAL` with the context in the reason - promoting "PREMIUM" to "BULLISH"
here would smuggle in a directional claim two earlier phases explicitly refused
to make.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.domain.analysis.evidence import (
    EvidenceCategory,
    EvidenceDirection,
    EvidenceItem,
    EvidenceSource,
    EvidenceStrength,
)
from app.domain.analysis.timeframes import TimeframeView
from app.domain.common.enums import Direction
from app.domain.common.verification import VerificationStatus
from app.domain.futures.basis import BasisContext, BasisResult
from app.domain.futures.open_interest import OpenInterestContext, OpenInterestReading
from app.domain.structure.breakouts import (
    BreakoutEvent,
    BreakoutEventType,
    RetestEvent,
    RetestEventType,
    VolumeConfirmation,
)
from app.domain.structure.divergence import DivergenceEvent, DivergenceType
from app.domain.structure.events import StructuralEvent, StructuralEventType
from app.domain.structure.market_structure import StructureBias
from app.domain.structure.regime import EmaStack, MarketRegime, RegimeAssessment
from app.domain.structure.zones import Zone, ZoneKind
from app.domain.technical.types import IndicatorValues


@dataclass(frozen=True, slots=True)
class EvidenceConfig:
    """The project heuristics used when translating engine output.

    These are **project conventions, not exchange facts and not
    probabilities**. Every one is explicit, configurable and deterministic, and
    each is named after what it decides.
    """

    level_proximity_atr: float = 1.0
    """How close, in ATR, a zone must sit to the last close before it counts
    as evidence at all. Beyond this it is a level on the chart, not a fact
    about the current candle."""

    strong_zone_strength: float = 0.66
    """Zone score at or above which the level evidence is STRONG."""

    moderate_zone_strength: float = 0.33
    """Zone score at or above which the level evidence is MODERATE."""

    rsi_bullish: float = 55.0
    rsi_bearish: float = 45.0
    """The band outside which RSI is read as leaning. Between them momentum is
    reported NEUTRAL rather than rounded to the nearer side. The default is
    deliberately wider than the 50 midpoint: a reading of 50.4 is not a bullish
    fact."""

    rsi_stretched: float = 70.0
    rsi_depressed: float = 30.0
    """Beyond these the reading is graded STRONG. §11 names 70/30 as the
    conventional band; it is used here only to grade an existing reading, and
    is never turned into a reversal call."""


def build_timeframe_evidence(
    view: TimeframeView,
    config: EvidenceConfig | None = None,
) -> tuple[EvidenceItem, ...]:
    """Every observation the Phase 1-3 engines support for one timeframe.

    Deterministic in content and in order: the builders run in a fixed
    sequence and each preserves the order of the events it reads.
    """
    settings = config if config is not None else EvidenceConfig()
    items: list[EvidenceItem] = []

    items.extend(_ema_alignment(view))
    items.extend(_market_structure(view))
    items.extend(_regime(view))
    items.extend(_momentum(view, settings))
    items.extend(_vwap_relationship(view))
    items.extend(_structural_events(view))
    items.extend(_breakouts(view))
    items.extend(_retests(view))
    items.extend(_divergences(view))
    items.extend(_levels(view, settings))

    return tuple(items)


def build_contract_evidence(
    basis: BasisResult | None = None,
    open_interest: OpenInterestReading | None = None,
) -> tuple[EvidenceItem, ...]:
    """Contract context that belongs to no timeframe.

    Both readings are `NEUTRAL` by construction - see the module docstring.
    """
    items: list[EvidenceItem] = []
    if basis is not None:
        items.append(_basis_item(basis))
    if open_interest is not None:
        items.append(_open_interest_item(open_interest))
    return tuple(items)


# ----------------------------------------------------------------------
# Point-in-time observations: they describe the final candle only
# ----------------------------------------------------------------------


def _point_in_time(
    view: TimeframeView,
    source: EvidenceSource,
    category: EvidenceCategory,
    direction: EvidenceDirection,
    strength: EvidenceStrength,
    reason: str,
) -> EvidenceItem:
    return EvidenceItem(
        source=source,
        category=category,
        direction=direction,
        strength=strength,
        reason=reason,
        timeframe=view.timeframe,
        role=view.role,
        confirmed_index=view.last_index,
        confirmed_time=view.last_time,
        point_in_time=True,
    )


def _ema_alignment(view: TimeframeView) -> tuple[EvidenceItem, ...]:
    """Read straight off the Phase 2 regime evidence.

    The stack was computed once, by the regime engine, from Phase 1 EMAs. It
    is not recomputed here, and the ADX threshold that grades the strength is
    the regime engine's own ``strong_adx`` rather than a second, competing
    number invented for Phase 4.
    """
    regime: RegimeAssessment = view.structure.regime
    stack = regime.evidence.ema_stack
    adx = regime.evidence.adx

    if stack is EmaStack.UNKNOWN:
        return (
            _point_in_time(
                view,
                EvidenceSource.EMA_ALIGNMENT,
                EvidenceCategory.TREND,
                EvidenceDirection.UNAVAILABLE,
                EvidenceStrength.WEAK,
                "EMA alignment is not computable yet: the moving averages are still in warm-up",
            ),
        )

    if stack is EmaStack.MIXED:
        return (
            _point_in_time(
                view,
                EvidenceSource.EMA_ALIGNMENT,
                EvidenceCategory.TREND,
                EvidenceDirection.NEUTRAL,
                EvidenceStrength.WEAK,
                "the EMAs are interleaved rather than stacked, so they favour neither side",
            ),
        )

    direction = (
        EvidenceDirection.BULLISH if stack is EmaStack.BULLISH else EvidenceDirection.BEARISH
    )
    if adx is None:
        strength = EvidenceStrength.WEAK
        qualifier = "trend strength is not yet measurable"
    elif adx >= regime.config.strong_adx:
        strength = EvidenceStrength.STRONG
        qualifier = f"ADX {adx:.1f} is at or above the {regime.config.strong_adx:.1f} threshold"
    else:
        strength = EvidenceStrength.MODERATE
        qualifier = f"ADX {adx:.1f} is below the {regime.config.strong_adx:.1f} threshold"

    return (
        _point_in_time(
            view,
            EvidenceSource.EMA_ALIGNMENT,
            EvidenceCategory.TREND,
            direction,
            strength,
            f"the EMAs are stacked {stack.value.lower()}; {qualifier}",
        ),
    )


def _momentum(view: TimeframeView, config: EvidenceConfig) -> tuple[EvidenceItem, ...]:
    """RSI and the MACD histogram, read off the finished Phase 1 snapshot.

    Neither is recomputed and neither is turned into a reversal call: an RSI of
    75 is reported as stretched momentum in the direction it is stretched, not
    as "due to fall", which would be a prediction this project does not make.

    The two must agree to produce a directional reading. Where they disagree
    the item is NEUTRAL and says which way each pointed - the same refusal to
    cast a deciding vote that the timeframe readings use.
    """
    index = view.last_index
    rsi = _at(view.technicals.rsi, index)
    histogram = _at(view.technicals.macd.histogram, index)

    if rsi is None and histogram is None:
        return (
            _point_in_time(
                view,
                EvidenceSource.MOMENTUM,
                EvidenceCategory.MOMENTUM,
                EvidenceDirection.UNAVAILABLE,
                EvidenceStrength.WEAK,
                "neither RSI nor MACD has finished warming up",
            ),
        )

    rsi_direction = EvidenceDirection.UNAVAILABLE
    if rsi is not None:
        if rsi >= config.rsi_bullish:
            rsi_direction = EvidenceDirection.BULLISH
        elif rsi <= config.rsi_bearish:
            rsi_direction = EvidenceDirection.BEARISH
        else:
            rsi_direction = EvidenceDirection.NEUTRAL

    macd_direction = EvidenceDirection.UNAVAILABLE
    if histogram is not None:
        if histogram > 0:
            macd_direction = EvidenceDirection.BULLISH
        elif histogram < 0:
            macd_direction = EvidenceDirection.BEARISH
        else:
            macd_direction = EvidenceDirection.NEUTRAL

    detail = (f"RSI {rsi:.1f}" if rsi is not None else "RSI unavailable") + (
        f", MACD histogram {histogram:+.4f}" if histogram is not None else ", MACD unavailable"
    )

    if rsi_direction.is_directional and macd_direction.is_directional:
        if rsi_direction is macd_direction:
            stretched = rsi is not None and (
                rsi >= config.rsi_stretched or rsi <= config.rsi_depressed
            )
            return (
                _point_in_time(
                    view,
                    EvidenceSource.MOMENTUM,
                    EvidenceCategory.MOMENTUM,
                    rsi_direction,
                    EvidenceStrength.STRONG if stretched else EvidenceStrength.MODERATE,
                    f"RSI and MACD both lean {rsi_direction.value.lower()} ({detail})",
                ),
            )
        return (
            _point_in_time(
                view,
                EvidenceSource.MOMENTUM,
                EvidenceCategory.MOMENTUM,
                EvidenceDirection.NEUTRAL,
                EvidenceStrength.WEAK,
                f"RSI and MACD disagree, so neither casts a deciding vote ({detail})",
            ),
        )

    single = rsi_direction if rsi_direction.is_directional else macd_direction
    if single.is_directional:
        return (
            _point_in_time(
                view,
                EvidenceSource.MOMENTUM,
                EvidenceCategory.MOMENTUM,
                single,
                EvidenceStrength.WEAK,
                f"only one momentum reading leans {single.value.lower()} ({detail})",
            ),
        )

    return (
        _point_in_time(
            view,
            EvidenceSource.MOMENTUM,
            EvidenceCategory.MOMENTUM,
            EvidenceDirection.NEUTRAL,
            EvidenceStrength.WEAK,
            f"momentum sits mid-range ({detail})",
        ),
    )


def _vwap_relationship(view: TimeframeView) -> tuple[EvidenceItem, ...]:
    """Where the close sits relative to VWAP (§11 intraday context).

    Named for the *relationship*, not for the indicator: VWAP itself is
    computed once, by Phase 1, and is only read here.

    Carries `DEVELOPMENT_DEFAULT` provenance deliberately. Phase 1 anchors
    VWAP to the UTC calendar day because verified Borsa Istanbul session hours
    are a §118 fact this project does not hold; the anchor is therefore a
    development default, and every reading built on it inherits that. The
    provenance makes the item report `UNVERIFIED_SOURCE` reliability, so a
    consumer can see the caveat rather than having to remember it.
    """
    index = view.last_index
    vwap = _at(view.technicals.vwap, index)
    if vwap is None:
        return (
            _point_in_time(
                view,
                EvidenceSource.VWAP,
                EvidenceCategory.INTRADAY,
                EvidenceDirection.UNAVAILABLE,
                EvidenceStrength.WEAK,
                "VWAP is not computable for this candle",
            ),
        )

    close = float(view.last_close)
    if close > vwap:
        direction = EvidenceDirection.BULLISH
        relation = "above"
    elif close < vwap:
        direction = EvidenceDirection.BEARISH
        relation = "below"
    else:
        direction = EvidenceDirection.NEUTRAL
        relation = "exactly at"

    return (
        EvidenceItem(
            source=EvidenceSource.VWAP,
            category=EvidenceCategory.INTRADAY,
            direction=direction,
            strength=EvidenceStrength.WEAK,
            reason=(
                f"the close {view.last_close} is {relation} VWAP {vwap:.4f}, which is anchored "
                "to the UTC calendar day because verified session hours are unavailable"
            ),
            timeframe=view.timeframe,
            role=view.role,
            confirmed_index=index,
            confirmed_time=view.last_time,
            point_in_time=True,
            provenance=VerificationStatus.DEVELOPMENT_DEFAULT,
        ),
    )


def _at(values: IndicatorValues, index: int) -> float | None:
    """One indicator reading, or ``None`` when the series does not reach it."""
    if index < 0 or index >= len(values):
        return None
    return values[index]


_BIAS_DIRECTION: dict[StructureBias, EvidenceDirection] = {
    StructureBias.BULLISH: EvidenceDirection.BULLISH,
    StructureBias.BEARISH: EvidenceDirection.BEARISH,
    StructureBias.CONTRACTING: EvidenceDirection.NEUTRAL,
    StructureBias.EXPANDING: EvidenceDirection.NEUTRAL,
    StructureBias.AMBIGUOUS: EvidenceDirection.NEUTRAL,
    StructureBias.INSUFFICIENT: EvidenceDirection.UNAVAILABLE,
}


def _market_structure(view: TimeframeView) -> tuple[EvidenceItem, ...]:
    structure = view.structure.structure
    direction = _BIAS_DIRECTION[structure.bias]

    if direction is EvidenceDirection.UNAVAILABLE:
        strength = EvidenceStrength.WEAK
        reason = "not enough confirmed swings to establish a structural bias"
    elif direction.is_directional and structure.alternates:
        strength = EvidenceStrength.STRONG
        reason = f"swings alternate cleanly and read {structure.bias.value.lower()}"
    elif direction.is_directional:
        strength = EvidenceStrength.MODERATE
        reason = (
            f"structure reads {structure.bias.value.lower()}, though the swings do not "
            "alternate cleanly"
        )
    else:
        strength = EvidenceStrength.WEAK
        reason = f"structure is {structure.bias.value.lower()}, which favours neither side"

    return (
        _point_in_time(
            view,
            EvidenceSource.MARKET_STRUCTURE,
            EvidenceCategory.STRUCTURE,
            direction,
            strength,
            reason,
        ),
    )


_REGIME_DIRECTION: dict[MarketRegime, EvidenceDirection] = {
    MarketRegime.STRONG_UPTREND: EvidenceDirection.BULLISH,
    MarketRegime.WEAK_UPTREND: EvidenceDirection.BULLISH,
    MarketRegime.BREAKOUT: EvidenceDirection.BULLISH,
    MarketRegime.STRONG_DOWNTREND: EvidenceDirection.BEARISH,
    MarketRegime.WEAK_DOWNTREND: EvidenceDirection.BEARISH,
    MarketRegime.BREAKDOWN: EvidenceDirection.BEARISH,
    MarketRegime.RANGE: EvidenceDirection.NEUTRAL,
    MarketRegime.LOW_VOLATILITY_RANGE: EvidenceDirection.NEUTRAL,
    MarketRegime.HIGH_VOLATILITY_RANGE: EvidenceDirection.NEUTRAL,
    MarketRegime.CHAOTIC: EvidenceDirection.NEUTRAL,
    MarketRegime.UNCERTAIN: EvidenceDirection.NEUTRAL,
}

_STRONG_REGIMES = frozenset(
    {
        MarketRegime.STRONG_UPTREND,
        MarketRegime.STRONG_DOWNTREND,
        MarketRegime.BREAKOUT,
        MarketRegime.BREAKDOWN,
    }
)


def _regime(view: TimeframeView) -> tuple[EvidenceItem, ...]:
    """The Phase 2 classification, carried across unchanged.

    `UNCERTAIN` and `CHAOTIC` map to `NEUTRAL` rather than `UNAVAILABLE`: the
    engine did measure, and its answer was that the market favours neither
    side. The reason string keeps the two apart for a reader.
    """
    assessment = view.structure.regime
    direction = _REGIME_DIRECTION[assessment.regime]
    if assessment.regime in _STRONG_REGIMES:
        strength = EvidenceStrength.STRONG
    elif direction.is_directional:
        strength = EvidenceStrength.MODERATE
    else:
        strength = EvidenceStrength.WEAK

    return (
        _point_in_time(
            view,
            EvidenceSource.MARKET_REGIME,
            EvidenceCategory.REGIME,
            direction,
            strength,
            f"regime is {assessment.regime.value}: {assessment.reason}",
        ),
    )


def _levels(view: TimeframeView, config: EvidenceConfig) -> tuple[EvidenceItem, ...]:
    """The nearest zone on each side, when close enough to matter.

    A level *above* price is an obstacle to an advance, so it reads bearish; a
    level *below* is a floor, so it reads bullish. Neither is a prediction -
    §13 is explicit that a zone is an area of interest and its strength score
    is not a probability.
    """
    atr_values = view.technicals.atr
    atr = atr_values[view.last_index] if view.last_index < len(atr_values) else None
    if atr is None or atr <= 0:
        return (
            _point_in_time(
                view,
                EvidenceSource.SUPPORT_RESISTANCE,
                EvidenceCategory.LEVEL,
                EvidenceDirection.UNAVAILABLE,
                EvidenceStrength.WEAK,
                "level proximity needs ATR, which is not computable yet",
            ),
        )

    reach = Decimal(str(atr * config.level_proximity_atr))
    close = view.last_close
    items: list[EvidenceItem] = []

    nearest_resistance = _nearest(view.structure.resistance_zones, close, above=True)
    if nearest_resistance is not None and nearest_resistance.low - close <= reach:
        items.append(_zone_item(view, nearest_resistance, EvidenceDirection.BEARISH, config, close))

    nearest_support = _nearest(view.structure.support_zones, close, above=False)
    if nearest_support is not None and close - nearest_support.high <= reach:
        items.append(_zone_item(view, nearest_support, EvidenceDirection.BULLISH, config, close))

    return tuple(items)


def _nearest(zones: tuple[Zone, ...], close: Decimal, *, above: bool) -> Zone | None:
    candidates = [zone for zone in zones if (zone.low > close if above else zone.high < close)]
    if not candidates:
        return None
    return min(candidates, key=lambda zone: zone.low - close if above else close - zone.high)


def _zone_item(
    view: TimeframeView,
    zone: Zone,
    direction: EvidenceDirection,
    config: EvidenceConfig,
    close: Decimal,
) -> EvidenceItem:
    if zone.strength >= config.strong_zone_strength:
        strength = EvidenceStrength.STRONG
    elif zone.strength >= config.moderate_zone_strength:
        strength = EvidenceStrength.MODERATE
    else:
        strength = EvidenceStrength.WEAK

    side = "above" if zone.kind is ZoneKind.RESISTANCE else "below"
    return _point_in_time(
        view,
        EvidenceSource.SUPPORT_RESISTANCE,
        EvidenceCategory.LEVEL,
        direction,
        strength,
        f"{zone.kind.value.lower()} zone {zone.low}-{zone.high} sits {side} the close "
        f"{close} with {len(zone.touches)} touches (zone score {zone.strength:.2f}, "
        "not a probability)",
    )


# ----------------------------------------------------------------------
# Event observations: each carries the date it became knowable
# ----------------------------------------------------------------------


def _event_item(
    view: TimeframeView,
    source: EvidenceSource,
    category: EvidenceCategory,
    direction: EvidenceDirection,
    strength: EvidenceStrength,
    reason: str,
    confirmed_index: int,
    confirmed_time: datetime,
) -> EvidenceItem:
    return EvidenceItem(
        source=source,
        category=category,
        direction=direction,
        strength=strength,
        reason=reason,
        timeframe=view.timeframe,
        role=view.role,
        confirmed_index=confirmed_index,
        confirmed_time=confirmed_time,
        point_in_time=False,
    )


def _direction_of(direction: Direction) -> EvidenceDirection:
    if direction is Direction.LONG:
        return EvidenceDirection.BULLISH
    if direction is Direction.SHORT:
        return EvidenceDirection.BEARISH
    return EvidenceDirection.NEUTRAL


def _structural_events(view: TimeframeView) -> tuple[EvidenceItem, ...]:
    """BOS, CHOCH and LEVEL_BREAK, dated to their confirmation.

    `LEVEL_BREAK` stays deliberately weak. Phase 2 introduced it for a break
    with no established directional structure behind it, so it is real
    information about a level giving way and nothing at all about continuation
    or reversal.
    """
    items: list[EvidenceItem] = []
    for event in view.structure.structural_events:
        items.append(_structural_event_item(view, event))
    return tuple(items)


def _structural_event_item(view: TimeframeView, event: StructuralEvent) -> EvidenceItem:
    if event.event_type is StructuralEventType.CHOCH:
        strength = EvidenceStrength.STRONG
        note = f"character change away from a {event.prior_bias.value.lower()} structure"
    elif event.event_type is StructuralEventType.BOS:
        strength = EvidenceStrength.MODERATE
        note = f"continuation of the {event.prior_bias.value.lower()} structure"
    else:
        strength = EvidenceStrength.WEAK
        note = "no directional structure preceded it, so it says nothing about continuation"

    return _event_item(
        view,
        EvidenceSource.STRUCTURAL_EVENT,
        EvidenceCategory.STRUCTURE,
        _direction_of(event.direction),
        strength,
        f"{event.event_type.value} through {event.broken_level}: {note}",
        event.confirmed_index,
        event.confirmed_time,
    )


def _breakouts(view: TimeframeView) -> tuple[EvidenceItem, ...]:
    """Confirmed breakouts and failed ones, plus their volume separately.

    Unresolved states - a zone being challenged, a breach still inside its
    failure window - produce nothing. They are not yet facts.

    The breakout's participation is a **separate** item rather than a modifier
    on the breakout's own strength, so the same volume reading is never counted
    twice through two different doors.
    """
    items: list[EvidenceItem] = []
    for event in view.structure.breakout_events:
        if event.event_type is BreakoutEventType.CONFIRMED:
            items.append(_confirmed_breakout(view, event))
            items.append(_breakout_volume(view, event))
        elif event.event_type is BreakoutEventType.FALSE_BREAKOUT:
            items.append(_false_breakout(view, event))
    return tuple(items)


def _confirmed_breakout(view: TimeframeView, event: BreakoutEvent) -> EvidenceItem:
    return _event_item(
        view,
        EvidenceSource.BREAKOUT,
        EvidenceCategory.BREAKOUT,
        _direction_of(event.direction),
        EvidenceStrength.MODERATE,
        f"confirmed break of the {event.zone.kind.value.lower()} zone "
        f"{event.zone.low}-{event.zone.high} at {event.price}",
        event.confirmed_index,
        event.confirmed_time,
    )


def _false_breakout(view: TimeframeView, event: BreakoutEvent) -> EvidenceItem:
    """A failed break reads against the direction it was attempted in.

    Phase 2 only emits this once the failure has actually happened, so the
    reversal claim rests on observed price action rather than on a forecast.
    """
    attempted = _direction_of(event.direction)
    return _event_item(
        view,
        EvidenceSource.BREAKOUT,
        EvidenceCategory.BREAKOUT,
        attempted.opposite,
        EvidenceStrength.MODERATE,
        f"the {attempted.value.lower()} break of {event.zone.low}-{event.zone.high} failed "
        "and price returned inside the zone",
        event.confirmed_index,
        event.confirmed_time,
    )


def _breakout_volume(view: TimeframeView, event: BreakoutEvent) -> EvidenceItem:
    """Participation behind a break - never a direction of its own.

    `WEAK` means measured and low, which is genuinely neutral information.
    `UNAVAILABLE` means relative volume was not computable, which is not.
    """
    if event.volume_confirmation is VolumeConfirmation.CONFIRMED:
        direction = _direction_of(event.direction)
        strength = EvidenceStrength.MODERATE
        reason = "the break carried above-threshold participation"
    elif event.volume_confirmation is VolumeConfirmation.WEAK:
        direction = EvidenceDirection.NEUTRAL
        strength = EvidenceStrength.WEAK
        reason = (
            "the break carried below-threshold participation, which neither confirms nor denies it"
        )
    else:
        direction = EvidenceDirection.UNAVAILABLE
        strength = EvidenceStrength.WEAK
        reason = "relative volume is not computable at the break, so participation is unknown"

    relative = event.relative_volume
    detail = f" (relative volume {relative:.2f})" if relative is not None else ""
    return _event_item(
        view,
        EvidenceSource.BREAKOUT_VOLUME,
        EvidenceCategory.VOLUME,
        direction,
        strength,
        reason + detail,
        event.confirmed_index,
        event.confirmed_time,
    )


def _retests(view: TimeframeView) -> tuple[EvidenceItem, ...]:
    """Resolved retests only. A zone merely being touched is not yet a fact."""
    items: list[EvidenceItem] = []
    for event in view.structure.retest_events:
        if event.event_type is RetestEventType.HELD:
            items.append(_retest_item(view, event, held=True))
        elif event.event_type is RetestEventType.FAILED:
            items.append(_retest_item(view, event, held=False))
    return tuple(items)


def _retest_item(view: TimeframeView, event: RetestEvent, *, held: bool) -> EvidenceItem:
    breakout_direction = _direction_of(event.direction)
    direction = breakout_direction if held else breakout_direction.opposite
    return _event_item(
        view,
        EvidenceSource.RETEST,
        EvidenceCategory.RETEST,
        direction,
        EvidenceStrength.STRONG if held else EvidenceStrength.MODERATE,
        (
            f"the broken zone {event.zone.low}-{event.zone.high} held on retest"
            if held
            else f"the broken zone {event.zone.low}-{event.zone.high} failed on retest"
        ),
        event.confirmed_index,
        event.confirmed_time,
    )


def _divergences(view: TimeframeView) -> tuple[EvidenceItem, ...]:
    return tuple(_divergence_item(view, event) for event in view.structure.divergences)


def _divergence_item(view: TimeframeView, event: DivergenceEvent) -> EvidenceItem:
    direction = (
        EvidenceDirection.BEARISH
        if event.divergence_type is DivergenceType.BEARISH
        else EvidenceDirection.BULLISH
    )
    return _event_item(
        view,
        EvidenceSource.VOLUME_DIVERGENCE,
        EvidenceCategory.DIVERGENCE,
        direction,
        EvidenceStrength.MODERATE,
        f"price/volume divergence between confirmed swings: {event.reason}",
        event.confirmed_index,
        event.confirmed_time,
    )


# ----------------------------------------------------------------------
# Contract context: real information, deliberately not directional
# ----------------------------------------------------------------------


def _basis_item(basis: BasisResult) -> EvidenceItem:
    unavailable = basis.context is BasisContext.UNAVAILABLE
    return EvidenceItem(
        source=EvidenceSource.BASIS,
        category=EvidenceCategory.BASIS,
        direction=(EvidenceDirection.UNAVAILABLE if unavailable else EvidenceDirection.NEUTRAL),
        strength=EvidenceStrength.WEAK,
        reason=(
            f"basis context {basis.context.value}: {basis.reason}. "
            "Master spec section 32 makes this context, not a direction."
        ),
        timeframe=None,
        role=None,
        confirmed_index=None,
        confirmed_time=None,
        point_in_time=True,
    )


def _open_interest_item(reading: OpenInterestReading) -> EvidenceItem:
    unavailable = reading.context is OpenInterestContext.INSUFFICIENT_DATA
    return EvidenceItem(
        source=EvidenceSource.OPEN_INTEREST,
        category=EvidenceCategory.OPEN_INTEREST,
        direction=(EvidenceDirection.UNAVAILABLE if unavailable else EvidenceDirection.NEUTRAL),
        strength=EvidenceStrength.WEAK,
        reason=(
            f"open interest context {reading.context.value}: {reading.reason}. "
            "Master spec section 33 states these readings as possibilities, not directions."
        ),
        timeframe=None,
        role=None,
        confirmed_index=None,
        confirmed_time=None,
        point_in_time=True,
    )

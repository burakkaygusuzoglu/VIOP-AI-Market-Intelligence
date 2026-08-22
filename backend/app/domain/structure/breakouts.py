"""Breakout, breakdown, false breakout and retest — primitives, not strategies.

These emit facts about what price did to a level. None of them says buy or
sell, and none of them scores a setup; that is Phase 4 and later.

**The trap this module exists to avoid.** A false breakout is only knowable
*after* the failure. It is very easy - and very common - to walk a dataset,
notice that a breach at candle 40 was reversed at candle 43, and label candle
40 "false breakout". A backtest built on that never takes a losing breakout,
because it declines every trade using information from three candles into its
own future.

So the lifecycle is a stream of separate events, each stamped with the candle
that made it knowable:

    breach (40)  →  false breakout (43)      the breach stays a breach
    breach (40)  →  confirmed (43)           survived the failure window

The breach event at candle 40 is never rewritten. A reader asking "what did we
know at candle 41?" gets a breach and nothing else, which is the truth.

Retests follow the same discipline: they attach to an already-confirmed
breakout and never alter it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.enums import Direction
from app.domain.market.series import ValidatedCandleSeries
from app.domain.structure.zones import Zone, ZoneKind
from app.domain.technical.types import IndicatorValues


@unique
class BreakoutEventType(StrEnum):
    """Stages of the lifecycle, each its own event with its own timestamp."""

    CHALLENGE = "CHALLENGE"
    """Price entered the zone but did not close beyond it."""

    BREACH = "BREACH"
    """A candle closed beyond the zone. Not yet confirmed."""

    CONFIRMED = "CONFIRMED"
    """The breach survived the failure window without closing back inside."""

    FALSE_BREAKOUT = "FALSE_BREAKOUT"
    """Price closed back inside the zone within the failure window."""


@unique
class VolumeConfirmation(StrEnum):
    """Breakout volume confirmation (master spec section 11).

    Deliberately three states, and deliberately not a direction. This says
    whether the break carried unusual participation - nothing about whether to
    trade it.
    """

    CONFIRMED = "CONFIRMED"
    """Relative volume at the breach met the threshold."""

    WEAK = "WEAK"
    """Relative volume was below the threshold. The break may still be real."""

    UNAVAILABLE = "UNAVAILABLE"
    """Relative volume is not computable here - warm-up, or no volume data.
    Reported honestly rather than defaulted to WEAK, which would read as a
    measurement that was never taken."""


@dataclass(frozen=True, slots=True)
class BreakoutEvent:
    """One stage of one zone's lifecycle."""

    event_type: BreakoutEventType
    direction: Direction
    zone: Zone
    event_index: int
    event_time: datetime
    confirmed_index: int
    confirmed_time: datetime
    breach_index: int
    """The candle that first closed beyond the zone. Equal to ``event_index``
    on the BREACH event itself; on CONFIRMED and FALSE_BREAKOUT it points back
    at the breach being resolved."""

    price: Decimal
    volume_confirmation: VolumeConfirmation
    relative_volume: float | None
    reason: str

    def known_at(self, index: int) -> bool:
        return self.confirmed_index <= index


@unique
class RetestEventType(StrEnum):
    """What happened when price came back to a level it had already broken."""

    TOUCHED = "TOUCHED"
    """Price returned into the broken zone."""

    HELD = "HELD"
    """It left again in the breakout direction - old resistance acting as
    support, or the reverse."""

    FAILED = "FAILED"
    """It closed back through the zone against the breakout direction."""


@dataclass(frozen=True, slots=True)
class RetestEvent:
    """A retest of a level that was already confirmed broken."""

    event_type: RetestEventType
    direction: Direction
    zone: Zone
    breakout_confirmed_index: int
    """When the breakout this retest belongs to became knowable. A retest can
    never precede it."""

    event_index: int
    event_time: datetime
    confirmed_index: int
    confirmed_time: datetime
    price: Decimal
    reason: str

    def known_at(self, index: int) -> bool:
        return self.confirmed_index <= index


@dataclass(frozen=True, slots=True)
class BreakoutConfig:
    """Lifecycle windows. All project heuristics, all configurable."""

    failure_window: int = 3
    """Candles after a breach during which a close back inside the zone makes
    it a false breakout. It is also the confirmation lag: a breach is confirmed
    at ``breach + failure_window`` precisely because that is the first candle
    at which it is known *not* to have failed. Shortening this confirms faster
    and calls more failures 'confirmed breakouts'."""

    retest_window: int = 20
    """Candles after confirmation during which a return to the zone counts as a
    retest of that breakout rather than unrelated price action."""

    retest_resolution_window: int = 5
    """Candles after a retest touch in which it must resolve to HELD or
    FAILED. Unresolved retests emit only the TOUCHED event."""

    volume_confirmation_multiple: float = 1.5
    """Relative volume at the breach candle needed for CONFIRMED. 1.5x the
    20-candle average is a common chart-reading rule of thumb; it is a
    heuristic, not a measured edge."""

    def __post_init__(self) -> None:
        if self.failure_window < 1:
            raise ValueError("failure_window must be >= 1")
        if self.retest_window < 1:
            raise ValueError("retest_window must be >= 1")
        if self.retest_resolution_window < 1:
            raise ValueError("retest_resolution_window must be >= 1")
        if self.volume_confirmation_multiple <= 0:
            raise ValueError("volume_confirmation_multiple must be positive")


def detect_breakouts(
    series: ValidatedCandleSeries,
    zones: Sequence[Zone],
    relative_volume: IndicatorValues,
    config: BreakoutConfig | None = None,
) -> tuple[BreakoutEvent, ...]:
    """Walk each zone's lifecycle forward through the candles.

    A resistance zone breaks **upward**, a support zone **downward**; the
    direction is a property of which side the zone was on, not a prediction.

    For each zone, scanning begins at the candle after the zone became knowable
    (``zone.confirmed_index``) - a zone cannot be broken before it exists.

    The lifecycle per breach:

    1. ``CHALLENGE`` while price enters the zone without closing beyond it.
    2. ``BREACH`` on the first close beyond the zone.
    3. Then either ``FALSE_BREAKOUT`` - a close back inside or past the zone
       within ``failure_window`` candles, stamped at *that* candle - or
       ``CONFIRMED`` at ``breach + failure_window``.

    After a resolution the zone is done; one zone yields at most one breach
    outcome, because after a genuine break the zone is no longer the level it
    was.
    """
    settings = config if config is not None else BreakoutConfig()
    events: list[BreakoutEvent] = []
    for zone in zones:
        events.extend(_zone_lifecycle(series, zone, relative_volume, settings))
    return tuple(sorted(events, key=lambda event: (event.confirmed_index, event.event_index)))


def _zone_lifecycle(
    series: ValidatedCandleSeries,
    zone: Zone,
    relative_volume: IndicatorValues,
    settings: BreakoutConfig,
) -> list[BreakoutEvent]:
    total = len(series)
    highs = series.highs
    lows = series.lows
    closes = series.closes
    times = series.open_times

    upward = zone.kind is ZoneKind.RESISTANCE
    direction = Direction.LONG if upward else Direction.SHORT
    events: list[BreakoutEvent] = []
    challenged = False

    for index in range(zone.confirmed_index + 1, total):
        close = closes[index]
        beyond = close > zone.high if upward else close < zone.low

        if not beyond:
            touched = highs[index] >= zone.low if upward else lows[index] <= zone.high
            if touched and not challenged:
                challenged = True
                events.append(
                    _event(
                        BreakoutEventType.CHALLENGE,
                        direction=direction,
                        zone=zone,
                        index=index,
                        time=times[index],
                        confirmed_index=index,
                        confirmed_time=times[index],
                        breach_index=index,
                        price=close,
                        volume=VolumeConfirmation.UNAVAILABLE,
                        relative=None,
                        reason=(
                            f"price entered the zone without closing beyond {zone.low}-{zone.high}"
                        ),
                    )
                )
            continue

        volume_state, relative = _volume_confirmation(relative_volume, index, settings)
        events.append(
            _event(
                BreakoutEventType.BREACH,
                direction=direction,
                zone=zone,
                index=index,
                time=times[index],
                confirmed_index=index,
                confirmed_time=times[index],
                breach_index=index,
                price=close,
                volume=volume_state,
                relative=relative,
                reason=f"close {close} beyond the zone {zone.low}-{zone.high}",
            )
        )

        resolution = _resolve_breach(
            series, zone, index, upward, settings, volume_state, relative, direction
        )
        if resolution is not None:
            events.append(resolution)
        return events

    return events


def _resolve_breach(
    series: ValidatedCandleSeries,
    zone: Zone,
    breach_index: int,
    upward: bool,
    settings: BreakoutConfig,
    volume_state: VolumeConfirmation,
    relative: float | None,
    direction: Direction,
) -> BreakoutEvent | None:
    """Decide the breach's fate using only candles after it.

    The returned event is stamped with the candle that settled it, never with
    the breach candle. That is the whole point.
    """
    total = len(series)
    closes = series.closes
    times = series.open_times

    for offset in range(1, settings.failure_window + 1):
        index = breach_index + offset
        if index >= total:
            return None  # the window has not finished; nothing is known yet
        close = closes[index]
        returned = close <= zone.high if upward else close >= zone.low
        if returned:
            return _event(
                BreakoutEventType.FALSE_BREAKOUT,
                direction=direction,
                zone=zone,
                index=index,
                time=times[index],
                confirmed_index=index,
                confirmed_time=times[index],
                breach_index=breach_index,
                price=close,
                volume=volume_state,
                relative=relative,
                reason=(
                    f"close {close} returned inside the zone {offset} candle(s) after the "
                    f"breach at index {breach_index}"
                ),
            )

    confirmed_index = breach_index + settings.failure_window
    return _event(
        BreakoutEventType.CONFIRMED,
        direction=direction,
        zone=zone,
        index=confirmed_index,
        time=times[confirmed_index],
        confirmed_index=confirmed_index,
        confirmed_time=times[confirmed_index],
        breach_index=breach_index,
        price=closes[confirmed_index],
        volume=volume_state,
        relative=relative,
        reason=(f"the breach at index {breach_index} held for {settings.failure_window} candle(s)"),
    )


def detect_retests(
    series: ValidatedCandleSeries,
    breakouts: Sequence[BreakoutEvent],
    config: BreakoutConfig | None = None,
) -> tuple[RetestEvent, ...]:
    """Find returns to levels that were already confirmed broken.

    Only ``CONFIRMED`` breakouts are eligible, and scanning starts at the
    candle after ``confirmed_index``. A retest therefore cannot exist before
    the breakout it belongs to was knowable, and - critically - discovering one
    later never edits the breakout event.

    Resolution: after the touch, ``HELD`` when a close moves back beyond the
    zone in the breakout direction, ``FAILED`` when a close goes through it the
    other way. Neither within ``retest_resolution_window`` leaves the retest
    reported as ``TOUCHED`` and nothing more, which is an honest "we do not
    know yet".
    """
    settings = config if config is not None else BreakoutConfig()
    total = len(series)
    highs = series.highs
    lows = series.lows
    closes = series.closes
    times = series.open_times

    events: list[RetestEvent] = []
    for breakout in breakouts:
        if breakout.event_type is not BreakoutEventType.CONFIRMED:
            continue

        zone = breakout.zone
        upward = zone.kind is ZoneKind.RESISTANCE
        start = breakout.confirmed_index + 1
        stop = min(total, start + settings.retest_window)

        for index in range(start, stop):
            entered = lows[index] <= zone.high if upward else highs[index] >= zone.low
            if not entered:
                continue

            events.append(
                RetestEvent(
                    event_type=RetestEventType.TOUCHED,
                    direction=breakout.direction,
                    zone=zone,
                    breakout_confirmed_index=breakout.confirmed_index,
                    event_index=index,
                    event_time=times[index],
                    confirmed_index=index,
                    confirmed_time=times[index],
                    price=closes[index],
                    reason=f"price returned into the broken zone {zone.low}-{zone.high}",
                )
            )
            resolution = _resolve_retest(series, breakout, zone, index, upward, settings)
            if resolution is not None:
                events.append(resolution)
            break  # one retest per breakout; later returns are new price action

    return tuple(sorted(events, key=lambda event: (event.confirmed_index, event.event_index)))


def _resolve_retest(
    series: ValidatedCandleSeries,
    breakout: BreakoutEvent,
    zone: Zone,
    touch_index: int,
    upward: bool,
    settings: BreakoutConfig,
) -> RetestEvent | None:
    total = len(series)
    closes = series.closes
    times = series.open_times

    for offset in range(1, settings.retest_resolution_window + 1):
        index = touch_index + offset
        if index >= total:
            return None
        close = closes[index]
        held = close > zone.high if upward else close < zone.low
        failed = close < zone.low if upward else close > zone.high
        if held or failed:
            return RetestEvent(
                event_type=RetestEventType.HELD if held else RetestEventType.FAILED,
                direction=breakout.direction,
                zone=zone,
                breakout_confirmed_index=breakout.confirmed_index,
                event_index=index,
                event_time=times[index],
                confirmed_index=index,
                confirmed_time=times[index],
                price=close,
                reason=(
                    f"close {close} left the zone in the breakout direction"
                    if held
                    else f"close {close} went back through the zone"
                ),
            )
    return None


def _volume_confirmation(
    relative_volume: IndicatorValues, index: int, settings: BreakoutConfig
) -> tuple[VolumeConfirmation, float | None]:
    """Phase 1 relative volume, read - never recomputed - at the breach candle."""
    if index >= len(relative_volume):
        return VolumeConfirmation.UNAVAILABLE, None
    value = relative_volume[index]
    if value is None:
        return VolumeConfirmation.UNAVAILABLE, None
    state = (
        VolumeConfirmation.CONFIRMED
        if value >= settings.volume_confirmation_multiple
        else VolumeConfirmation.WEAK
    )
    return state, value


def _event(
    event_type: BreakoutEventType,
    *,
    direction: Direction,
    zone: Zone,
    index: int,
    time: datetime,
    confirmed_index: int,
    confirmed_time: datetime,
    breach_index: int,
    price: Decimal,
    volume: VolumeConfirmation,
    relative: float | None,
    reason: str,
) -> BreakoutEvent:
    return BreakoutEvent(
        event_type=event_type,
        direction=direction,
        zone=zone,
        event_index=index,
        event_time=time,
        confirmed_index=confirmed_index,
        confirmed_time=confirmed_time,
        breach_index=breach_index,
        price=price,
        volume_confirmation=volume,
        relative_volume=relative,
        reason=reason,
    )

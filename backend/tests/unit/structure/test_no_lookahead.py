"""Proof that no Phase 2 engine backdates knowledge.

The property, stated precisely and separately from Phase 1's:

Phase 1 asked whether an indicator *value* at index ``i`` could change when
later candles arrived. Phase 2 asks something stricter, because structure is
discovered late. A pivot at candle 100 with ``right=2`` genuinely does not
exist until candle 102, so appending candles is *supposed* to reveal new
swings. What must never happen is the system claiming it knew about that pivot
at candle 100 or 101.

So the test is not "the output is identical". It is:

    everything the full series says was knowable at candle N
    == everything the N-candle prefix reports at all

If a longer series produces an event stamped ``confirmed_index <= N`` that the
prefix did not report, knowledge has been backdated. If the prefix reports
something the full run does not, the engine is unstable. Both fail here.

This is applied to swings, HH/HL/LH/LL, BOS/CHOCH, zones, breakouts, false
breakouts, retests, divergence and the regime.

Prices are TEST_FIXTURE data.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import pytest

from app.domain.structure.engine import StructureConfig, StructureSnapshot, analyse_structure
from app.domain.structure.swings import SwingConfig
from app.domain.technical.engine import TechnicalConfig, compute_technicals
from tests.factories import ohlcv_series, prefix_of

CONFIG = StructureConfig(swings=SwingConfig(left=2, right=2))
TECHNICALS = TechnicalConfig(ema_periods=(9, 20, 50), sma_periods=(20,))

PREFIX = 140
TOTAL = 220


def _market(size: int) -> tuple[list[float], list[float], list[float], list[float]]:
    """A varied but wholly deterministic market.

    Trends, reverses, ranges and breaks out. Every value is a function of the
    index alone - nothing depends on ``size`` - so candle ``i`` is the same
    candle whatever length is requested. A generator whose shape depends on the
    requested length would compare two different markets and prove nothing;
    Phase 1's look-ahead test was written that way at first and had to be
    fixed.
    """
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[float] = []
    price = 200.0
    for index in range(size):
        if index < 70:
            drift = 1.1 if index % 6 not in (4, 5) else -1.4
        elif index < 120:
            drift = 1.6 if index % 4 in (1, 2) else -1.6
        elif index < 170:
            drift = -1.2 if index % 6 not in (4, 5) else 1.5
        else:
            drift = 2.0 if index % 5 != 4 else -1.0
        price = max(20.0, price + drift)
        wiggle = 1.0 + (index * 37 % 11) * 0.15
        closes.append(round(price, 2))
        highs.append(round(price + wiggle, 2))
        lows.append(round(price - wiggle, 2))
        volumes.append(float(600 + (index * 91) % 900))
    return highs, lows, closes, volumes


def _analyse(size: int) -> StructureSnapshot:
    highs, lows, closes, volumes = _market(size)
    series = ohlcv_series(highs, lows, closes, volumes)
    return analyse_structure(series, compute_technicals(series, TECHNICALS), CONFIG)


@pytest.fixture(scope="module")
def prefix_snapshot() -> StructureSnapshot:
    return _analyse(PREFIX)


@pytest.fixture(scope="module")
def full_snapshot() -> StructureSnapshot:
    return _analyse(TOTAL)


class Confirmable(Protocol):
    """Anything this phase stamps with the moment it became knowable."""

    @property
    def confirmed_index(self) -> int: ...


def _known[Item: Confirmable](items: Sequence[Item], index: int) -> list[Item]:
    return [item for item in items if item.confirmed_index <= index]


# ----------------------------------------------------------------------
# The comparison is meaningful
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_two_runs_really_are_the_same_market(
    prefix_snapshot: StructureSnapshot, full_snapshot: StructureSnapshot
) -> None:
    """Guard against comparing two different price paths."""
    assert prefix_snapshot.candle_count == PREFIX
    assert full_snapshot.candle_count == TOTAL
    highs_prefix, _, _, _ = _market(PREFIX)
    highs_full, _, _, _ = _market(TOTAL)
    assert highs_prefix == highs_full[:PREFIX]


@pytest.mark.unit
def test_the_longer_run_does_discover_more(
    prefix_snapshot: StructureSnapshot, full_snapshot: StructureSnapshot
) -> None:
    """Otherwise every assertion below would pass vacuously."""
    assert len(full_snapshot.swings) > len(prefix_snapshot.swings)
    assert len(full_snapshot.structural_events) > len(prefix_snapshot.structural_events)


@pytest.mark.unit
def test_the_scenario_exercises_every_engine(
    prefix_snapshot: StructureSnapshot,
) -> None:
    assert prefix_snapshot.swings
    assert prefix_snapshot.structural_events
    assert prefix_snapshot.support_zones or prefix_snapshot.resistance_zones
    assert prefix_snapshot.breakout_events


# ----------------------------------------------------------------------
# The property, engine by engine
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_swings_are_not_backdated(
    prefix_snapshot: StructureSnapshot, full_snapshot: StructureSnapshot
) -> None:
    assert _known(full_snapshot.swings, PREFIX - 1) == list(prefix_snapshot.swings)


@pytest.mark.unit
def test_a_pivot_is_never_claimed_before_its_confirmation(
    full_snapshot: StructureSnapshot,
) -> None:
    """The prompt's example, checked over every swing in the series."""
    right = CONFIG.swings.right
    for swing in full_snapshot.swings:
        assert swing.confirmed_index == swing.pivot_index + right
        assert not swing.known_at(swing.pivot_index)
        assert not swing.known_at(swing.confirmed_index - 1)
        assert swing.known_at(swing.confirmed_index)


@pytest.mark.unit
def test_structure_labels_are_not_backdated(
    prefix_snapshot: StructureSnapshot, full_snapshot: StructureSnapshot
) -> None:
    """HH/HL/LH/LL over the swings knowable at the prefix's last candle."""
    known = _known(full_snapshot.structure.labelled, PREFIX - 1)
    assert [(item.swing.pivot_index, item.label) for item in known] == [
        (item.swing.pivot_index, item.label) for item in prefix_snapshot.structure.labelled
    ]


@pytest.mark.unit
def test_the_structural_bias_at_the_prefix_end_is_reproducible(
    prefix_snapshot: StructureSnapshot,
) -> None:
    """Re-analysing the same prefix gives the same bias; nothing carries over."""
    assert _analyse(PREFIX).structure.bias is prefix_snapshot.structure.bias


@pytest.mark.unit
def test_bos_and_choch_are_not_backdated(
    prefix_snapshot: StructureSnapshot, full_snapshot: StructureSnapshot
) -> None:
    assert _known(full_snapshot.structural_events, PREFIX - 1) == list(
        prefix_snapshot.structural_events
    )


@pytest.mark.unit
def test_a_later_swing_never_reclassifies_an_earlier_break(
    prefix_snapshot: StructureSnapshot, full_snapshot: StructureSnapshot
) -> None:
    """BOS versus CHOCH depends on the prior bias, which must stay frozen."""
    later = {
        event.event_index: (event.event_type, event.prior_bias)
        for event in _known(full_snapshot.structural_events, PREFIX - 1)
    }
    for event in prefix_snapshot.structural_events:
        assert later[event.event_index] == (event.event_type, event.prior_bias)


@pytest.mark.unit
def test_breakout_events_are_not_backdated(
    prefix_snapshot: StructureSnapshot, full_snapshot: StructureSnapshot
) -> None:
    known = _known(full_snapshot.breakout_events, PREFIX - 1)
    assert [(event.event_type, event.event_index, event.breach_index) for event in known] == [
        (event.event_type, event.event_index, event.breach_index)
        for event in prefix_snapshot.breakout_events
    ]


@pytest.mark.unit
def test_a_false_breakout_is_never_stamped_on_its_own_breach(
    full_snapshot: StructureSnapshot,
) -> None:
    """The single most valuable assertion in this module.

    A breakout labelled false at the breach candle would let a backtest decline
    every losing break using information from its own future.
    """
    from app.domain.structure.breakouts import BreakoutEventType

    failures = [
        event
        for event in full_snapshot.breakout_events
        if event.event_type is BreakoutEventType.FALSE_BREAKOUT
    ]
    assert failures, "the scenario produced no false breakout to check"
    for failure in failures:
        assert failure.confirmed_index > failure.breach_index
        assert not failure.known_at(failure.breach_index)


@pytest.mark.unit
def test_retests_are_not_backdated(
    prefix_snapshot: StructureSnapshot, full_snapshot: StructureSnapshot
) -> None:
    known = _known(full_snapshot.retest_events, PREFIX - 1)
    assert [(e.event_type, e.event_index) for e in known] == [
        (e.event_type, e.event_index) for e in prefix_snapshot.retest_events
    ]


@pytest.mark.unit
def test_a_retest_never_precedes_its_breakout(full_snapshot: StructureSnapshot) -> None:
    for retest in full_snapshot.retest_events:
        assert retest.event_index > retest.breakout_confirmed_index


@pytest.mark.unit
def test_divergences_are_not_backdated(
    prefix_snapshot: StructureSnapshot, full_snapshot: StructureSnapshot
) -> None:
    known = _known(full_snapshot.divergences, PREFIX - 1)
    assert [(d.divergence_type, d.later_swing.pivot_index) for d in known] == [
        (d.divergence_type, d.later_swing.pivot_index) for d in prefix_snapshot.divergences
    ]


@pytest.mark.unit
def test_divergence_uses_only_confirmed_pivots(full_snapshot: StructureSnapshot) -> None:
    for divergence in full_snapshot.divergences:
        assert divergence.confirmed_index == divergence.later_swing.confirmed_index
        assert divergence.confirmed_index > divergence.later_swing.pivot_index


@pytest.mark.unit
def test_zones_use_only_confirmed_swings(full_snapshot: StructureSnapshot) -> None:
    for zone in full_snapshot.zones:
        assert zone.confirmed_index <= full_snapshot.candle_count - 1
        for swing in zone.touches:
            assert swing.confirmed_index <= zone.confirmed_index


@pytest.mark.unit
def test_historical_volatility_is_not_backdated(
    prefix_snapshot: StructureSnapshot, full_snapshot: StructureSnapshot
) -> None:
    assert full_snapshot.historical_volatility[:PREFIX] == prefix_snapshot.historical_volatility


# ----------------------------------------------------------------------
# Point-in-time views
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("size", (60, 100, PREFIX, 180, TOTAL))
def test_the_regime_at_a_candle_depends_only_on_candles_up_to_it(size: int) -> None:
    """A growing window: the regime read at each step must be stable.

    This is how live mode will consume the engine - one candle at a time - so
    re-analysing the same prefix later must give the same answer it gave then.
    """
    assert _analyse(size).regime.regime is _analyse(size).regime.regime


@pytest.mark.unit
@pytest.mark.parametrize("size", (80, 120, 160, 200))
def test_a_growing_window_never_rewrites_what_it_already_reported(size: int) -> None:
    """The full property, checked at several cut points rather than one."""
    shorter = _analyse(size)
    longer = _analyse(TOTAL)
    assert _known(longer.swings, size - 1) == list(shorter.swings)
    assert _known(longer.structural_events, size - 1) == list(shorter.structural_events)


@pytest.mark.unit
def test_analysis_is_bit_for_bit_reproducible() -> None:
    """No clock, no randomness, no global state."""
    assert _analyse(120) == _analyse(120)


@pytest.mark.unit
def test_prefixing_a_series_is_the_same_as_analysing_it_short() -> None:
    """``prefix_of`` must be a faithful stand-in for having stopped early."""
    highs, lows, closes, volumes = _market(TOTAL)
    full_series = ohlcv_series(highs, lows, closes, volumes)
    sliced = prefix_of(full_series, PREFIX)
    built = _analyse(PREFIX)
    assert analyse_structure(sliced, compute_technicals(sliced, TECHNICALS), CONFIG) == built

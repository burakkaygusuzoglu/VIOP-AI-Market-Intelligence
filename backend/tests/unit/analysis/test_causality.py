"""No look-ahead: evidence cannot exist before its source was knowable.

The Phase 2 discipline, carried into Phase 4. The argument rests on one
comparison: analysing a *prefix* is exactly what the engine would have seen in
real time at that candle, so any evidence it reports there must survive the
arrival of later candles unchanged, and the full run must report nothing extra
at that date.

Point-in-time evidence - a regime, a structure bias, the nearest level - is
excluded from the comparison by construction, not by convenience: it describes
the final candle only, so it is legitimately different at every candle and
there is nothing to hold constant. Event evidence is the part that carries a
date, and it is the part tested here.

Every market is TEST_FIXTURE data.
"""

from __future__ import annotations

import pytest

from app.domain.analysis.evidence import EvidenceItem, evidence_known_at
from app.domain.analysis.generation import build_timeframe_evidence
from app.domain.analysis.timeframes import TimeframeRole
from app.domain.common.enums import Timeframe
from app.domain.market.series import ValidatedCandleSeries
from tests.factories import prefix_of
from tests.factories_analysis import BULLISH_DRIFT, view, zigzag

FULL = 140
CHECKPOINTS = (60, 80, 100, 120)


def market(timeframe: Timeframe = Timeframe.H1) -> ValidatedCandleSeries:
    """One market, analysed whole and in prefixes.

    Fixed shape and fixed length: a generator whose behaviour depended on the
    series length would make the prefix and the whole *different markets*, and
    the comparison below would prove nothing. That mistake was made once in
    Phase 1 and is not repeated here.
    """
    return zigzag(drift=BULLISH_DRIFT, size=FULL, timeframe=timeframe)


def events_at(series: ValidatedCandleSeries, count: int) -> tuple[EvidenceItem, ...]:
    """Event evidence from the first ``count`` candles, as seen at the time."""
    subject = view(TimeframeRole.BIAS, prefix_of(series, count))
    return tuple(item for item in build_timeframe_evidence(subject) if not item.point_in_time)


def events_known_at(series: ValidatedCandleSeries, count: int) -> tuple[EvidenceItem, ...]:
    """Event evidence the *full* run says was knowable by ``count - 1``."""
    subject = view(TimeframeRole.BIAS, series)
    items = tuple(item for item in build_timeframe_evidence(subject) if not item.point_in_time)
    return evidence_known_at(items, count - 1)


def comparable(items: tuple[EvidenceItem, ...]) -> set[tuple[object, ...]]:
    """Identity without the timeframe-position fields that differ by run."""
    return {
        (item.source, item.category, item.direction, item.strength, item.confirmed_index)
        for item in items
    }


# ----------------------------------------------------------------------
# A future event cannot create evidence early
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("count", CHECKPOINTS)
def test_the_full_run_claims_nothing_at_a_checkpoint_the_prefix_did_not_know(
    count: int,
) -> None:
    """The look-ahead test proper.

    If the full analysis dated any evidence at or before candle ``count - 1``
    that the prefix run did not produce, that evidence was created by candles
    which had not happened yet.
    """
    series = market()
    assert comparable(events_known_at(series, count)) <= comparable(events_at(series, count))


@pytest.mark.unit
@pytest.mark.parametrize("count", CHECKPOINTS)
def test_no_evidence_is_dated_after_the_candle_that_produced_it(count: int) -> None:
    for item in events_at(market(), count):
        assert item.confirmed_index is not None
        assert 0 <= item.confirmed_index <= count - 1


@pytest.mark.unit
def test_a_swing_cannot_produce_evidence_before_it_confirms() -> None:
    """A pivot at candle 100 confirmed at 102 is knowable at 102, never at 100.

    Asserted against the Phase 2 swing records themselves rather than against a
    restatement of them.
    """
    subject = view(TimeframeRole.BIAS, market())
    items = build_timeframe_evidence(subject)
    for swing in subject.structure.swings:
        assert swing.confirmed_index >= swing.pivot_index
    for item in items:
        if item.point_in_time or item.confirmed_index is None:
            continue
        assert item.confirmed_index <= subject.last_index


# ----------------------------------------------------------------------
# Future candles cannot rewrite what was already known
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("count", CHECKPOINTS)
def test_appending_candles_does_not_revise_earlier_evidence(count: int) -> None:
    """Whatever the prefix knew and the full run still dates at that time must
    be identical in direction and strength. A revision would mean later price
    action rewrote an earlier fact."""
    series = market()
    earlier = {
        (item.source, item.confirmed_index): (item.direction, item.strength)
        for item in events_at(series, count)
    }
    for item in events_known_at(series, count):
        key = (item.source, item.confirmed_index)
        if key in earlier:
            assert earlier[key] == (item.direction, item.strength)


@pytest.mark.unit
def test_evidence_grows_monotonically_as_candles_arrive() -> None:
    """Each checkpoint knows everything the one before it knew."""
    series = market()
    previous: set[tuple[object, ...]] = set()
    for count in CHECKPOINTS:
        current = comparable(events_at(series, count))
        assert previous <= current
        previous = current


@pytest.mark.unit
def test_a_breakout_that_fails_later_was_not_bearish_at_the_time() -> None:
    """A false breakout only exists once the failure has happened.

    Before that, the same candles were a breach and nothing more. Phase 2
    already enforces this; the test confirms the evidence layer did not
    backdate the reversal it eventually reports.
    """
    series = market()
    full = view(TimeframeRole.BIAS, series)
    failures = [
        event
        for event in full.structure.breakout_events
        if event.event_type.value == "FALSE_BREAKOUT"
    ]
    for event in failures:
        assert event.confirmed_index > event.breach_index
        early = events_at(series, event.breach_index + 1)
        assert all(
            item.confirmed_index != event.confirmed_index
            for item in early
            if item.source.value == "BREAKOUT"
        )


@pytest.mark.unit
def test_a_retest_is_dated_to_its_resolution_not_to_the_touch() -> None:
    subject = view(TimeframeRole.BIAS, market())
    for event in subject.structure.retest_events:
        if event.event_type.value in {"HELD", "FAILED"}:
            assert event.confirmed_index >= event.event_index


@pytest.mark.unit
def test_the_prefix_and_the_full_run_agree_on_the_same_market() -> None:
    """Guards the test itself: if the prefix and the whole were different
    markets, every assertion above would pass vacuously."""
    series = market()
    prefix = prefix_of(series, 100)
    assert prefix.candles == series.candles[:100]
    assert prefix.timeframe is series.timeframe

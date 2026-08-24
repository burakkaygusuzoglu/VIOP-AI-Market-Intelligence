"""Evidence generation from Phase 1-3 output (master spec section 16).

Every value here is TEST_FIXTURE data. The markets are synthetic shapes chosen
so one reading is unambiguous, never a recording of a real instrument.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain.analysis.evidence import (
    EvidenceCategory,
    EvidenceDirection,
    EvidenceSource,
    EvidenceStrength,
    directional,
    evidence_known_at,
    opposes,
    strongest,
)
from app.domain.analysis.generation import (
    build_contract_evidence,
    build_timeframe_evidence,
)
from app.domain.analysis.timeframes import TimeframeRole
from app.domain.common.enums import Timeframe
from app.domain.futures.basis import BasisContext, BasisResult
from app.domain.futures.open_interest import OpenInterestContext, OpenInterestReading
from tests.factories_analysis import (
    BEARISH_DRIFT,
    BULLISH_DRIFT,
    FLAT_DRIFT,
    market_view,
    view,
    zigzag,
)


def evidence_of(drift: float, role: TimeframeRole = TimeframeRole.BIAS, size: int = 140):  # type: ignore[no-untyped-def]
    return build_timeframe_evidence(market_view(role, drift=drift, size=size))


def item_for(items, source: EvidenceSource):  # type: ignore[no-untyped-def]
    found = [item for item in items if item.source is source]
    return found[0] if found else None


# ----------------------------------------------------------------------
# The vocabulary itself
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_strength_is_ordinal_and_cannot_be_summed() -> None:
    """Section 19 forbids presenting an analysis score as a likelihood.

    Making the enum a plain ``StrEnum`` with an explicit ``rank`` means the
    arithmetic that would produce "78% chance of LONG" does not typecheck, let
    alone run. Ordering is available; addition is not.
    """
    assert not issubclass(EvidenceStrength, int)
    assert EvidenceStrength.STRONG.at_least(EvidenceStrength.MODERATE)
    assert not EvidenceStrength.WEAK.at_least(EvidenceStrength.MODERATE)

    with pytest.raises(TypeError):
        sum([EvidenceStrength.WEAK, EvidenceStrength.STRONG])  # type: ignore[list-item]

    # `+` between two members is string concatenation, not addition. Worth
    # pinning: it is the one operator that does not fail loudly, and the
    # result is visibly a label rather than a total.
    assert EvidenceStrength.WEAK + EvidenceStrength.STRONG == "WEAKSTRONG"


@pytest.mark.unit
def test_no_evidence_field_offers_a_probability() -> None:
    from app.domain.analysis.evidence import EvidenceItem  # noqa: PLC0415

    forbidden = {"probability", "confidence", "score", "percent", "likelihood", "odds"}
    assert forbidden.isdisjoint(EvidenceItem.__dataclass_fields__)


@pytest.mark.unit
def test_unavailable_and_neutral_are_separate_members() -> None:
    """Measured-and-balanced is not the same fact as could-not-measure.

    Both are non-directional, which is exactly why they would be tempting to
    collapse into one value - and collapsing them would let missing data pass
    as a finding.
    """
    assert len({member.value for member in EvidenceDirection}) == 4
    assert not EvidenceDirection.NEUTRAL.is_directional
    assert not EvidenceDirection.UNAVAILABLE.is_directional
    assert EvidenceDirection.NEUTRAL.opposite is EvidenceDirection.NEUTRAL
    assert EvidenceDirection.UNAVAILABLE.opposite is EvidenceDirection.UNAVAILABLE


@pytest.mark.unit
@pytest.mark.parametrize(
    ("first", "second", "expected"),
    (
        (EvidenceDirection.BULLISH, EvidenceDirection.BEARISH, True),
        (EvidenceDirection.BULLISH, EvidenceDirection.BULLISH, False),
        (EvidenceDirection.BULLISH, EvidenceDirection.NEUTRAL, False),
        (EvidenceDirection.BULLISH, EvidenceDirection.UNAVAILABLE, False),
        (EvidenceDirection.NEUTRAL, EvidenceDirection.UNAVAILABLE, False),
    ),
)
def test_only_two_directional_readings_can_oppose(
    first: EvidenceDirection, second: EvidenceDirection, expected: bool
) -> None:
    """Neither a measured neutral nor an absent reading contradicts anything."""
    assert opposes(first, second) is expected


@pytest.mark.unit
def test_strongest_takes_a_maximum_and_never_a_total() -> None:
    items = evidence_of(BULLISH_DRIFT)
    assert strongest(items) in set(EvidenceStrength)
    assert strongest(()) is None


# ----------------------------------------------------------------------
# Bullish, bearish, neutral
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_rising_market_reads_bullish() -> None:
    items = evidence_of(BULLISH_DRIFT)
    for source in (
        EvidenceSource.EMA_ALIGNMENT,
        EvidenceSource.MARKET_STRUCTURE,
        EvidenceSource.MARKET_REGIME,
    ):
        assert item_for(items, source).direction is EvidenceDirection.BULLISH


@pytest.mark.unit
def test_a_falling_market_reads_bearish() -> None:
    items = evidence_of(BEARISH_DRIFT)
    for source in (
        EvidenceSource.EMA_ALIGNMENT,
        EvidenceSource.MARKET_STRUCTURE,
        EvidenceSource.MARKET_REGIME,
    ):
        assert item_for(items, source).direction is EvidenceDirection.BEARISH


@pytest.mark.unit
def test_a_ranging_market_reads_neutral_not_absent() -> None:
    """The engine measured and found no direction. That is a finding."""
    items = evidence_of(FLAT_DRIFT)
    assert item_for(items, EvidenceSource.MARKET_REGIME).direction is EvidenceDirection.NEUTRAL
    assert item_for(items, EvidenceSource.MARKET_STRUCTURE).direction is EvidenceDirection.NEUTRAL


@pytest.mark.unit
def test_warm_up_reads_unavailable_not_neutral() -> None:
    """Too few candles for an EMA stack or a swing is missing data, not balance."""
    items = build_timeframe_evidence(
        view(TimeframeRole.BIAS, zigzag(drift=BULLISH_DRIFT, size=6, timeframe=Timeframe.H1))
    )
    assert item_for(items, EvidenceSource.EMA_ALIGNMENT).direction is EvidenceDirection.UNAVAILABLE
    assert (
        item_for(items, EvidenceSource.MARKET_STRUCTURE).direction is EvidenceDirection.UNAVAILABLE
    )


@pytest.mark.unit
def test_a_trending_market_grades_stronger_than_a_ranging_one() -> None:
    """Relative, deliberately.

    The grade comes from the Phase 2 regime, whose STRONG/WEAK boundary is an
    ADX threshold. These fixtures sit either side of it by a fraction - the
    rising market reaches ADX 24.7 and the falling one 25.2 - so asserting an
    absolute grade would pin the test to an accident of the fixture rather
    than to the behaviour. What must hold is the ordering.
    """
    trending = item_for(evidence_of(BULLISH_DRIFT), EvidenceSource.MARKET_REGIME)
    ranging = item_for(evidence_of(FLAT_DRIFT), EvidenceSource.MARKET_REGIME)

    assert trending.direction is EvidenceDirection.BULLISH
    assert trending.strength.at_least(EvidenceStrength.MODERATE)
    assert not ranging.strength.at_least(EvidenceStrength.MODERATE)
    assert trending.strength.rank > ranging.strength.rank


@pytest.mark.unit
def test_a_strong_regime_grades_strong() -> None:
    """The other side of the same threshold, so both branches are covered."""
    falling = item_for(evidence_of(BEARISH_DRIFT), EvidenceSource.MARKET_REGIME)
    assert falling.strength is EvidenceStrength.STRONG


# ----------------------------------------------------------------------
# Identity, dating and provenance
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_every_timeframe_item_carries_its_timeframe_and_role() -> None:
    items = evidence_of(BULLISH_DRIFT, TimeframeRole.SETUP)
    assert items
    for item in items:
        assert item.timeframe is Timeframe.M15
        assert item.role is TimeframeRole.SETUP


@pytest.mark.unit
def test_point_in_time_items_are_dated_to_the_final_candle() -> None:
    subject = market_view(TimeframeRole.BIAS, drift=BULLISH_DRIFT)
    for item in build_timeframe_evidence(subject):
        if item.point_in_time:
            assert item.confirmed_index == subject.last_index
            assert item.confirmed_time == subject.last_time


@pytest.mark.unit
def test_event_items_are_dated_to_confirmation_not_to_the_price_action() -> None:
    """The Phase 2 causality contract, carried into evidence unchanged.

    A swing that pivoted at candle 100 and confirmed at 102 must produce
    evidence dated 102 - the moment it could first have been known.
    """
    subject = market_view(TimeframeRole.BIAS, drift=BULLISH_DRIFT)
    events = {event.confirmed_index for event in subject.structure.structural_events}
    dated = {
        item.confirmed_index
        for item in build_timeframe_evidence(subject)
        if item.source is EvidenceSource.STRUCTURAL_EVENT
    }
    assert dated
    assert dated <= events


@pytest.mark.unit
def test_evidence_known_at_excludes_what_had_not_confirmed_yet() -> None:
    items = evidence_of(BULLISH_DRIFT)
    cutoff = 40
    known = evidence_known_at(items, cutoff)
    assert known
    for item in known:
        assert item.confirmed_index is not None
        assert item.confirmed_index <= cutoff
    assert len(known) < len(items)


@pytest.mark.unit
def test_generation_is_deterministic() -> None:
    subject = market_view(TimeframeRole.BIAS, drift=BULLISH_DRIFT)
    assert build_timeframe_evidence(subject) == build_timeframe_evidence(subject)


@pytest.mark.unit
def test_directional_filters_out_neutral_and_unavailable() -> None:
    items = evidence_of(FLAT_DRIFT)
    assert all(item.is_directional for item in directional(items))


# ----------------------------------------------------------------------
# Nothing is invented
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_only_sources_backed_by_a_real_engine_exist() -> None:
    """Section 16 requires deterministic evidence.

    There is no member for an LLM, a screenshot, a news feed or a user
    opinion, and nothing in this phase can add one at runtime.
    """
    forbidden = {"CLAUDE", "LLM", "AI", "NEWS", "SCREENSHOT", "VISION", "USER", "SENTIMENT"}
    assert forbidden.isdisjoint({source.name for source in EvidenceSource})


@pytest.mark.unit
def test_an_engine_that_reported_nothing_produces_no_evidence() -> None:
    """A market with no divergence yields no divergence item - not a neutral one."""
    subject = market_view(TimeframeRole.BIAS, drift=FLAT_DRIFT, size=40)
    items = build_timeframe_evidence(subject)
    divergences = [item for item in items if item.source is EvidenceSource.VOLUME_DIVERGENCE]
    assert len(divergences) == len(subject.structure.divergences)


@pytest.mark.unit
def test_unresolved_breakouts_produce_nothing() -> None:
    """A zone under challenge, or a breach still inside its failure window, is
    not yet a fact. Only CONFIRMED and FALSE_BREAKOUT are translated."""
    subject = market_view(TimeframeRole.BIAS, drift=BULLISH_DRIFT)
    resolved = [
        event
        for event in subject.structure.breakout_events
        if event.event_type.value in {"CONFIRMED", "FALSE_BREAKOUT"}
    ]
    breakout_items = [
        item for item in build_timeframe_evidence(subject) if item.source is EvidenceSource.BREAKOUT
    ]
    assert len(breakout_items) == len(resolved)


@pytest.mark.unit
def test_breakout_volume_is_a_separate_item_from_the_breakout() -> None:
    """Counting the same participation twice, once through each door, would
    inflate a single observation into two."""
    items = evidence_of(BULLISH_DRIFT)
    breakouts = [item for item in items if item.source is EvidenceSource.BREAKOUT]
    volumes = [item for item in items if item.source is EvidenceSource.BREAKOUT_VOLUME]
    assert len(volumes) <= len(breakouts)
    for item in volumes:
        assert item.category is EvidenceCategory.VOLUME


# ----------------------------------------------------------------------
# Contract context is context, never direction
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_premium_basis_is_context_and_not_a_bullish_signal() -> None:
    """Master spec section 32 says the basis engine returns no direction, and a
    Phase 3 test enforces it. Promoting PREMIUM to BULLISH here would smuggle
    in the directional claim that phase refused to make.
    """
    items = build_contract_evidence(
        basis=BasisResult(
            context=BasisContext.PREMIUM,
            basis=Decimal("1"),
            basis_ratio=Decimal("0.01"),
            futures_price=Decimal("101"),
            spot_price=Decimal("100"),
            reason="fixture",
        )
    )
    assert len(items) == 1
    assert items[0].direction is EvidenceDirection.NEUTRAL
    assert items[0].category is EvidenceCategory.BASIS


@pytest.mark.unit
def test_open_interest_context_is_not_a_direction() -> None:
    items = build_contract_evidence(
        open_interest=OpenInterestReading(
            context=OpenInterestContext.NEW_LONG_PARTICIPATION,
            price_change=Decimal("2"),
            open_interest_change=Decimal("200"),
            reason="fixture",
        )
    )
    assert items[0].direction is EvidenceDirection.NEUTRAL


@pytest.mark.unit
@pytest.mark.parametrize(
    ("basis_context", "oi_context"),
    ((BasisContext.UNAVAILABLE, OpenInterestContext.INSUFFICIENT_DATA),),
)
def test_unmeasurable_contract_context_reads_unavailable(
    basis_context: BasisContext, oi_context: OpenInterestContext
) -> None:
    items = build_contract_evidence(
        basis=BasisResult(
            context=basis_context,
            basis=None,
            basis_ratio=None,
            futures_price=None,
            spot_price=None,
            reason="fixture",
        ),
        open_interest=OpenInterestReading(
            context=oi_context, price_change=None, open_interest_change=None, reason="fixture"
        ),
    )
    assert {item.direction for item in items} == {EvidenceDirection.UNAVAILABLE}


@pytest.mark.unit
def test_contract_evidence_belongs_to_no_timeframe() -> None:
    items = build_contract_evidence(
        open_interest=OpenInterestReading(
            context=OpenInterestContext.UNCHANGED,
            price_change=Decimal("0"),
            open_interest_change=Decimal("0"),
            reason="fixture",
        )
    )
    assert items[0].timeframe is None
    assert items[0].role is None
    assert items[0].confirmed_index is None


@pytest.mark.unit
def test_omitting_contract_readings_adds_nothing() -> None:
    """Not a neutral placeholder - nothing at all."""
    assert build_contract_evidence() == ()


@pytest.mark.unit
def test_undated_items_are_never_returned_as_known_at_an_index() -> None:
    items = build_contract_evidence(
        open_interest=OpenInterestReading(
            context=OpenInterestContext.UNCHANGED,
            price_change=Decimal("0"),
            open_interest_change=Decimal("0"),
            reason="fixture",
        )
    )
    assert evidence_known_at(items, 10_000) == ()


@pytest.mark.unit
def test_a_fixture_timestamp_is_timezone_aware() -> None:
    """Inherited from the Phase 1 series invariant, asserted here because
    evidence timestamps are compared across timeframes."""
    subject = market_view(TimeframeRole.BIAS, drift=BULLISH_DRIFT, size=60)
    assert subject.last_time.tzinfo is not None
    assert subject.last_time > datetime(2020, 1, 1, tzinfo=UTC)

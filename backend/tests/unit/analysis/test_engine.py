"""The assembled Phase 4A view: composition, reuse and phase boundaries.

Every market here is TEST_FIXTURE data.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.analysis.engine import (
    AnalysisConfig,
    MultiTimeframeAnalysis,
    analyse_multi_timeframe,
)
from app.domain.analysis.evidence import EvidenceSource, evidence_known_at
from app.domain.analysis.generation import EvidenceConfig
from app.domain.analysis.timeframes import (
    ROLES_BROADEST_FIRST,
    MultiTimeframeError,
    MultiTimeframeIssue,
    TimeframeRole,
    TimeframeRolePolicy,
)
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

BASIS = BasisResult(
    context=BasisContext.PREMIUM,
    basis=Decimal("1"),
    basis_ratio=Decimal("0.01"),
    futures_price=Decimal("101"),
    spot_price=Decimal("100"),
    reason="fixture",
)
OPEN_INTEREST = OpenInterestReading(
    context=OpenInterestContext.NEW_LONG_PARTICIPATION,
    price_change=Decimal("2"),
    open_interest_change=Decimal("200"),
    reason="fixture",
)


def full() -> MultiTimeframeAnalysis:
    return analyse_multi_timeframe(
        tuple(market_view(role, drift=BULLISH_DRIFT) for role in ROLES_BROADEST_FIRST)
    )


# ----------------------------------------------------------------------
# Composition
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_analysis_carries_every_role_it_was_given() -> None:
    analysis = full()
    assert analysis.views.is_complete
    assert analysis.contradictions.unavailable_roles == ()
    for role in ROLES_BROADEST_FIRST:
        assert analysis.evidence_for(role)


@pytest.mark.unit
def test_evidence_is_ordered_broadest_role_first() -> None:
    """So a reader meets the regime before the entry timing, and so the order
    is reproducible rather than incidental."""
    ranks = [item.role.rank for item in full().evidence if item.role is not None]
    assert ranks == sorted(ranks)


@pytest.mark.unit
def test_evidence_for_a_role_is_confined_to_that_timeframe() -> None:
    """Indices only mean anything within one timeframe, so this is the unit
    that may be handed to ``evidence_known_at``."""
    analysis = full()
    setup = analysis.evidence_for(TimeframeRole.SETUP)
    assert {item.timeframe for item in setup} == {Timeframe.M15}
    assert evidence_known_at(setup, 10)


@pytest.mark.unit
def test_contract_evidence_is_kept_apart_from_timeframe_evidence() -> None:
    analysis = analyse_multi_timeframe(
        (market_view(TimeframeRole.BIAS, drift=BULLISH_DRIFT),),
        basis=BASIS,
        open_interest=OPEN_INTEREST,
    )
    assert {item.source for item in analysis.contract_evidence} == {
        EvidenceSource.BASIS,
        EvidenceSource.OPEN_INTEREST,
    }
    assert all(item.timeframe is not None for item in analysis.evidence)
    assert analysis.all_evidence == analysis.evidence + analysis.contract_evidence


@pytest.mark.unit
def test_omitting_the_contract_readings_adds_no_placeholder() -> None:
    assert full().contract_evidence == ()


@pytest.mark.unit
def test_the_policy_used_is_reported_back() -> None:
    assert full().policy == TimeframeRolePolicy()


@pytest.mark.unit
def test_a_custom_config_reaches_the_generators() -> None:
    """A zone must be within reach of the close to count; tightening that to
    zero removes the level evidence entirely.

    A *ranging* market is the right fixture here: a market making new highs
    every swing has no zone anywhere near its close, so it produces no level
    evidence at any proximity setting and the config would appear to do
    nothing.
    """
    views = (market_view(TimeframeRole.BIAS, drift=FLAT_DRIFT),)
    tight = analyse_multi_timeframe(
        views, config=AnalysisConfig(evidence=EvidenceConfig(level_proximity_atr=0.0))
    )
    wide = analyse_multi_timeframe(
        views, config=AnalysisConfig(evidence=EvidenceConfig(level_proximity_atr=50.0))
    )

    def levels(analysis: MultiTimeframeAnalysis) -> int:
        return len(
            [item for item in analysis.evidence if item.source is EvidenceSource.SUPPORT_RESISTANCE]
        )

    assert levels(tight) < levels(wide)


# ----------------------------------------------------------------------
# Validation happens before anything is computed
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_an_invalid_set_of_views_is_refused_rather_than_analysed() -> None:
    swapped = view(
        TimeframeRole.ENTRY, zigzag(drift=BULLISH_DRIFT, size=60, timeframe=Timeframe.M15)
    )
    with pytest.raises(MultiTimeframeError) as excinfo:
        analyse_multi_timeframe((swapped,))
    assert excinfo.value.issue is MultiTimeframeIssue.SWAPPED_TIMEFRAME


@pytest.mark.unit
def test_a_partial_set_analyses_and_reports_what_is_missing() -> None:
    analysis = analyse_multi_timeframe(
        (
            market_view(TimeframeRole.REGIME, drift=BULLISH_DRIFT),
            market_view(TimeframeRole.ENTRY, drift=BEARISH_DRIFT),
        )
    )
    assert analysis.contradictions.unavailable_roles == (
        TimeframeRole.BIAS,
        TimeframeRole.SETUP,
    )
    assert analysis.evidence_for(TimeframeRole.BIAS) == ()


# ----------------------------------------------------------------------
# Determinism and phase boundary
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_same_input_produces_an_identical_analysis() -> None:
    views = tuple(market_view(role, drift=BULLISH_DRIFT) for role in ROLES_BROADEST_FIRST)
    assert analyse_multi_timeframe(views) == analyse_multi_timeframe(views)


@pytest.mark.unit
def test_the_analysis_reaches_no_verdict() -> None:
    """Setup quality, entry quality, NO TRADE and the Bull/Bear/Neutral
    scenarios are Phase 4B. Nothing here converts evidence into an action."""
    forbidden = {
        "decision",
        "verdict",
        "recommendation",
        "setup_quality",
        "entry_quality",
        "trade",
        "scenario",
    }
    assert forbidden.isdisjoint(MultiTimeframeAnalysis.__dataclass_fields__)

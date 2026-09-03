"""Assembly against real Phase 1-6 results (§2, §4, §5).

The other context tests build a `SynthesisContext` by hand, which is right for
exercising canonicalisation but proves nothing about `build_synthesis_context`.
This file runs the real engines - the same `analyse_multi_timeframe`,
`score_setup`, `assess_no_trade` the rest of the project uses - and assembles
their output.

Two things it is looking for:

* that assembly **reads** rather than recomputes: every score in the context
  equals the one the engine produced, not a recalculation that happens to agree;
* that the properties the hand-built tests assert survive contact with real
  data - stable digests, ordered references, forming state preserved.

Every market is TEST_FIXTURE data.
"""

from __future__ import annotations

import pytest

from app.application.synthesis.canonical import context_digest
from app.application.synthesis.context import ConfirmationState, build_synthesis_context
from app.domain.analysis.engine import analyse_multi_timeframe
from app.domain.analysis.evidence import EvidenceDirection
from app.domain.analysis.quality import score_setup
from app.domain.analysis.timeframes import ROLES_BROADEST_FIRST
from app.domain.risk.sizing import SizingOutcome
from app.domain.suitability.no_trade import assess_no_trade
from app.domain.synthesis.actions import FinalAction
from app.domain.synthesis.references import ReferenceKind
from tests.factories_analysis import BULLISH_DRIFT, market_view
from tests.factories_synthesis import data_quality, sizing


def real_analysis(drift: float = BULLISH_DRIFT, roles=ROLES_BROADEST_FIRST):  # type: ignore[no-untyped-def]
    return analyse_multi_timeframe(tuple(market_view(role, drift=drift) for role in roles))


def assembled(drift: float = BULLISH_DRIFT, **kwargs: object):  # type: ignore[no-untyped-def]
    analysis = real_analysis(drift)
    direction = EvidenceDirection.BULLISH
    quality = score_setup(analysis.fused, direction)
    verdict = assess_no_trade(analysis, direction)
    return build_synthesis_context(
        analysis,
        direction,
        verdict,
        contradictions=analysis.contradictions,
        setup_quality=quality,
        **kwargs,  # type: ignore[arg-type]
    )


# ----------------------------------------------------------------------
# Assembly reads; it does not recompute
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_setup_quality_carried_is_the_engine_s_own_number() -> None:
    analysis = real_analysis()
    quality = score_setup(analysis.fused, EvidenceDirection.BULLISH)
    context = assembled()

    assert context.setup_quality_score == quality.score
    assert context.setup_quality_label == quality.label
    assert "HEURISTIC" in context.setup_quality_label, "§15: it is not a probability"


@pytest.mark.unit
def test_every_quality_component_is_carried_with_its_awarded_points() -> None:
    analysis = real_analysis()
    quality = score_setup(analysis.fused, EvidenceDirection.BULLISH)
    context = assembled()

    by_name = {item.component: item for item in context.setup_components}
    for score in quality.components:
        carried = by_name[score.component.value]
        assert carried.awarded == score.awarded
        assert carried.weight == score.weight
        assert carried.availability == score.availability.value


@pytest.mark.unit
def test_all_the_evidence_reaches_the_context() -> None:
    analysis = real_analysis()
    context = assembled()

    assert len(context.bull_evidence) + len(context.bear_evidence) + len(
        context.neutral_evidence
    ) == len(analysis.evidence)


@pytest.mark.unit
def test_evidence_keeps_its_direction() -> None:
    context = assembled()
    assert all(item.direction is EvidenceDirection.BULLISH for item in context.bull_evidence)
    assert all(item.direction is EvidenceDirection.BEARISH for item in context.bear_evidence)


@pytest.mark.unit
def test_a_bullish_market_yields_bullish_evidence() -> None:
    context = assembled()
    assert context.bull_evidence, "the fixture market should produce bullish evidence"


# ----------------------------------------------------------------------
# Identifiers and ordering
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_every_reference_is_unique_and_well_formed() -> None:
    context = assembled()
    ids = [ref.ref_id for ref in context.all_refs]

    assert ids
    assert len(ids) == len(set(ids))
    for ref in context.all_refs:
        assert ref.ref_id.startswith(f"{ref.kind.prefix}-")


@pytest.mark.unit
def test_identifiers_are_assigned_in_a_canonical_order() -> None:
    """Numbering follows the sort, so the same facts always number the same."""
    first = assembled()
    second = assembled()
    assert [ref.ref_id for ref in first.all_refs] == [ref.ref_id for ref in second.all_refs]


@pytest.mark.unit
def test_assembling_the_same_market_twice_gives_the_same_digest() -> None:
    assert context_digest(assembled()) == context_digest(assembled())


@pytest.mark.unit
def test_the_digest_is_sensitive_to_the_market() -> None:
    from tests.factories_analysis import BEARISH_DRIFT  # noqa: PLC0415

    assert context_digest(assembled()) != context_digest(assembled(BEARISH_DRIFT))


@pytest.mark.unit
def test_evidence_ordering_does_not_depend_on_view_order() -> None:
    """§5 and §27 probe 20: the same views collected differently hash the same."""
    forward = ROLES_BROADEST_FIRST
    reversed_roles = tuple(reversed(ROLES_BROADEST_FIRST))

    one = assembled()
    analysis = analyse_multi_timeframe(
        tuple(market_view(role, drift=BULLISH_DRIFT) for role in reversed_roles)
    )
    verdict = assess_no_trade(analysis, EvidenceDirection.BULLISH)
    other = build_synthesis_context(
        analysis,
        EvidenceDirection.BULLISH,
        verdict,
        contradictions=analysis.contradictions,
        setup_quality=score_setup(analysis.fused, EvidenceDirection.BULLISH),
    )

    assert [ref.ref_id for ref in one.all_refs] == [ref.ref_id for ref in other.all_refs]
    assert len(forward) == len(reversed_roles)


# ----------------------------------------------------------------------
# The envelope is derived from the real veto
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_envelope_reflects_the_real_assessment() -> None:
    context = assembled()
    assert context.envelope.permits(FinalAction.NO_TRADE)
    assert not context.envelope.permits(FinalAction.SHORT), "only LONG was assessed"


@pytest.mark.unit
def test_real_risk_refusal_removes_the_directional_action() -> None:
    context = assembled(sizing=sizing(SizingOutcome.NOT_PERMITTED))
    assert not context.envelope.permits(FinalAction.LONG)
    assert context.envelope.forced is FinalAction.NO_TRADE
    assert context.sizing_outcome is SizingOutcome.NOT_PERMITTED


@pytest.mark.unit
def test_blocked_data_quality_reaches_the_envelope() -> None:
    from app.domain.market.quality import DataQualityVerdict  # noqa: PLC0415

    context = assembled(data_quality=data_quality(DataQualityVerdict.BLOCKED))
    assert context.envelope.forced is FinalAction.NO_TRADE
    assert context.data_quality_verdict is DataQualityVerdict.BLOCKED


@pytest.mark.unit
def test_a_risk_result_becomes_a_citable_finding() -> None:
    context = assembled(sizing=sizing(SizingOutcome.NOT_PERMITTED))
    assert context.risk_findings
    assert context.risk_findings[0].severity == "BLOCKING"
    assert context.risk_findings[0].ref.kind is ReferenceKind.RISK_FINDING


# ----------------------------------------------------------------------
# §19 / §20 against real data
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_confirmation_state_is_recorded_for_every_piece_of_evidence() -> None:
    context = assembled()
    states = {
        item.confirmation
        for item in (*context.bull_evidence, *context.bear_evidence, *context.neutral_evidence)
    }
    assert states <= set(ConfirmationState)
    assert ConfirmationState.UNKNOWN not in states, "real evidence should classify"


@pytest.mark.unit
def test_point_in_time_evidence_is_not_labelled_confirmed() -> None:
    """A regime reading is true of the latest candle, not settled by a close."""
    analysis = real_analysis()
    context = assembled()

    point_in_time = {item.reason for item in analysis.evidence if item.point_in_time}
    if not point_in_time:
        pytest.skip("this fixture market produced no point-in-time evidence")

    carried = {
        item.reason: item.confirmation
        for item in (*context.bull_evidence, *context.bear_evidence, *context.neutral_evidence)
    }
    for reason in point_in_time:
        assert carried[reason] is ConfirmationState.POINT_IN_TIME


@pytest.mark.unit
def test_missing_requirements_become_explicit_entries() -> None:
    """The veto records what it could not evaluate; assembly must carry it."""
    analysis = real_analysis()
    verdict = assess_no_trade(analysis, EvidenceDirection.BULLISH)
    context = assembled()

    assert len(context.missing) == len(verdict.missing_requirements)
    assert {item.detail for item in context.missing} == set(verdict.missing_requirements)


@pytest.mark.unit
def test_assembly_takes_no_clock_and_derives_the_as_of_from_the_data() -> None:
    """§4: assembly reads no clock, ambient or injected.

    The only time it carries comes from the market data itself, so two runs
    over the same candles produce the same as-of and the same digest however
    much wall-clock time separates them.
    """
    import inspect  # noqa: PLC0415

    parameters = set(inspect.signature(build_synthesis_context).parameters)
    assert "generated_at" not in parameters
    assert "clock" not in parameters

    context = assembled()
    assert context.analysis_as_of is not None
    assert context.analysis_as_of.tzinfo is not None


@pytest.mark.unit
def test_the_as_of_is_the_latest_candle_the_analysis_read() -> None:
    analysis = real_analysis()
    latest = max(
        view.series.candles[-1].open_time for view in analysis.views.views if view.series.candles
    )
    assert assembled().analysis_as_of == latest


@pytest.mark.unit
def test_synthesising_the_same_snapshot_twice_gives_one_digest() -> None:
    """The invariant the digest exists for: identical inputs, identical hash.

    Nothing about *when* a synthesis runs may move it. Wall-clock time passes
    between these two assemblies and the digest does not care.
    """
    assert context_digest(assembled()) == context_digest(assembled())


@pytest.mark.unit
def test_numeric_facts_hold_only_engine_produced_numbers() -> None:
    context = assembled(sizing=sizing())
    names = {fact.name for fact in context.numeric_facts}

    assert "SETUP_QUALITY" in names
    assert "ALLOWED_CONTRACTS" in names
    for fact in context.numeric_facts:
        assert fact.rendered == format(fact.value.normalize(), "f")

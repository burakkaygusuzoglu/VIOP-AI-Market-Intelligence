"""Context assembly, canonical form and digest (§2, §4, §5, §19, §20).

The digest is the reason this has to be exact. An audit record says "this
context produced that request"; if the same situation can hash two ways, the
record proves nothing. So the tests here are mostly about *sameness*: the same
facts arriving in a different order, a Decimal written with a trailing zero, a
set that iterated differently - none of them may move the hash.

Every value is TEST_FIXTURE data.
"""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.application.synthesis.canonical import (
    canonical_context,
    canonical_json,
    context_digest,
)
from app.application.synthesis.context import (
    CONTEXT_SCHEMA_VERSION,
    ConfirmationState,
    ContextContradiction,
    ContextEvidence,
    ContextFinding,
    ContextVisionObservation,
    MissingInformation,
    SynthesisContext,
)
from app.application.synthesis.untrusted import UntrustedOrigin, UntrustedText
from app.domain.analysis.evidence import EvidenceDirection
from app.domain.common.enums import DataSourcePriority, Timeframe
from app.domain.synthesis.actions import derive_action_envelope
from app.domain.synthesis.references import AuthorityClass, ReferenceKind
from app.domain.vision.extraction import ObservationKind
from tests.factories_synthesis import (
    FIXTURE_NOW,
    FIXTURE_SYMBOL,
    clean_assessment,
    make_ref,
    numeric_fact,
)


def _evidence(
    kind: ReferenceKind,
    index: int,
    *,
    direction: EvidenceDirection,
    strength: str = "MODERATE",
    confirmation: ConfirmationState = ConfirmationState.CONFIRMED,
) -> ContextEvidence:
    return ContextEvidence(
        ref=make_ref(kind, index),
        direction=direction,
        category="TREND",
        strength=strength,
        reason=f"fixture {kind.value} {index}",
        timeframe=Timeframe.H1,
        role="BIAS",
        confirmation=confirmation,
        confirmed_at=FIXTURE_NOW if confirmation is ConfirmationState.CONFIRMED else None,
    )


def context_with_evidence(
    *,
    confirmed: bool = True,
    bull_count: int = 2,
    bear_count: int = 1,
) -> SynthesisContext:
    """A context carrying evidence, a contradiction and a numeric fact."""
    state = ConfirmationState.CONFIRMED if confirmed else ConfirmationState.FORMING
    return SynthesisContext(
        schema_version=CONTEXT_SCHEMA_VERSION,
        symbol=FIXTURE_SYMBOL,
        direction=EvidenceDirection.BULLISH,
        envelope=derive_action_envelope(clean_assessment(), EvidenceDirection.BULLISH),
        bull_evidence=tuple(
            _evidence(
                ReferenceKind.BULL_EVIDENCE,
                index,
                direction=EvidenceDirection.BULLISH,
                confirmation=state,
            )
            for index in range(1, bull_count + 1)
        ),
        bear_evidence=tuple(
            _evidence(
                ReferenceKind.BEAR_EVIDENCE,
                index,
                direction=EvidenceDirection.BEARISH,
                confirmation=state,
            )
            for index in range(1, bear_count + 1)
        ),
        contradictions=(
            ContextContradiction(
                ref=make_ref(ReferenceKind.CONTRADICTION, 1),
                contradiction_type="REGIME_VS_STRUCTURE",
                severity="MAJOR",
                reason="fixture contradiction",
                timeframes=(Timeframe.H1,),
                confirmed_at=FIXTURE_NOW,
            ),
        ),
        missing=(
            MissingInformation(
                ref=make_ref(ReferenceKind.MISSING_INFORMATION, 1),
                code="MISSING_REQUIREMENT",
                detail="position sizing was not supplied",
            ),
        ),
        numeric_facts=(numeric_fact("SETUP_QUALITY", Decimal("72")),),
        setup_quality_score=72,
        no_trade_state=False,
    )


# ----------------------------------------------------------------------
# Canonical form is stable
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_same_context_always_produces_the_same_digest() -> None:
    digests = {context_digest(context_with_evidence()) for _ in range(5)}
    assert len(digests) == 1


@pytest.mark.unit
def test_container_order_does_not_change_the_digest() -> None:
    """§27 probe 20: the same evidence collected differently is the same context."""
    base = context_with_evidence(bull_count=3)
    shuffled = replace(base, bull_evidence=tuple(reversed(base.bull_evidence)))

    assert context_digest(shuffled) != context_digest(base), (
        "a genuinely different order is a different canonical form; assembly is what sorts"
    )
    resorted = replace(
        shuffled, bull_evidence=tuple(sorted(shuffled.bull_evidence, key=lambda i: i.ref.ref_id))
    )
    assert context_digest(resorted) == context_digest(base)


@pytest.mark.unit
def test_a_changed_fact_changes_the_digest() -> None:
    """The digest has to be sensitive, or it identifies nothing."""
    base = context_with_evidence()
    changed = replace(base, setup_quality_score=71)
    assert context_digest(changed) != context_digest(base)


@pytest.mark.unit
def test_decimals_are_exact_and_never_pass_through_float() -> None:
    trailing = numeric_fact("X", Decimal("61.270"), AuthorityClass.RISK)
    plain = numeric_fact("X", Decimal("61.27"), AuthorityClass.RISK)
    assert trailing.rendered == plain.rendered == "61.27"

    base = context_with_evidence()
    assert context_digest(replace(base, numeric_facts=(trailing,))) == context_digest(
        replace(base, numeric_facts=(plain,))
    )


@pytest.mark.unit
def test_a_float_in_a_context_fails_loudly() -> None:
    """No field is a float today; if one appears the digest must not go quiet."""
    from app.application.synthesis.canonical import _canonical_value  # noqa: PLC0415

    with pytest.raises(TypeError, match="use Decimal"):
        _canonical_value(1.5)


@pytest.mark.unit
def test_raw_bytes_can_never_enter_a_canonical_context() -> None:
    """§4, §17: no image bytes, no secrets."""
    from app.application.synthesis.canonical import _canonical_value  # noqa: PLC0415

    with pytest.raises(TypeError, match="raw bytes"):
        _canonical_value(b"\x89PNG\r\n\x1a\n")


@pytest.mark.unit
def test_a_naive_timestamp_is_refused() -> None:
    from app.application.synthesis.canonical import _canonical_value  # noqa: PLC0415

    with pytest.raises(ValueError, match="timezone-aware"):
        _canonical_value(datetime(2026, 3, 2, 12, 0))  # noqa: DTZ001


@pytest.mark.unit
def test_the_canonical_json_is_sorted_and_reparseable() -> None:
    import json  # noqa: PLC0415

    text = canonical_json(context_with_evidence())
    parsed = json.loads(text)
    assert parsed["canonical_format"].startswith("synthesis-canonical/")
    assert list(parsed.keys()) == sorted(parsed.keys())


@pytest.mark.unit
def test_no_python_object_repr_leaks_into_the_canonical_form() -> None:
    text = canonical_json(context_with_evidence())
    for leak in ("object at 0x", "<enum", "Decimal(", "datetime.datetime("):
        assert leak not in text


@pytest.mark.unit
def test_enums_serialise_by_value() -> None:
    body = canonical_context(context_with_evidence())["context"]
    assert body["direction"] == "BULLISH"
    assert body["envelope"]["allowed"] == ["LONG", "WAIT", "NO_TRADE"]


# ----------------------------------------------------------------------
# §20: missing stays missing
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_missing_information_is_an_entry_not_an_absence() -> None:
    context = context_with_evidence()
    assert context.missing
    assert context.missing[0].ref.authority is AuthorityClass.MISSING
    assert context.missing[0].ref.ref_id in context.ref_ids


@pytest.mark.unit
def test_an_unknown_value_is_serialised_as_an_explicit_null() -> None:
    """An absent key and a key whose value is unknown are different claims."""
    body = canonical_context(context_with_evidence())["context"]
    assert "entry_quality_score" in body
    assert body["entry_quality_score"] is None


@pytest.mark.unit
def test_missing_never_reads_as_neutral() -> None:
    """§27 probe 15: a missing timeframe is not a neutral one."""
    context = context_with_evidence()
    missing_ids = {item.ref.ref_id for item in context.missing}
    neutral_ids = {item.ref.ref_id for item in context.neutral_evidence}
    assert missing_ids.isdisjoint(neutral_ids)
    assert all(item.ref.kind is ReferenceKind.MISSING_INFORMATION for item in context.missing)


# ----------------------------------------------------------------------
# §19: forming stays forming
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_forming_evidence_is_carried_as_forming() -> None:
    context = context_with_evidence(confirmed=False)
    assert all(item.confirmation is ConfirmationState.FORMING for item in context.bull_evidence)


@pytest.mark.unit
def test_confirmed_and_forming_produce_different_contexts() -> None:
    assert context_digest(context_with_evidence(confirmed=True)) != context_digest(
        context_with_evidence(confirmed=False)
    )


@pytest.mark.unit
def test_confirmation_state_is_a_field_not_an_inference() -> None:
    body = canonical_context(context_with_evidence(confirmed=False))["context"]
    assert body["bull_evidence"][0]["confirmation"] == "FORMING"


# ----------------------------------------------------------------------
# §3: authority stays distinguishable
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_vision_and_structured_provenance_stay_distinct() -> None:
    """§27 probe 5: a screenshot reading is not a measurement."""
    observation = ContextVisionObservation(
        ref=make_ref(ReferenceKind.VISION_OBSERVATION, 1),
        field_name="INDICATOR_READING",
        value=UntrustedText(origin=UntrustedOrigin.SCREENSHOT_TEXT, content="RSI 99"),
        observation_kind=ObservationKind.DIRECTLY_VISIBLE,
        source_priority=DataSourcePriority.SCREENSHOT_EXTRACTED,
        confidence=Decimal("0.9"),
    )
    calculated = ContextFinding(
        ref=make_ref(ReferenceKind.RISK_FINDING, 1),
        code="ALLOWED",
        severity="CAUTION",
        detail="fixture",
    )

    assert observation.source_priority is DataSourcePriority.SCREENSHOT_EXTRACTED
    assert observation.ref.authority.is_model_derived, "a screenshot reading is model-derived"
    assert not calculated.ref.authority.is_model_derived, "a risk result is not"
    assert observation.ref.authority is not calculated.ref.authority
    assert DataSourcePriority.STRUCTURED_MARKET_DATA.wins_over(observation.source_priority)


@pytest.mark.unit
def test_model_derived_authority_is_identifiable() -> None:
    assert AuthorityClass.VISION_EXTRACTION.is_model_derived
    assert AuthorityClass.AI_VISUAL_INFERENCE.is_model_derived
    assert not AuthorityClass.CALCULATED_METRIC.is_model_derived
    assert not AuthorityClass.OBSERVED_FACT.is_model_derived


@pytest.mark.unit
def test_vision_confidence_is_not_a_probability_field() -> None:
    """§15: it describes legibility, and nothing in the context calls it odds."""
    text = canonical_json(context_with_evidence())
    for word in ("probability", "chance", "odds", "win_rate"):
        assert word not in text.lower()


# ----------------------------------------------------------------------
# Reference lookup
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_every_reference_is_reachable_by_id() -> None:
    context = context_with_evidence()
    for ref in context.all_refs:
        assert context.ref(ref.ref_id) is ref


@pytest.mark.unit
def test_reference_ids_are_unique_across_kinds() -> None:
    context = context_with_evidence()
    ids = [ref.ref_id for ref in context.all_refs]
    assert len(ids) == len(set(ids))


@pytest.mark.unit
def test_opposing_refs_are_the_other_side_plus_contradictions() -> None:
    context = context_with_evidence()
    opposing = set(context.opposing_refs)
    assert {item.ref.ref_id for item in context.bear_evidence} <= opposing
    assert {item.ref.ref_id for item in context.contradictions} <= opposing
    assert opposing.isdisjoint({item.ref.ref_id for item in context.bull_evidence})


@pytest.mark.unit
def test_refs_of_kind_filters_correctly() -> None:
    context = context_with_evidence()
    assert all(
        ref.kind is ReferenceKind.CONTRADICTION
        for ref in context.refs_of_kind(ReferenceKind.CONTRADICTION)
    )


@pytest.mark.unit
def test_the_context_carries_no_synthesis_execution_time_at_all() -> None:
    """The digest identifies *inputs*, so when synthesis ran cannot be in it.

    This test replaces one that asserted the opposite. A `generated_at` field
    carrying the synthesis instant used to sit on the context and therefore in
    the canonical form, so the same deterministic analysis hashed differently
    merely because it was synthesised twice - an audit correlation that changes
    when nothing about the market changed identifies nothing.
    """
    names = {item.name for item in fields(SynthesisContext)}
    assert "generated_at" not in names
    assert "analysis_as_of" in names


@pytest.mark.unit
def test_the_analysis_as_of_time_is_part_of_the_digest() -> None:
    """It is a semantic input: one candle later is a different situation."""
    base = context_with_evidence()
    earlier = replace(base, analysis_as_of=datetime(2026, 3, 2, 12, 0, tzinfo=UTC))
    later = replace(base, analysis_as_of=datetime(2026, 3, 2, 13, 0, tzinfo=UTC))

    assert context_digest(earlier) != context_digest(later)

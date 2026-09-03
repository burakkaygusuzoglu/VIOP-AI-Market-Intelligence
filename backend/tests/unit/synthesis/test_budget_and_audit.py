"""Context trimming and audit metadata (§18, §4, §22, §24).

Truncation is where safety information disappears quietly: the prompt is too
long, the tail gets trimmed, and the tail held the risk blocker. Nothing errors
and the synthesis reads confidently. So the tests here mostly assert what
*survives*, and that the module refuses rather than guesses when it cannot make
room.

Every value is TEST_FIXTURE data.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.application.synthesis.audit import (
    MODEL_PLACEHOLDER,
    PROMPT_VERSION_PLACEHOLDER,
    build_audit_record,
)
from app.application.synthesis.budget import (
    ContextBudget,
    ContextBudgetExceededError,
    fit_to_budget,
    mandatory_entries,
)
from app.application.synthesis.canonical import context_digest
from app.application.synthesis.draft import (
    SynthesisDraft,
    SynthesisOutcome,
    SynthesisStatus,
)
from app.domain.synthesis.actions import FinalAction
from app.domain.synthesis.references import ReferenceKind
from tests.factories_synthesis import draft, make_ref, minimal_context
from tests.unit.synthesis.test_context import context_with_evidence


def _blocking_context(bull_count: int = 30):  # type: ignore[no-untyped-def]
    """A context with a blocker, missing information and a lot of evidence."""
    from app.application.synthesis.context import ContextFinding  # noqa: PLC0415

    base = context_with_evidence(bull_count=bull_count)
    return replace(
        base,
        suitability_findings=(
            ContextFinding(
                ref=make_ref(ReferenceKind.SUITABILITY_FINDING, 1),
                code="RISK_NOT_PERMITTED",
                severity="BLOCKING",
                detail="sizing permits zero contracts",
            ),
        ),
    )


# ----------------------------------------------------------------------
# What may never be dropped
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_blockers_missing_information_and_major_contradictions_are_mandatory() -> None:
    context = _blocking_context()
    mandatory = set(mandatory_entries(context))

    assert context.suitability_findings[0].ref.ref_id in mandatory
    assert context.missing[0].ref.ref_id in mandatory
    assert context.contradictions[0].ref.ref_id in mandatory


@pytest.mark.unit
def test_a_safety_blocker_survives_aggressive_trimming() -> None:
    """§27 probe 17: the blocker must not be what makes room for the prose."""
    context = _blocking_context(bull_count=40)
    result = fit_to_budget(
        context,
        ContextBudget(max_entries=10, keep_neutral_evidence=False, keep_vision_observations=False),
    )

    surviving = set(result.context.ref_ids)
    assert context.suitability_findings[0].ref.ref_id in surviving
    assert context.missing[0].ref.ref_id in surviving
    assert context.contradictions[0].ref.ref_id in surviving
    assert result.was_trimmed


@pytest.mark.unit
def test_trimming_records_exactly_what_left() -> None:
    """A shorter context must be visibly a decision, not an unexplained gap."""
    context = _blocking_context(bull_count=40)
    result = fit_to_budget(context, ContextBudget(max_entries=10))

    assert result.removed
    assert set(result.removed).isdisjoint(set(result.context.ref_ids))
    assert set(result.removed) <= set(context.ref_ids)


@pytest.mark.unit
def test_a_context_that_already_fits_is_returned_untouched() -> None:
    context = context_with_evidence()
    result = fit_to_budget(context, ContextBudget(max_entries=100))

    assert not result.was_trimmed
    assert context_digest(result.context) == context_digest(context)


@pytest.mark.unit
def test_mandatory_context_larger_than_the_budget_fails_explicitly() -> None:
    """§18: fail rather than drop a blocker to fit a prompt."""
    context = _blocking_context(bull_count=2)
    with pytest.raises(ContextBudgetExceededError) as excinfo:
        fit_to_budget(context, ContextBudget(max_entries=1))

    assert excinfo.value.budget == 1
    assert "cannot be dropped" in excinfo.value.detail


@pytest.mark.unit
def test_the_weakest_supporting_evidence_goes_first() -> None:
    """And evidence against the reading is not a trim candidate at all."""
    context = _blocking_context(bull_count=30)
    result = fit_to_budget(context, ContextBudget(max_entries=12))

    bear_ids = {item.ref.ref_id for item in context.bear_evidence}
    assert bear_ids.isdisjoint(set(result.removed)), (
        "counter-evidence is what a Devil's Advocate needs; it is not spare capacity"
    )


@pytest.mark.unit
def test_trimming_is_deterministic() -> None:
    """§18: no dependence on unordered iteration."""
    context = _blocking_context(bull_count=30)
    budget = ContextBudget(max_entries=12)
    results = {fit_to_budget(context, budget).removed for _ in range(5)}
    assert len(results) == 1


@pytest.mark.unit
def test_a_major_contradiction_is_never_trimmed_even_under_pressure() -> None:
    context = _blocking_context(bull_count=40)
    result = fit_to_budget(context, ContextBudget(max_entries=8))
    assert context.contradictions[0].ref.ref_id in set(result.context.ref_ids)


@pytest.mark.unit
def test_a_budget_must_allow_at_least_one_entry() -> None:
    with pytest.raises(ValueError, match="at least one entry"):
        ContextBudget(max_entries=0)


# ----------------------------------------------------------------------
# §22: provider failure is not a market opinion
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "status",
    (
        SynthesisStatus.INVALID_OUTPUT,
        SynthesisStatus.PROVIDER_FAILURE,
        SynthesisStatus.NOT_CONFIGURED,
    ),
)
def test_a_failed_attempt_carries_no_draft_and_no_action(status: SynthesisStatus) -> None:
    """§27 probe 18: a timeout is not NO_TRADE and not WAIT."""
    outcome = SynthesisOutcome(status=status, detail="fixture failure")

    assert outcome.draft is None
    assert not status.produced_synthesis
    assert status.value not in {item.value for item in FinalAction}


@pytest.mark.unit
def test_a_failure_status_may_not_smuggle_a_draft() -> None:
    with pytest.raises(ValueError, match="must not carry a draft"):
        SynthesisOutcome(status=SynthesisStatus.PROVIDER_FAILURE, draft=draft())


@pytest.mark.unit
def test_a_success_without_a_draft_is_impossible() -> None:
    with pytest.raises(ValueError, match="must carry a draft"):
        SynthesisOutcome(status=SynthesisStatus.SUCCESS)


@pytest.mark.unit
def test_no_synthesis_status_is_named_after_a_market_state() -> None:
    """The two vocabularies must not overlap, or they will be confused."""
    statuses = {item.value for item in SynthesisStatus}
    actions = {item.value for item in FinalAction}
    assert statuses.isdisjoint(actions)


@pytest.mark.unit
def test_a_deterministic_envelope_still_exists_when_synthesis_fails() -> None:
    """The honest fallback: refuse to synthesise without inventing a view."""
    context = minimal_context()
    outcome = SynthesisOutcome(status=SynthesisStatus.PROVIDER_FAILURE, detail="timeout")

    assert outcome.draft is None
    assert context.envelope.allowed, "the deterministic answer never needed a model"


# ----------------------------------------------------------------------
# §4 / §24: audit metadata
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_an_audit_record_identifies_the_context_that_produced_it() -> None:
    context = context_with_evidence()
    record = build_audit_record(context, SynthesisStatus.SUCCESS, proposed_action=FinalAction.WAIT)

    assert record.context_digest == context_digest(context)
    assert record.context_schema_version.startswith("synthesis-context/")
    assert record.output_schema_version.startswith("synthesis-output/")
    assert record.reference_ids == tuple(sorted(context.ref_ids))


@pytest.mark.unit
def test_a_failed_attempt_is_recorded_as_a_failure_not_lost() -> None:
    record = build_audit_record(
        context_with_evidence(),
        SynthesisStatus.PROVIDER_FAILURE,
        rejection_codes=("TIMEOUT",),
    )
    assert record.status is SynthesisStatus.PROVIDER_FAILURE
    assert record.accepted_action is None


@pytest.mark.unit
def test_the_missing_manifest_lists_gaps_explicitly() -> None:
    record = build_audit_record(
        context_with_evidence(), SynthesisStatus.SUCCESS, proposed_action=FinalAction.WAIT
    )
    assert record.missing_manifest == ("position sizing was not supplied",)


@pytest.mark.unit
def test_the_record_is_honest_that_no_prompt_or_model_exists_yet() -> None:
    """§24: 7A calls nothing, and an empty string would read as data loss."""
    record = build_audit_record(context_with_evidence(), SynthesisStatus.NOT_CONFIGURED)

    assert record.prompt_version == PROMPT_VERSION_PLACEHOLDER
    assert record.model == MODEL_PLACEHOLDER
    assert not record.is_reproducible_input, "input reproducibility requires a real prompt version"


@pytest.mark.unit
def test_reproducibility_is_claimed_for_the_input_only() -> None:
    """§24: no claim of bit-for-bit reproducible model prose anywhere."""
    record = build_audit_record(
        context_with_evidence(),
        SynthesisStatus.SUCCESS,
        proposed_action=FinalAction.WAIT,
        prompt_version="synthesis-prompt/1",
        model="fixture-model",
    )
    assert record.is_reproducible_input
    assert not hasattr(record, "is_reproducible_output")


@pytest.mark.unit
def test_usage_is_never_fabricated_when_a_provider_omits_it() -> None:
    record = build_audit_record(context_with_evidence(), SynthesisStatus.PROVIDER_FAILURE)
    assert record.input_tokens is None
    assert record.output_tokens is None


@pytest.mark.unit
def test_the_record_carries_no_secret_and_no_image() -> None:
    import json  # noqa: PLC0415
    from dataclasses import asdict  # noqa: PLC0415

    record = build_audit_record(
        context_with_evidence(), SynthesisStatus.SUCCESS, proposed_action=FinalAction.WAIT
    )
    rendered = json.dumps(asdict(record), default=str)

    for forbidden in ("sk-ant", "api_key", "Authorization", "iVBORw0", "\\x89PNG"):
        assert forbidden not in rendered


@pytest.mark.unit
def test_the_record_states_the_risk_and_data_quality_it_saw() -> None:
    record = build_audit_record(
        context_with_evidence(), SynthesisStatus.SUCCESS, proposed_action=FinalAction.WAIT
    )
    assert record.risk_state == "NOT_SUPPLIED"
    assert record.data_quality_state == "NOT_SUPPLIED"
    assert record.suitability_state == "False"


@pytest.mark.unit
def test_the_draft_reports_every_reference_it_cited() -> None:
    candidate: SynthesisDraft = draft(
        supporting_refs=("EV-BULL-001",),
        opposing_refs=("EV-BEAR-001",),
        missing_refs=("MISS-001",),
    )
    assert set(candidate.all_referenced_ids) >= {"EV-BULL-001", "EV-BEAR-001", "MISS-001"}

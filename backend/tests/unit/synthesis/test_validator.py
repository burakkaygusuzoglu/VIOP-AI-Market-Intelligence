"""The deterministic synthesis validator (§9, §13, §14, §15, §21).

Everything here is adversarial. Each test constructs a proposal a model might
plausibly produce and asserts the validator refuses it - because the point of
this layer is not that a well-behaved model passes, but that a badly behaved
one cannot get through.

The most important assertion in the file is the simplest: a rejected draft
cannot be turned into an accepted one. `ValidationReport.accept` is the only
door, and it raises rather than returning something usable.

Every value is TEST_FIXTURE data.
"""

from __future__ import annotations

import pytest

from app.application.synthesis.context import SynthesisContext
from app.application.synthesis.draft import (
    DevilsAdvocate,
    ScenarioNarrative,
    SynthesisDraft,
)
from app.application.synthesis.validator import (
    RejectionCode,
    accepted_action,
    validate_synthesis,
)
from app.domain.analysis.evidence import EvidenceDirection
from app.domain.analysis.scenarios import ScenarioCase
from app.domain.risk.sizing import SizingOutcome
from app.domain.synthesis.actions import FinalAction
from app.domain.synthesis.references import AuthorityClass, ContextRef, ReferenceKind, make_ref_id
from tests.factories_synthesis import (
    blocking_assessment,
    draft,
    minimal_context,
    pending_assessment,
    sizing,
)
from tests.unit.synthesis.test_context import context_with_evidence


def validate(candidate: SynthesisDraft, context: SynthesisContext):  # type: ignore[no-untyped-def]
    return validate_synthesis(candidate, context)


# ----------------------------------------------------------------------
# The four valid shapes
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_permitted_long_is_accepted() -> None:
    context = minimal_context(direction=EvidenceDirection.BULLISH)
    report = validate(draft(action=FinalAction.LONG), context)
    assert report.is_valid, report.rejections
    assert report.accept(draft(action=FinalAction.LONG)).proposed_action is FinalAction.LONG


@pytest.mark.unit
def test_a_permitted_short_is_accepted() -> None:
    context = minimal_context(direction=EvidenceDirection.BEARISH)
    assert validate(draft(action=FinalAction.SHORT), context).is_valid


@pytest.mark.unit
def test_a_permitted_wait_is_accepted() -> None:
    context = minimal_context(no_trade_assessment=pending_assessment())
    assert validate(draft(action=FinalAction.WAIT), context).is_valid


@pytest.mark.unit
def test_no_trade_is_accepted_in_every_context() -> None:
    for context in (
        minimal_context(),
        minimal_context(no_trade_assessment=pending_assessment()),
        minimal_context(no_trade_assessment=blocking_assessment()),
    ):
        assert validate(draft(action=FinalAction.NO_TRADE), context).is_valid


# ----------------------------------------------------------------------
# §9: the envelope is not negotiable
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_long_outside_the_envelope_is_rejected() -> None:
    """§27 probe 1: LONG proposed while max contracts is zero."""
    context = minimal_context(position_sizing=sizing(SizingOutcome.NOT_PERMITTED))
    report = validate(draft(action=FinalAction.LONG), context)

    assert not report.is_valid
    assert RejectionCode.ACTION_NOT_PERMITTED in report.codes


@pytest.mark.unit
def test_a_short_while_risk_refuses_is_rejected() -> None:
    """§27 probe 2."""
    context = minimal_context(
        direction=EvidenceDirection.BEARISH,
        position_sizing=sizing(SizingOutcome.NOT_PERMITTED),
    )
    assert (
        RejectionCode.ACTION_NOT_PERMITTED
        in validate(draft(action=FinalAction.SHORT), context).codes
    )


@pytest.mark.unit
def test_a_rejected_action_is_never_silently_upgraded_or_downgraded() -> None:
    """§9: the answer is refusal, not adjustment to something permitted."""
    context = minimal_context(no_trade_assessment=blocking_assessment())
    candidate = draft(action=FinalAction.LONG)
    report = validate(candidate, context)

    assert not report.is_valid
    assert accepted_action(candidate, report) is None
    with pytest.raises(ValueError, match="rejected synthesis cannot be accepted"):
        report.accept(candidate)


@pytest.mark.unit
def test_a_wait_proposed_against_a_hard_blocker_is_rejected() -> None:
    """§10 from the other side: waiting is not available when it cannot help."""
    context = minimal_context(no_trade_assessment=blocking_assessment())
    assert (
        RejectionCode.ACTION_NOT_PERMITTED
        in validate(draft(action=FinalAction.WAIT), context).codes
    )


@pytest.mark.unit
def test_the_rejection_explains_what_was_allowed_instead() -> None:
    context = minimal_context(no_trade_assessment=blocking_assessment())
    detail = validate(draft(action=FinalAction.LONG), context).rejections[0].detail
    assert "NO_TRADE" in detail
    assert "RISK_NOT_PERMITTED" in detail


# ----------------------------------------------------------------------
# §6: evidence must exist
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_an_unknown_evidence_id_is_rejected() -> None:
    """§27 probe 7: a hallucinated identifier invalidates the whole output."""
    context = context_with_evidence()
    report = validate(draft(action=FinalAction.WAIT, supporting_refs=("EV-BULL-999",)), context)
    assert RejectionCode.UNKNOWN_REFERENCE in report.codes


@pytest.mark.unit
def test_a_known_evidence_id_is_accepted() -> None:
    context = context_with_evidence()
    known = context.bull_evidence[0].ref.ref_id
    assert validate(draft(action=FinalAction.WAIT, supporting_refs=(known,)), context).is_valid


@pytest.mark.unit
def test_a_reference_of_the_wrong_kind_is_rejected() -> None:
    """A contradiction is not a piece of missing information."""
    context = context_with_evidence()
    contradiction_ref = context.contradictions[0].ref.ref_id
    report = validate(draft(action=FinalAction.WAIT, missing_refs=(contradiction_ref,)), context)
    assert RejectionCode.WRONG_REFERENCE_KIND in report.codes


@pytest.mark.unit
def test_every_reference_list_is_checked_not_only_the_first() -> None:
    context = context_with_evidence()
    report = validate(
        draft(
            action=FinalAction.WAIT,
            supporting_refs=("EV-BULL-900",),
            invalidation_refs=("CON-900",),
            confirmation_refs=("SUIT-900",),
        ),
        context,
    )
    unknown = [item for item in report.rejections if item.code is RejectionCode.UNKNOWN_REFERENCE]
    assert len(unknown) == 3


# ----------------------------------------------------------------------
# §13: the Devil's Advocate
# ----------------------------------------------------------------------


def advocate_draft(**kwargs: object) -> SynthesisDraft:
    base = draft(action=FinalAction.WAIT)
    return SynthesisDraft(
        proposed_action=base.proposed_action,
        summary=base.summary,
        bull=base.bull,
        bear=base.bear,
        neutral=base.neutral,
        devils_advocate=DevilsAdvocate(**kwargs),  # type: ignore[arg-type]
    )


@pytest.mark.unit
def test_an_empty_devils_advocate_is_rejected() -> None:
    report = validate(advocate_draft(challenge="   ", evidence_is_limited=True), minimal_context())
    assert RejectionCode.MISSING_DEVILS_ADVOCATE in report.codes


@pytest.mark.unit
def test_a_challenge_that_cites_nothing_and_admits_nothing_is_rejected() -> None:
    """An objection with no basis is a fabrication, not an analysis."""
    report = validate(
        advocate_draft(challenge="This might be wrong.", evidence_is_limited=False),
        minimal_context(),
    )
    assert RejectionCode.FABRICATED_COUNTER_EVIDENCE in report.codes


@pytest.mark.unit
def test_declaring_evidence_limited_while_citing_evidence_is_rejected() -> None:
    context = context_with_evidence()
    report = validate(
        advocate_draft(
            challenge="Limited, yet here are five.",
            opposing_refs=(context.bear_evidence[0].ref.ref_id,),
            evidence_is_limited=True,
        ),
        context,
    )
    assert RejectionCode.FABRICATED_COUNTER_EVIDENCE in report.codes


@pytest.mark.unit
def test_citing_counter_evidence_a_context_does_not_hold_is_rejected() -> None:
    """§13: it must not invent an objection when none exists."""
    context = minimal_context()  # no evidence at all
    report = validate(
        advocate_draft(
            challenge="The bears have a point.",
            opposing_refs=("EV-BEAR-001",),
            evidence_is_limited=False,
        ),
        context,
    )
    assert RejectionCode.UNKNOWN_REFERENCE in report.codes


@pytest.mark.unit
def test_an_honest_declaration_of_limited_evidence_is_accepted() -> None:
    """The correct answer when there genuinely is no counter-case."""
    report = validate(
        advocate_draft(
            challenge="The context holds no evidence against this reading.",
            evidence_is_limited=True,
        ),
        minimal_context(),
    )
    assert report.is_valid, report.rejections


@pytest.mark.unit
def test_a_challenge_citing_real_opposing_evidence_is_accepted() -> None:
    context = context_with_evidence()
    report = validate(
        advocate_draft(
            challenge="The bearish structure reading is unresolved.",
            opposing_refs=(context.bear_evidence[0].ref.ref_id,),
            contradiction_refs=(context.contradictions[0].ref.ref_id,),
            evidence_is_limited=False,
        ),
        context,
    )
    assert report.is_valid, report.rejections


# ----------------------------------------------------------------------
# §12: the three narratives
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("case", (ScenarioCase.BULL, ScenarioCase.BEAR, ScenarioCase.NEUTRAL))
def test_an_empty_scenario_narrative_is_rejected(case: ScenarioCase) -> None:
    base = draft(action=FinalAction.WAIT)
    blank = ScenarioNarrative(case=case, narrative="   ")
    candidate = SynthesisDraft(
        proposed_action=base.proposed_action,
        summary=base.summary,
        bull=blank if case is ScenarioCase.BULL else base.bull,
        bear=blank if case is ScenarioCase.BEAR else base.bear,
        neutral=blank if case is ScenarioCase.NEUTRAL else base.neutral,
        devils_advocate=base.devils_advocate,
    )
    assert RejectionCode.MISSING_SCENARIO in validate(candidate, minimal_context()).codes


@pytest.mark.unit
def test_all_three_narratives_are_present_on_a_valid_draft() -> None:
    cases = {item.case for item in draft().narratives}
    assert cases == {ScenarioCase.BULL, ScenarioCase.BEAR, ScenarioCase.NEUTRAL}


# ----------------------------------------------------------------------
# §14 / §15: invented numbers and probability language
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    (
        "There is an 83% chance of success on this setup.",
        "Probability of profit is high at 70%.",
        "%75 ihtimalle yükselir.",
        "Kazanma olasılığı yüksek.",
        "The win rate for this pattern is strong.",
    ),
)
def test_a_probability_claim_is_rejected(text: str) -> None:
    """§27 probes 12-14: this project has no calibrated probability model."""
    report = validate(draft(action=FinalAction.WAIT, summary=text), minimal_context())
    assert RejectionCode.PROBABILITY_CLAIM in report.codes


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    (
        "Support sits at 61.27 and should hold.",
        "Place the stop at 58.40.",
        "The target is 71.85.",
        "The contract multiplier is 12.5.",
    ),
)
def test_an_invented_market_number_is_rejected(text: str) -> None:
    """§27 probes 8-11: a figure no engine produced is an invention."""
    report = validate(draft(action=FinalAction.WAIT, summary=text), minimal_context())
    assert RejectionCode.UNSUPPORTED_NUMERIC_CLAIM in report.codes


@pytest.mark.unit
def test_a_number_the_context_actually_holds_is_accepted() -> None:
    """The alternative to inventing is citing, and citing must work."""
    context = context_with_evidence()
    allowed = next(iter(context.allowed_numeric_strings))
    report = validate(
        draft(action=FinalAction.WAIT, summary=f"The setup quality is {allowed}."), context
    )
    assert RejectionCode.UNSUPPORTED_NUMERIC_CLAIM not in report.codes


@pytest.mark.unit
def test_timeframe_labels_and_reference_ids_are_not_numeric_claims() -> None:
    """Vocabulary must not be mistaken for market figures."""
    context = context_with_evidence()
    known = context.bull_evidence[0].ref.ref_id
    text = f"On 1D and 15M the {known} reading holds."
    report = validate(draft(action=FinalAction.WAIT, summary=text), context)
    assert RejectionCode.UNSUPPORTED_NUMERIC_CLAIM not in report.codes


@pytest.mark.unit
def test_an_indicator_reading_written_as_digits_is_no_longer_exempt() -> None:
    """`RSI 99` used to pass as if it were a period like `RSI 14`.

    The two are lexically indistinguishable, so the blanket exemption for
    indicator periods was removed rather than made cleverer: a reading belongs
    in a `{{FACT-…}}` reference, and this scan is the net behind that.
    """
    report = validate(
        draft(action=FinalAction.WAIT, summary="RSI 99 seviyesinde."), minimal_context()
    )
    assert RejectionCode.UNSUPPORTED_NUMERIC_CLAIM in report.codes


@pytest.mark.unit
def test_narrative_scanning_covers_every_section_not_only_the_summary() -> None:
    context = minimal_context()
    report = validate(
        draft(action=FinalAction.WAIT, bear_text="Resistance at 71.85 caps this."), context
    )
    assert RejectionCode.UNSUPPORTED_NUMERIC_CLAIM in report.codes


# ----------------------------------------------------------------------
# §21: invented risk permission, upgraded confirmation
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    (
        "The risk is acceptable here so size normally.",
        "You may size up beyond the limit.",
        "Ignore the risk engine on this one.",
    ),
)
def test_a_synthesis_may_not_grant_risk_permission(text: str) -> None:
    report = validate(draft(action=FinalAction.WAIT, challenge=text), minimal_context())
    assert RejectionCode.INVENTED_RISK_PERMISSION in report.codes


@pytest.mark.unit
def test_a_forming_observation_may_not_be_narrated_as_confirmed() -> None:
    """§19 and §27 probe 16."""
    context = context_with_evidence(confirmed=False)
    report = validate(
        draft(action=FinalAction.WAIT, summary="The breakout is now confirmed."), context
    )
    assert RejectionCode.CONFIRMATION_UPGRADED in report.codes


@pytest.mark.unit
def test_confirmed_language_is_allowed_when_evidence_really_is_confirmed() -> None:
    context = context_with_evidence(confirmed=True)
    report = validate(
        draft(action=FinalAction.WAIT, summary="The breakout is confirmed by the close."),
        context,
    )
    assert RejectionCode.CONFIRMATION_UPGRADED not in report.codes


# ----------------------------------------------------------------------
# Reporting behaviour
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_every_problem_is_reported_not_only_the_first() -> None:
    context = minimal_context(no_trade_assessment=blocking_assessment())
    report = validate(
        draft(
            action=FinalAction.LONG,
            supporting_refs=("EV-BULL-404",),
            summary="An 83% chance of success at 61.27.",
        ),
        context,
    )
    assert {
        RejectionCode.ACTION_NOT_PERMITTED,
        RejectionCode.UNKNOWN_REFERENCE,
        RejectionCode.PROBABILITY_CLAIM,
        RejectionCode.UNSUPPORTED_NUMERIC_CLAIM,
    } <= set(report.codes)


@pytest.mark.unit
def test_an_empty_summary_is_rejected() -> None:
    report = validate(draft(action=FinalAction.WAIT, summary="   "), minimal_context())
    assert RejectionCode.EMPTY_NARRATIVE in report.codes


@pytest.mark.unit
def test_a_valid_report_returns_the_action_and_an_invalid_one_returns_none() -> None:
    context = minimal_context()
    good = draft(action=FinalAction.LONG)
    assert accepted_action(good, validate(good, context)) is FinalAction.LONG

    bad = draft(action=FinalAction.SHORT)
    assert accepted_action(bad, validate(bad, context)) is None


@pytest.mark.unit
def test_a_reference_id_must_be_well_formed_to_exist_at_all() -> None:
    with pytest.raises(ValueError, match="well-formed reference id"):
        ContextRef(
            ref_id="not an id",
            kind=ReferenceKind.BULL_EVIDENCE,
            label="x",
            authority=AuthorityClass.CALCULATED_METRIC,
        )


@pytest.mark.unit
def test_reference_numbering_sorts_the_way_the_facts_do() -> None:
    ids = [make_ref_id(ReferenceKind.BULL_EVIDENCE, index) for index in (2, 10)]
    assert ids == ["EV-BULL-002", "EV-BULL-010"]
    assert sorted(ids) == ids, "plain string ordering must agree with numeric ordering"

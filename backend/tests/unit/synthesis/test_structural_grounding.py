"""Safety-critical claims are grounded in state, not in wording (§2, §31).

The carry-forward this phase exists to close. Phase 7A checked confirmation and
risk permission by scanning prose for phrases. A model that would write

    the breakout is now confirmed

can equally write

    all conditions for entry are fully established at this point

and mean the same thing. The phrase list catches the first and not the second.

So these tests do two things. They prove a paraphrase **cannot** establish a
safety-critical fact - the typed claim is checked against the state its
references actually carry - and they prove the phrase scanners still fire as
defence in depth without being the authority.

Every value is TEST_FIXTURE data.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.application.synthesis.context import ContextFinding
from app.application.synthesis.draft import SynthesisDraft
from app.application.synthesis.validator import RejectionCode, validate_synthesis
from app.domain.market.quality import DataQualityVerdict
from app.domain.risk.sizing import SizingOutcome
from app.domain.synthesis.actions import FinalAction
from app.domain.synthesis.claims import Claim, ClaimType
from app.domain.synthesis.references import ReferenceKind
from tests.factories_synthesis import draft, make_ref, minimal_context, sizing
from tests.unit.synthesis.test_context import context_with_evidence

PARAPHRASES = (
    "the breakout is now confirmed",
    "all conditions for entry are fully established at this point",
    "the setup has completed its trigger and is ready",
    "every requirement has been satisfied by the close",
    "giriş koşulları tamamen sağlandı",
)


def with_claims(*claims: Claim, action: FinalAction = FinalAction.WAIT, **kwargs: object):  # type: ignore[no-untyped-def]
    base = draft(action=action, **kwargs)  # type: ignore[arg-type]
    return SynthesisDraft(
        proposed_action=base.proposed_action,
        summary=base.summary,
        bull=base.bull,
        bear=base.bear,
        neutral=base.neutral,
        devils_advocate=base.devils_advocate,
        claims=claims,
    )


def codes(candidate: SynthesisDraft, context: object) -> set[RejectionCode]:
    return set(validate_synthesis(candidate, context).codes)  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Confirmation cannot be established by prose, in any wording
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("wording", PARAPHRASES)
def test_no_wording_can_establish_confirmation_over_forming_evidence(wording: str) -> None:
    """The point of the whole carry-forward.

    Whatever the sentence says, an ENTRY_CONFIRMED claim citing forming
    evidence is refused - because the validator reads the evidence's recorded
    state, not the sentence.
    """
    context = context_with_evidence(confirmed=False)
    forming_ref = context.bull_evidence[0].ref.ref_id

    candidate = with_claims(
        Claim(
            claim_type=ClaimType.ENTRY_CONFIRMED,
            narrative=wording,
            evidence_refs=(forming_ref,),
        )
    )
    assert RejectionCode.CLAIM_CONTRADICTS_CONTEXT in codes(candidate, context)


@pytest.mark.unit
def test_a_confirmation_claim_citing_confirmed_evidence_is_accepted() -> None:
    context = context_with_evidence(confirmed=True)
    candidate = with_claims(
        Claim(
            claim_type=ClaimType.ENTRY_CONFIRMED,
            narrative="Kapanışla teyit edildi.",
            evidence_refs=(context.bull_evidence[0].ref.ref_id,),
        )
    )
    report = validate_synthesis(candidate, context)
    assert RejectionCode.CLAIM_CONTRADICTS_CONTEXT not in report.codes
    assert RejectionCode.UNGROUNDED_CLAIM not in report.codes


@pytest.mark.unit
def test_a_confirmation_claim_citing_nothing_is_ungrounded() -> None:
    context = context_with_evidence(confirmed=True)
    candidate = with_claims(
        Claim(claim_type=ClaimType.ENTRY_CONFIRMED, narrative="It is confirmed.")
    )
    assert RejectionCode.UNGROUNDED_CLAIM in codes(candidate, context)


@pytest.mark.unit
def test_the_phrase_scanner_still_fires_as_defence_in_depth() -> None:
    """Prose asserting confirmation with no claim behind it is still caught.

    It catches only listed wordings, which is exactly why it is not allowed to
    be the authority - but it costs nothing to keep.
    """
    context = context_with_evidence(confirmed=False)
    candidate = draft(action=FinalAction.WAIT, summary="The breakout is now confirmed.")
    assert RejectionCode.CONFIRMATION_UPGRADED in codes(candidate, context)


# ----------------------------------------------------------------------
# Risk permission comes from Phase 3, never from a sentence
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_risk_permission_claim_citing_a_refusing_result_is_refused() -> None:
    context = minimal_context(position_sizing=sizing(SizingOutcome.NOT_PERMITTED))
    context = replace(
        context,
        risk_findings=(
            ContextFinding(
                ref=make_ref(ReferenceKind.RISK_FINDING, 1),
                code=SizingOutcome.NOT_PERMITTED.value,
                severity="BLOCKING",
                detail="zero contracts",
            ),
        ),
    )
    candidate = with_claims(
        Claim(
            claim_type=ClaimType.RISK_PERMITS_POSITION,
            narrative="Pozisyon açılabilir.",
            risk_refs=(context.risk_findings[0].ref.ref_id,),
        ),
        action=FinalAction.NO_TRADE,
    )
    assert RejectionCode.CLAIM_CONTRADICTS_CONTEXT in codes(candidate, context)


@pytest.mark.unit
def test_a_risk_permission_claim_citing_an_allowed_result_is_accepted() -> None:
    context = replace(
        minimal_context(position_sizing=sizing(SizingOutcome.ALLOWED)),
        risk_findings=(
            ContextFinding(
                ref=make_ref(ReferenceKind.RISK_FINDING, 1),
                code=SizingOutcome.ALLOWED.value,
                severity="CAUTION",
                detail="four contracts",
            ),
        ),
    )
    candidate = with_claims(
        Claim(
            claim_type=ClaimType.RISK_PERMITS_POSITION,
            risk_refs=(context.risk_findings[0].ref.ref_id,),
        ),
        action=FinalAction.LONG,
    )
    assert RejectionCode.CLAIM_CONTRADICTS_CONTEXT not in codes(candidate, context)


@pytest.mark.unit
def test_a_risk_permission_claim_with_no_risk_reference_is_ungrounded() -> None:
    candidate = with_claims(
        Claim(claim_type=ClaimType.RISK_PERMITS_POSITION, narrative="Risk uygundur.")
    )
    assert RejectionCode.UNGROUNDED_CLAIM in codes(candidate, minimal_context())


# ----------------------------------------------------------------------
# Blocking, data quality, scenarios, missing information
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_blocking_claim_citing_a_non_blocking_finding_is_refused() -> None:
    context = replace(
        minimal_context(),
        suitability_findings=(
            ContextFinding(
                ref=make_ref(ReferenceKind.SUITABILITY_FINDING, 1),
                code="NO_CONFIRMATION",
                severity="PENDING",
                detail="waiting",
            ),
        ),
    )
    candidate = with_claims(
        Claim(
            claim_type=ClaimType.BLOCKING_CONDITION,
            suitability_refs=(context.suitability_findings[0].ref.ref_id,),
        )
    )
    assert RejectionCode.CLAIM_CONTRADICTS_CONTEXT in codes(candidate, context)


@pytest.mark.unit
def test_a_data_quality_claim_with_no_verdict_to_read_is_refused() -> None:
    """§21: unverified metadata cannot be presented as verified.

    Refused as ungrounded, because a claim citing nothing never reaches the
    verdict check - which is the right code for the right reason.
    """
    candidate = with_claims(Claim(claim_type=ClaimType.DATA_QUALITY_ACCEPTABLE))
    assert RejectionCode.UNGROUNDED_CLAIM in codes(candidate, minimal_context())


@pytest.mark.unit
def test_a_grounded_data_quality_claim_is_still_checked_against_the_verdict() -> None:
    """Citing something real does not make the claim true."""
    context = context_with_evidence()  # carries evidence, but no verdict
    candidate = with_claims(
        Claim(
            claim_type=ClaimType.DATA_QUALITY_ACCEPTABLE,
            evidence_refs=(context.bull_evidence[0].ref.ref_id,),
        )
    )
    assert RejectionCode.CLAIM_CONTRADICTS_CONTEXT in codes(candidate, context)


@pytest.mark.unit
def test_a_data_quality_claim_matching_a_blocked_verdict_is_accepted() -> None:
    from tests.factories_synthesis import data_quality  # noqa: PLC0415

    context = minimal_context(quality=data_quality(DataQualityVerdict.BLOCKED))
    context = replace(context, data_quality_verdict=DataQualityVerdict.BLOCKED)
    candidate = with_claims(
        Claim(claim_type=ClaimType.DATA_QUALITY_BLOCKED), action=FinalAction.NO_TRADE
    )
    assert RejectionCode.CLAIM_CONTRADICTS_CONTEXT not in codes(candidate, context)


@pytest.mark.unit
def test_a_scenario_claim_needs_both_a_scenario_and_evidence() -> None:
    context = context_with_evidence()
    only_scenario = with_claims(
        Claim(claim_type=ClaimType.SCENARIO_SUPPORTED, scenario_refs=("SCEN-BULL",))
    )
    assert RejectionCode.UNGROUNDED_CLAIM in codes(only_scenario, context)


@pytest.mark.unit
def test_a_missing_information_claim_needs_a_missing_reference() -> None:
    """§21: a model may explain a gap and may never fill one."""
    context = context_with_evidence()
    ungrounded = with_claims(
        Claim(claim_type=ClaimType.INFORMATION_MISSING, narrative="OI verisi yok.")
    )
    assert RejectionCode.UNGROUNDED_CLAIM in codes(ungrounded, context)

    grounded = with_claims(
        Claim(
            claim_type=ClaimType.INFORMATION_MISSING,
            missing_refs=(context.missing[0].ref.ref_id,),
        )
    )
    assert RejectionCode.UNGROUNDED_CLAIM not in codes(grounded, context)


@pytest.mark.unit
def test_a_contradiction_claim_citing_a_nonexistent_contradiction_is_refused() -> None:
    context = context_with_evidence()
    candidate = with_claims(
        Claim(claim_type=ClaimType.CONTRADICTION_PRESENT, contradiction_refs=("CON-DEADBEEF",))
    )
    assert RejectionCode.UNKNOWN_REFERENCE in codes(candidate, context)


@pytest.mark.unit
def test_a_fact_claim_needs_a_fact_reference() -> None:
    context = context_with_evidence()
    assert RejectionCode.UNGROUNDED_CLAIM in codes(
        with_claims(Claim(claim_type=ClaimType.FACT_RELEVANT, narrative="RSI önemli.")), context
    )


@pytest.mark.unit
def test_a_fact_claim_citing_a_real_fact_is_accepted() -> None:
    context = context_with_evidence()
    candidate = with_claims(
        Claim(
            claim_type=ClaimType.FACT_RELEVANT,
            fact_refs=(context.numeric_facts[0].ref_id,),
        )
    )
    assert RejectionCode.UNGROUNDED_CLAIM not in codes(candidate, context)


# ----------------------------------------------------------------------
# Which claims are safety-critical
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "claim_type",
    (
        ClaimType.ENTRY_CONFIRMED,
        ClaimType.RISK_PERMITS_POSITION,
        ClaimType.RISK_BLOCKS_POSITION,
        ClaimType.BLOCKING_CONDITION,
        ClaimType.DATA_QUALITY_ACCEPTABLE,
        ClaimType.DATA_QUALITY_BLOCKED,
    ),
)
def test_a_safety_critical_claim_citing_nothing_is_always_refused(
    claim_type: ClaimType,
) -> None:
    assert claim_type.is_safety_critical
    candidate = with_claims(Claim(claim_type=claim_type, narrative="Bir iddia."))
    assert RejectionCode.UNGROUNDED_CLAIM in codes(candidate, minimal_context())


@pytest.mark.unit
def test_a_draft_reports_its_safety_critical_claims() -> None:
    candidate = with_claims(
        Claim(claim_type=ClaimType.ENTRY_CONFIRMED, evidence_refs=("EV-BULL-001",)),
        Claim(claim_type=ClaimType.PENDING_CONDITION, suitability_refs=("SUIT-001",)),
    )
    kinds = {item.claim_type for item in candidate.safety_critical_claims}
    assert kinds == {ClaimType.ENTRY_CONFIRMED}


@pytest.mark.unit
def test_claim_references_are_checked_for_existence_like_any_other() -> None:
    context = context_with_evidence()
    candidate = with_claims(
        Claim(claim_type=ClaimType.ENTRY_CONFIRMED, evidence_refs=("EV-BULL-NOTREAL",))
    )
    assert RejectionCode.UNKNOWN_REFERENCE in codes(candidate, context)


@pytest.mark.unit
def test_a_draft_with_no_claims_at_all_is_still_valid() -> None:
    """Claims are how safety-critical things are said, not a quota."""
    assert validate_synthesis(draft(action=FinalAction.WAIT), minimal_context()).is_valid

"""Deterministic validation of a proposed synthesis (§21).

The schema in `schemas.py` proves a response is *shaped* correctly. This proves
it is *permitted*, which needs the context the schema knows nothing about:

* the proposed action is inside the deterministic `ActionEnvelope`;
* every identifier it cites exists, and is of a kind that fits where it was
  cited;
* the mandatory sections are genuinely present;
* no probability claim and no invented number appears in any narrative;
* no confirmation state was upgraded;
* no risk permission was manufactured.

**Invalid output never becomes application state.** `validate_synthesis`
returns a report; a caller that ignores the report and uses the draft anyway
has bypassed the whole mechanism, which is why the accepted value is produced
by `accept` and cannot be obtained from an invalid report.

Nothing here repairs anything. A draft proposing LONG where policy permits only
NO_TRADE is rejected, not downgraded - §9 is explicit that silently upgrading or
adjusting the model's answer is worse than refusing it, because it produces a
final state nobody chose and nobody reviewed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from app.application.synthesis.context import (
    ConfirmationState,
    ContextEvidence,
    SynthesisContext,
)
from app.application.synthesis.draft import SynthesisDraft
from app.application.synthesis.rendering import referenced_fact_ids
from app.application.synthesis.safety import (
    SafetyViolation,
    probability_violations,
    unsupported_numeric_violations,
)
from app.domain.market.quality import DataQualityVerdict
from app.domain.risk.sizing import SizingOutcome
from app.domain.suitability.no_trade import FindingSeverity
from app.domain.synthesis.actions import FinalAction
from app.domain.synthesis.claims import Claim, ClaimType
from app.domain.synthesis.references import ReferenceKind, kind_of


@unique
class RejectionCode(StrEnum):
    """Why a synthesis was refused. Stable, and safe to log."""

    ACTION_NOT_PERMITTED = "ACTION_NOT_PERMITTED"
    UNKNOWN_REFERENCE = "UNKNOWN_REFERENCE"
    WRONG_REFERENCE_KIND = "WRONG_REFERENCE_KIND"
    MISSING_DEVILS_ADVOCATE = "MISSING_DEVILS_ADVOCATE"
    MISSING_SCENARIO = "MISSING_SCENARIO"
    FABRICATED_COUNTER_EVIDENCE = "FABRICATED_COUNTER_EVIDENCE"
    PROBABILITY_CLAIM = "PROBABILITY_CLAIM"
    UNSUPPORTED_NUMERIC_CLAIM = "UNSUPPORTED_NUMERIC_CLAIM"
    CONFIRMATION_UPGRADED = "CONFIRMATION_UPGRADED"
    INVENTED_RISK_PERMISSION = "INVENTED_RISK_PERMISSION"
    EMPTY_NARRATIVE = "EMPTY_NARRATIVE"
    UNGROUNDED_CLAIM = "UNGROUNDED_CLAIM"
    """A typed claim cited nothing, or nothing of the required kind."""

    CLAIM_CONTRADICTS_CONTEXT = "CLAIM_CONTRADICTS_CONTEXT"
    """A claim cited a real reference whose actual state refutes it - forming
    evidence cited as confirmation, a refusing sizing result cited as
    permission. The reference exists; the claim is still false."""


@dataclass(frozen=True, slots=True)
class Rejection:
    """One reason a draft was refused."""

    code: RejectionCode
    detail: str

    def __post_init__(self) -> None:
        if not self.detail.strip():
            raise ValueError(f"{self.code.value} rejection carries no detail")


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """The verdict, and every reason behind it."""

    rejections: tuple[Rejection, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.rejections

    @property
    def codes(self) -> tuple[RejectionCode, ...]:
        return tuple(item.code for item in self.rejections)

    def accept(self, draft: SynthesisDraft) -> SynthesisDraft:
        """Return the draft only if this report accepted it.

        The single door through which a proposal becomes usable. A caller
        cannot reach an accepted value without having consulted the report,
        which is what makes "invalid never becomes state" structural rather
        than a rule someone must remember.
        """
        if not self.is_valid:
            raise ValueError(
                "a rejected synthesis cannot be accepted: "
                + ", ".join(item.code.value for item in self.rejections)
            )
        return draft


# Where each reference list is allowed to point.
_EXPECTED_KINDS: dict[str, tuple[ReferenceKind, ...]] = {
    "supporting_refs": (
        ReferenceKind.BULL_EVIDENCE,
        ReferenceKind.BEAR_EVIDENCE,
        ReferenceKind.NEUTRAL_EVIDENCE,
        ReferenceKind.SETUP_QUALITY_COMPONENT,
        ReferenceKind.ENTRY_QUALITY_COMPONENT,
        ReferenceKind.SCENARIO,
        ReferenceKind.VISION_OBSERVATION,
    ),
    "opposing_refs": (
        ReferenceKind.BULL_EVIDENCE,
        ReferenceKind.BEAR_EVIDENCE,
        ReferenceKind.NEUTRAL_EVIDENCE,
        ReferenceKind.CONTRADICTION,
        ReferenceKind.SETUP_QUALITY_COMPONENT,
        ReferenceKind.ENTRY_QUALITY_COMPONENT,
        ReferenceKind.RISK_FINDING,
        ReferenceKind.SUITABILITY_FINDING,
        ReferenceKind.VISION_OBSERVATION,
    ),
    "missing_refs": (ReferenceKind.MISSING_INFORMATION,),
    "confirmation_refs": (
        ReferenceKind.SUITABILITY_FINDING,
        ReferenceKind.MISSING_INFORMATION,
        ReferenceKind.SCENARIO,
        ReferenceKind.ENTRY_QUALITY_COMPONENT,
    ),
    "invalidation_refs": (
        ReferenceKind.SCENARIO,
        ReferenceKind.CONTRADICTION,
        ReferenceKind.BULL_EVIDENCE,
        ReferenceKind.BEAR_EVIDENCE,
        ReferenceKind.NEUTRAL_EVIDENCE,
    ),
    "contradiction_refs": (ReferenceKind.CONTRADICTION,),
}

_RISK_PERMISSION_CLAIMS = (
    "risk is acceptable",
    "risk is fine",
    "sizing is permitted",
    "position is permitted",
    "you may size up",
    "override the risk",
    "ignore the risk",
    "risk kabul edilebilir",
    "riski göz ardı",
)

_CONFIRMATION_UPGRADES = (
    "now confirmed",
    "has confirmed",
    "is confirmed",
    "confirmed breakout",
    "confirmed close",
    "teyit edildi",
    "onaylandı",
)


def _check_references(draft: SynthesisDraft, context: SynthesisContext) -> tuple[Rejection, ...]:
    """Every cited identifier must exist, and fit where it was cited."""
    known = context.ref_ids
    found: list[Rejection] = []

    for ref_id in draft.all_referenced_ids:
        if ref_id not in known:
            found.append(
                Rejection(
                    code=RejectionCode.UNKNOWN_REFERENCE,
                    detail=f"{ref_id} does not exist in this context",
                )
            )

    def check_list(name: str, refs: tuple[str, ...]) -> None:
        expected = _EXPECTED_KINDS.get(name)
        if expected is None:
            return
        for ref_id in refs:
            if ref_id not in known:
                continue  # already reported as unknown
            kind = kind_of(ref_id)
            if kind is not None and kind not in expected:
                found.append(
                    Rejection(
                        code=RejectionCode.WRONG_REFERENCE_KIND,
                        detail=(
                            f"{ref_id} is a {kind.value} reference and cannot appear in {name}"
                        ),
                    )
                )

    check_list("supporting_refs", draft.supporting_refs)
    check_list("opposing_refs", draft.opposing_refs)
    check_list("missing_refs", draft.missing_refs)
    check_list("confirmation_refs", draft.confirmation_refs)
    check_list("invalidation_refs", draft.invalidation_refs)
    check_list("opposing_refs", draft.devils_advocate.opposing_refs)
    check_list("contradiction_refs", draft.devils_advocate.contradiction_refs)
    check_list("missing_refs", draft.devils_advocate.missing_refs)
    for narrative in draft.narratives:
        check_list("supporting_refs", narrative.supporting_refs)
        check_list("opposing_refs", narrative.opposing_refs)

    return tuple(found)


def _evidence_by_ref(context: SynthesisContext) -> dict[str, ContextEvidence]:
    return {
        item.ref.ref_id: item
        for item in (*context.bull_evidence, *context.bear_evidence, *context.neutral_evidence)
    }


def _check_claims(draft: SynthesisDraft, context: SynthesisContext) -> tuple[Rejection, ...]:
    """Every typed claim must be supported by the state it points at (§2, §3).

    This is the primary safety authority. A claim is established by the
    references it cites and the state those references actually carry - never
    by how its narrative is phrased. Prose cannot make a claim true here, so a
    paraphrase cannot make a false one pass.
    """
    found: list[Rejection] = []
    evidence = _evidence_by_ref(context)
    findings = {item.ref.ref_id: item for item in context.suitability_findings}
    risks = {item.ref.ref_id: item for item in context.risk_findings}

    def ungrounded(claim: Claim, needed: str) -> None:
        found.append(
            Rejection(
                code=RejectionCode.UNGROUNDED_CLAIM,
                detail=f"{claim.claim_type.value} requires {needed}, and cited none",
            )
        )

    def refuted(claim: Claim, detail: str) -> None:
        found.append(
            Rejection(
                code=RejectionCode.CLAIM_CONTRADICTS_CONTEXT,
                detail=f"{claim.claim_type.value}: {detail}",
            )
        )

    for claim in draft.claims:
        if claim.claim_type.is_safety_critical and not claim.cites_anything:
            ungrounded(claim, "at least one context reference")
            continue

        if claim.claim_type is ClaimType.ENTRY_CONFIRMED:
            if not claim.evidence_refs:
                ungrounded(claim, "confirmed evidence references")
                continue
            for ref_id in claim.evidence_refs:
                item = evidence.get(ref_id)
                if item is None:
                    continue  # reported separately as an unknown reference
                if item.confirmation is not ConfirmationState.CONFIRMED:
                    refuted(
                        claim,
                        f"{ref_id} is {item.confirmation.value}, not CONFIRMED; a forming "
                        "observation cannot establish confirmation however it is described",
                    )

        elif claim.claim_type is ClaimType.RISK_PERMITS_POSITION:
            if not claim.risk_refs:
                ungrounded(claim, "a risk reference whose outcome is ALLOWED")
                continue
            for ref_id in claim.risk_refs:
                finding = risks.get(ref_id)
                if finding is None:
                    continue
                if finding.code != SizingOutcome.ALLOWED.value:
                    refuted(
                        claim,
                        f"{ref_id} reports sizing {finding.code}; permission comes from the "
                        "Phase 3 engine and a synthesis cannot grant it",
                    )

        elif claim.claim_type in {
            ClaimType.RISK_BLOCKS_POSITION,
            ClaimType.BLOCKING_CONDITION,
        }:
            cited = (*claim.risk_refs, *claim.suitability_refs)
            if not cited:
                ungrounded(claim, "a blocking risk or suitability reference")
                continue
            severities = [
                (findings.get(ref) or risks.get(ref)).severity  # type: ignore[union-attr]
                for ref in cited
                if ref in findings or ref in risks
            ]
            if severities and FindingSeverity.BLOCKING.value not in severities:
                refuted(
                    claim,
                    f"the cited finding(s) are {', '.join(sorted(set(severities)))}, not BLOCKING",
                )

        elif claim.claim_type is ClaimType.PENDING_CONDITION:
            if not claim.suitability_refs and not claim.evidence_refs:
                ungrounded(claim, "a pending suitability finding or forming evidence")

        elif claim.claim_type is ClaimType.DATA_QUALITY_ACCEPTABLE:
            if context.data_quality_verdict is None:
                refuted(claim, "no data-quality verdict was supplied, so none can be vouched for")
            elif context.data_quality_verdict is DataQualityVerdict.BLOCKED:
                refuted(claim, "the data-quality verdict is BLOCKED")

        elif claim.claim_type is ClaimType.DATA_QUALITY_BLOCKED:
            if context.data_quality_verdict is not DataQualityVerdict.BLOCKED:
                verdict = context.data_quality_verdict
                refuted(
                    claim,
                    f"the data-quality verdict is "
                    f"{verdict.value if verdict is not None else 'NOT_SUPPLIED'}",
                )

        elif claim.claim_type is ClaimType.SCENARIO_SUPPORTED:
            if not claim.scenario_refs:
                ungrounded(claim, "a scenario reference")
            elif not claim.evidence_refs:
                ungrounded(claim, "at least one supporting evidence reference")

        elif claim.claim_type is ClaimType.CONTRADICTION_PRESENT and not claim.contradiction_refs:
            ungrounded(claim, "a contradiction reference")

        elif claim.claim_type is ClaimType.INFORMATION_MISSING and not claim.missing_refs:
            ungrounded(claim, "a missing-information reference")

        elif claim.claim_type is ClaimType.FACT_RELEVANT and not claim.fact_refs:
            ungrounded(claim, "a numeric fact reference")

    return tuple(found)


def _check_devils_advocate(
    draft: SynthesisDraft, context: SynthesisContext
) -> tuple[Rejection, ...]:
    """The challenge must exist, and must be honest about what backs it (§13)."""
    advocate = draft.devils_advocate
    found: list[Rejection] = []

    if not advocate.challenge.strip():
        found.append(
            Rejection(
                code=RejectionCode.MISSING_DEVILS_ADVOCATE,
                detail="the Devil's Advocate section is mandatory and cannot be empty",
            )
        )

    cited = (*advocate.opposing_refs, *advocate.contradiction_refs, *advocate.missing_refs)
    if not cited and not advocate.evidence_is_limited:
        # It cited nothing and did not admit to having nothing. One of those
        # must be true, and the model does not get to leave it ambiguous.
        found.append(
            Rejection(
                code=RejectionCode.FABRICATED_COUNTER_EVIDENCE,
                detail=(
                    "the challenge cites no counter-evidence and does not declare that "
                    "evidence is limited; an objection with no basis is a fabrication"
                ),
            )
        )

    if advocate.evidence_is_limited and cited:
        found.append(
            Rejection(
                code=RejectionCode.FABRICATED_COUNTER_EVIDENCE,
                detail=(
                    "the challenge declares evidence limited while citing "
                    f"{len(cited)} reference(s); both cannot be true"
                ),
            )
        )

    if not advocate.evidence_is_limited and not context.opposing_refs and cited:
        found.append(
            Rejection(
                code=RejectionCode.FABRICATED_COUNTER_EVIDENCE,
                detail="the context holds no opposing evidence, so none can be cited",
            )
        )

    return tuple(found)


def _check_confirmation_state(
    draft: SynthesisDraft, context: SynthesisContext
) -> tuple[Rejection, ...]:
    """Defence in depth: forming evidence narrated as confirmed (§19, §20).

    **Not the primary check.** `_check_claims` decides whether confirmation is
    established, by reading the state of the evidence a claim cites. This only
    catches prose that asserts confirmation without making a claim at all - and
    it catches only the wordings on its list, which is precisely why it is not
    allowed to be the authority.
    """
    any_confirmed = any(
        item.confirmation is ConfirmationState.CONFIRMED
        for item in (*context.bull_evidence, *context.bear_evidence, *context.neutral_evidence)
    )
    if any_confirmed:
        return ()

    lowered = draft.narrative_text.lower()
    for phrase in _CONFIRMATION_UPGRADES:
        if phrase in lowered:
            return (
                Rejection(
                    code=RejectionCode.CONFIRMATION_UPGRADED,
                    detail=(
                        f"the narrative says {phrase!r} while no evidence in this context is "
                        "confirmed; a forming observation must not be presented as settled"
                    ),
                ),
            )
    return ()


def _check_risk_permission(draft: SynthesisDraft) -> tuple[Rejection, ...]:
    """Defence in depth: prose that grants risk permission (§21).

    **Not the primary check.** `RISK_PERMITS_POSITION` claims are checked
    against the Phase 3 sizing outcome in `_check_claims`. This catches a
    sentence that asserts permission without any claim behind it, on a short
    list of wordings a paraphrase would escape.
    """
    lowered = draft.narrative_text.lower()
    for phrase in _RISK_PERMISSION_CLAIMS:
        if phrase in lowered:
            return (
                Rejection(
                    code=RejectionCode.INVENTED_RISK_PERMISSION,
                    detail=(
                        f"the narrative asserts {phrase!r}; risk permission comes from the "
                        "Phase 3 sizing engine and cannot be granted by a synthesis"
                    ),
                ),
            )
    return ()


def _as_rejections(violations: tuple[SafetyViolation, ...], code: RejectionCode) -> list[Rejection]:
    return [
        Rejection(code=code, detail=f"{item.detail} (near: {item.excerpt[:120]})")
        for item in violations
    ]


def validate_synthesis(draft: SynthesisDraft, context: SynthesisContext) -> ValidationReport:
    """Prove a proposed synthesis may become application state.

    Every check runs; the report lists all reasons rather than the first, so a
    caller sees everything wrong with an output instead of fixing one problem
    at a time.
    """
    rejections: list[Rejection] = []

    # §9: the envelope is the whole point. A proposal outside it is refused,
    # never adjusted to fit.
    if not context.envelope.permits(draft.proposed_action):
        blocked_by = context.envelope.why_not(draft.proposed_action)
        reasons = "; ".join(item.detail for item in blocked_by) or "not permitted"
        rejections.append(
            Rejection(
                code=RejectionCode.ACTION_NOT_PERMITTED,
                detail=(
                    f"{draft.proposed_action.value} is not permitted; allowed: "
                    f"{', '.join(item.value for item in context.envelope.allowed)}. {reasons}"
                ),
            )
        )

    rejections.extend(_check_references(draft, context))
    # The primary safety authority: typed claims checked against the state
    # their references actually carry.
    rejections.extend(_check_claims(draft, context))
    rejections.extend(_check_devils_advocate(draft, context))
    # Defence in depth only. These read prose, so they catch unsafe wording
    # that a correctly grounded claim did not cover - and they can never grant
    # anything, because nothing downstream consults them for permission.
    rejections.extend(_check_confirmation_state(draft, context))
    rejections.extend(_check_risk_permission(draft))

    if not draft.summary.strip():
        rejections.append(
            Rejection(code=RejectionCode.EMPTY_NARRATIVE, detail="the summary is empty")
        )
    for narrative in draft.narratives:
        if not narrative.narrative.strip():
            rejections.append(
                Rejection(
                    code=RejectionCode.MISSING_SCENARIO,
                    detail=f"the {narrative.case.value} narrative is empty",
                )
            )

    text = draft.narrative_text
    # Fact placeholders are citations like any other: an unknown one is a
    # fabricated reference that would otherwise reach a reader as a broken
    # span rather than as an error.
    renderable = {fact.ref_id for fact in context.numeric_facts} | {
        item.ref.ref_id for item in context.vision_observations
    }
    for ref_id in referenced_fact_ids(text):
        if ref_id not in renderable:
            rejections.append(
                Rejection(
                    code=RejectionCode.UNKNOWN_REFERENCE,
                    detail=(
                        f"{ref_id} is cited in a narrative but is not a fact or "
                        "observation in this context"
                    ),
                )
            )

    rejections.extend(_as_rejections(probability_violations(text), RejectionCode.PROBABILITY_CLAIM))
    rejections.extend(
        _as_rejections(
            unsupported_numeric_violations(text, context.allowed_numeric_strings),
            RejectionCode.UNSUPPORTED_NUMERIC_CLAIM,
        )
    )

    return ValidationReport(rejections=tuple(rejections))


def accepted_action(draft: SynthesisDraft, report: ValidationReport) -> FinalAction | None:
    """The final action, or ``None`` when the synthesis was refused.

    ``None`` means *no synthesis*, not NO_TRADE. A caller wanting a
    deterministic answer in that case reads the `ActionEnvelope`, which never
    needed a model.
    """
    return draft.proposed_action if report.is_valid else None

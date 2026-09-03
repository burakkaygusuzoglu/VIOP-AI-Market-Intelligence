"""The strict contract a synthesis response must satisfy (§11).

The boundary where untrusted model output becomes a typed proposal. Exactly as
unforgiving as Phase 6's vision schema, and for the same reason: a loosely
typed AI response is how unvalidated output reaches application state.

* ``extra="forbid"`` everywhere - an invented key is a contract violation, not
  something to ignore. A model that added a field has not answered the question
  asked.
* strict enums - the action is one of four, the scenario case one of three.
* bounded collections and bounded strings - an unbounded narrative is a denial
  of service and an unbounded reference list is a fishing expedition.
* no `dict[str, Any]` anywhere.

## What is *not* here

No numbers. There is deliberately no field in which a model can state a price, a
stop, a target, a size or a probability - §14 and §15 make those inventions
invalid, and the cheapest way to enforce that is to leave nowhere to put them.
Numeric facts are cited by reference; everything else is narrative that the
validator scans.

Structural validity is all this layer establishes. Whether the proposal is
*permitted* - inside the action envelope, citing facts that exist, free of
invented figures - is `validator.validate_synthesis`, which needs the context
this schema knows nothing about.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.application.synthesis.draft import (
    DevilsAdvocate,
    ScenarioNarrative,
    SynthesisDraft,
)
from app.domain.analysis.scenarios import ScenarioCase
from app.domain.synthesis.actions import FinalAction
from app.domain.synthesis.claims import Claim, ClaimType

OUTPUT_SCHEMA_VERSION = "synthesis-output/1"

_STRICT = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

_MAX_REFS = 40
"""Per reference list. Generous for a real synthesis and small enough that a
runaway response is refused rather than processed."""

_MAX_NARRATIVE = 4000
_MAX_SUMMARY = 1200
_MAX_CAVEAT = 600
_MAX_CLAIMS = 30


class ScenarioNarrativeSchema(BaseModel):
    """One of the three mandatory readings."""

    model_config = _STRICT

    case: ScenarioCase
    narrative: str = Field(min_length=1, max_length=_MAX_NARRATIVE)
    supporting_refs: tuple[str, ...] = Field(default=(), max_length=_MAX_REFS)
    opposing_refs: tuple[str, ...] = Field(default=(), max_length=_MAX_REFS)

    def to_draft(self) -> ScenarioNarrative:
        return ScenarioNarrative(
            case=self.case,
            narrative=self.narrative,
            supporting_refs=self.supporting_refs,
            opposing_refs=self.opposing_refs,
        )


class DevilsAdvocateSchema(BaseModel):
    """Mandatory. There is no shape of this response without one."""

    model_config = _STRICT

    challenge: str = Field(min_length=1, max_length=_MAX_NARRATIVE)
    opposing_refs: tuple[str, ...] = Field(default=(), max_length=_MAX_REFS)
    contradiction_refs: tuple[str, ...] = Field(default=(), max_length=_MAX_REFS)
    missing_refs: tuple[str, ...] = Field(default=(), max_length=_MAX_REFS)
    evidence_is_limited: bool = False
    """Set when the context genuinely holds no counter-evidence. Saying so is
    required; inventing an objection instead is what this flag prevents."""

    def to_draft(self) -> DevilsAdvocate:
        return DevilsAdvocate(
            challenge=self.challenge,
            opposing_refs=self.opposing_refs,
            contradiction_refs=self.contradiction_refs,
            missing_refs=self.missing_refs,
            evidence_is_limited=self.evidence_is_limited,
        )


class ClaimSchema(BaseModel):
    """One typed, grounded assertion (§3).

    Reference lists are separated by kind so the validator can demand the right
    *sort* of support, not merely some support.
    """

    model_config = _STRICT

    claim_type: ClaimType
    narrative: str = Field(default="", max_length=_MAX_CAVEAT)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=_MAX_REFS)
    contradiction_refs: tuple[str, ...] = Field(default=(), max_length=_MAX_REFS)
    risk_refs: tuple[str, ...] = Field(default=(), max_length=_MAX_REFS)
    suitability_refs: tuple[str, ...] = Field(default=(), max_length=_MAX_REFS)
    missing_refs: tuple[str, ...] = Field(default=(), max_length=_MAX_REFS)
    fact_refs: tuple[str, ...] = Field(default=(), max_length=_MAX_REFS)
    scenario_refs: tuple[str, ...] = Field(default=(), max_length=_MAX_REFS)

    def to_draft(self) -> Claim:
        return Claim(
            claim_type=self.claim_type,
            narrative=self.narrative,
            evidence_refs=self.evidence_refs,
            contradiction_refs=self.contradiction_refs,
            risk_refs=self.risk_refs,
            suitability_refs=self.suitability_refs,
            missing_refs=self.missing_refs,
            fact_refs=self.fact_refs,
            scenario_refs=self.scenario_refs,
        )


class SynthesisOutputSchema(BaseModel):
    """The whole synthesis response.

    All three scenario narratives are required fields. A model cannot omit the
    bear case on a bullish read, which is precisely when omitting it would be
    most tempting and least useful.
    """

    model_config = _STRICT

    proposed_action: FinalAction
    summary: str = Field(min_length=1, max_length=_MAX_SUMMARY)
    bull: ScenarioNarrativeSchema
    bear: ScenarioNarrativeSchema
    neutral: ScenarioNarrativeSchema
    devils_advocate: DevilsAdvocateSchema

    supporting_refs: tuple[str, ...] = Field(default=(), max_length=_MAX_REFS)
    opposing_refs: tuple[str, ...] = Field(default=(), max_length=_MAX_REFS)
    missing_refs: tuple[str, ...] = Field(default=(), max_length=_MAX_REFS)
    confirmation_refs: tuple[str, ...] = Field(default=(), max_length=_MAX_REFS)
    invalidation_refs: tuple[str, ...] = Field(default=(), max_length=_MAX_REFS)
    caveats: tuple[str, ...] = Field(default=(), max_length=20)
    claims: tuple[ClaimSchema, ...] = Field(default=(), max_length=_MAX_CLAIMS)
    """Where every safety-critical assertion belongs (§2).

    There is deliberately no field named `risk_permission`, `allowed_actions`,
    `maximum_contracts`, `blocker_override` or anything similar - §16. With
    `extra="forbid"` a response inventing one is refused outright, so the
    envelope is not something a model can address, let alone change.
    """

    def to_draft(self) -> SynthesisDraft:
        """Convert into the plain dataclass the port speaks.

        The single crossing point from pydantic into the rest of the
        application; past here nothing knows what a `BaseModel` is.
        """
        return SynthesisDraft(
            proposed_action=self.proposed_action,
            summary=self.summary,
            bull=self.bull.to_draft(),
            bear=self.bear.to_draft(),
            neutral=self.neutral.to_draft(),
            devils_advocate=self.devils_advocate.to_draft(),
            supporting_refs=self.supporting_refs,
            opposing_refs=self.opposing_refs,
            missing_refs=self.missing_refs,
            confirmation_refs=self.confirmation_refs,
            invalidation_refs=self.invalidation_refs,
            caveats=tuple(item[:_MAX_CAVEAT] for item in self.caveats),
            claims=tuple(item.to_draft() for item in self.claims),
        )


def scenario_cases_are_complete(schema: SynthesisOutputSchema) -> bool:
    """Whether the three narratives really are one of each case.

    A model can satisfy the field names while putting a BULL case in the bear
    slot. The validator treats that as invalid; this is the predicate it uses.
    """
    return (
        schema.bull.case is ScenarioCase.BULL
        and schema.bear.case is ScenarioCase.BEAR
        and schema.neutral.case is ScenarioCase.NEUTRAL
    )

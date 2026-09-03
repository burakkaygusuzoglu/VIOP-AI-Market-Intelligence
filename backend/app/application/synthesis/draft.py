"""What a synthesis provider returns, and what it may not contain (§11, §22, §23).

Two separate ideas live here and conflating them is the mistake this module
exists to prevent:

* `SynthesisDraft` - what a model *proposed*. Structurally valid, not yet
  trusted. It is a proposal until `validator.validate_synthesis` proves it sits
  inside the deterministic envelope and cites only facts that exist.
* `SynthesisStatus` - whether the attempt *ran*. Provider failure is not a
  market opinion (§22): a timeout is not WAIT, an unconfigured key is not
  NO_TRADE, and a malformed response is not bearish. The application can decline
  to produce a synthesis without pretending the market said anything.

That second distinction is load-bearing. Every one of those failure modes has a
tempting "safe" default, and every one of those defaults is a lie about what
was observed. A caller that wants an action when synthesis is unavailable
already has one: the deterministic `ActionEnvelope`, which is computed without
a model and stands on its own.

The draft is plain frozen dataclasses so the port stays free of pydantic, SDK
types, dicts and `Any` (§23). Strict parsing of provider JSON happens in
`schemas.py` at the application boundary, exactly as Phase 6 did for vision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from app.domain.analysis.scenarios import ScenarioCase
from app.domain.synthesis.actions import FinalAction
from app.domain.synthesis.claims import Claim


@unique
class SynthesisStatus(StrEnum):
    """Whether a synthesis attempt produced anything - never a market view."""

    SUCCESS = "SUCCESS"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    """The provider answered and the answer failed validation. Never repaired,
    never partially accepted."""

    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    """Timeout, network, rate limit, provider error."""

    NOT_CONFIGURED = "NOT_CONFIGURED"
    """No model or credential configured. An operational fact about this
    deployment, not an observation about the market."""

    CONTEXT_TOO_LARGE = "CONTEXT_TOO_LARGE"
    """The context did not fit the budget even after deterministic trimming.

    §12: the alternative would be dropping a risk blocker to make room, then
    asking a model to analyse what is left. A refusal is the honest outcome,
    and it is emphatically not NO_TRADE.
    """

    @property
    def produced_synthesis(self) -> bool:
        return self is SynthesisStatus.SUCCESS


@dataclass(frozen=True, slots=True)
class ScenarioNarrative:
    """One of the three mandatory readings (§12).

    Independent interpretations of the same evidence. They are **not** shares
    of a total: nothing here sums to 100, because the three are not competing
    for a fixed quantity and presenting them that way would manufacture a
    probability the project cannot support.

    The deterministic Phase 4 scenario stays authoritative for state and
    quality. This narrates it.
    """

    case: ScenarioCase
    narrative: str
    supporting_refs: tuple[str, ...] = ()
    opposing_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DevilsAdvocate:
    """The mandatory challenge to the preferred reading (§13).

    `evidence_is_limited` is the honest escape hatch. When the context holds no
    genuine counter-evidence, the correct answer is to say so - not to
    manufacture an objection, and not to omit the section. A fabricated
    counter-argument is worse than an acknowledged absence, because it reads
    like analysis.
    """

    challenge: str
    opposing_refs: tuple[str, ...] = ()
    contradiction_refs: tuple[str, ...] = ()
    missing_refs: tuple[str, ...] = ()
    evidence_is_limited: bool = False


@dataclass(frozen=True, slots=True)
class SynthesisDraft:
    """A model's proposed synthesis. Structurally valid, not yet trusted."""

    proposed_action: FinalAction
    summary: str
    bull: ScenarioNarrative
    bear: ScenarioNarrative
    neutral: ScenarioNarrative
    devils_advocate: DevilsAdvocate
    supporting_refs: tuple[str, ...] = ()
    opposing_refs: tuple[str, ...] = ()
    missing_refs: tuple[str, ...] = ()
    confirmation_refs: tuple[str, ...] = ()
    """Requirements that must be met before the setup triggers, cited by
    reference. Free text describing a new condition is not accepted - §11
    allows confirmation to be expressed only through known typed facts."""

    invalidation_refs: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()
    claims: tuple[Claim, ...] = ()
    """Typed, grounded assertions (§2, §3).

    Every safety-critical statement travels here rather than in prose, so the
    validator can check the referenced state instead of parsing a sentence.
    """

    @property
    def safety_critical_claims(self) -> tuple[Claim, ...]:
        return tuple(item for item in self.claims if item.claim_type.is_safety_critical)

    @property
    def narratives(self) -> tuple[ScenarioNarrative, ...]:
        return (self.bull, self.bear, self.neutral)

    @property
    def all_referenced_ids(self) -> tuple[str, ...]:
        """Every identifier the draft cites, in a stable order."""
        collected: list[str] = [
            *self.supporting_refs,
            *self.opposing_refs,
            *self.missing_refs,
            *self.confirmation_refs,
            *self.invalidation_refs,
            *self.devils_advocate.opposing_refs,
            *self.devils_advocate.contradiction_refs,
            *self.devils_advocate.missing_refs,
        ]
        for narrative in self.narratives:
            collected.extend(narrative.supporting_refs)
            collected.extend(narrative.opposing_refs)
        for claim in self.claims:
            collected.extend(claim.all_refs)
        return tuple(collected)

    @property
    def narrative_text(self) -> str:
        """Everything a human would read, joined for safety scanning."""
        parts = [self.summary, self.devils_advocate.challenge, *self.caveats]
        parts.extend(item.narrative for item in self.narratives)
        return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class SynthesisOutcome:
    """The result of an attempt: a status, and a draft only on success.

    A draft can never accompany a failure status, and success can never arrive
    without one. Enforced rather than documented, because the whole point of
    §22 is that a failed attempt must not be readable as a quiet opinion.
    """

    status: SynthesisStatus
    draft: SynthesisDraft | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status.produced_synthesis and self.draft is None:
            raise ValueError("a successful synthesis must carry a draft")
        if not self.status.produced_synthesis and self.draft is not None:
            raise ValueError(f"a {self.status.value} outcome must not carry a draft")

"""Safety-critical claims are structural, not lexical (§2, §3).

Phase 7A checked some claims by scanning prose for phrases - "now confirmed",
"risk is acceptable". The human review is right that this cannot be the primary
authority. A model that would write

    the breakout is now confirmed

can equally write

    all conditions for entry are fully established at this point

and mean the same thing. The phrase list catches the first and not the second,
and the space of paraphrases is unbounded. Any safety property that depends on
enumerating wordings is a property that fails quietly.

## The inversion

So the model does not *assert* safety-critical facts in prose. It **makes a
typed claim that points at the context**:

    Claim(claim_type=ENTRY_CONFIRMED, evidence_refs=("EV-BULL-8F2A1C9D0B",))

and the deterministic validator looks that reference up and checks the state it
actually has. If the cited evidence is FORMING, the claim is refused - however
the accompanying sentence was phrased, and whether or not any phrase in it
appeared on a list.

That flips the burden. Prose can no longer establish anything: it can only
describe a claim that the context already supports. Phrase scanning stays as
defence in depth - it catches unsafe wording that slipped past a correctly
grounded claim - but it can never *grant* anything.

## Why claim types are a closed enum

Each type names a question the validator knows how to answer against typed
state. A free-form claim type would be a string the validator could only
pattern-match, which is the problem this module exists to remove.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique


@unique
class ClaimType(StrEnum):
    """What a synthesis is asserting, in a form a validator can check."""

    ENTRY_CONFIRMED = "ENTRY_CONFIRMED"
    """Entry conditions are established by closed-candle evidence.

    Requires evidence references whose recorded confirmation state is
    CONFIRMED. Cited FORMING evidence refutes it.
    """

    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    """Something has not triggered yet. Requires a PENDING suitability finding
    or forming evidence - the honest counterpart of ENTRY_CONFIRMED."""

    RISK_PERMITS_POSITION = "RISK_PERMITS_POSITION"
    """Sizing allows a position. Requires a risk reference whose outcome is
    ALLOWED. A model can never grant this: it can only point at a Phase 3
    result that already says so."""

    RISK_BLOCKS_POSITION = "RISK_BLOCKS_POSITION"
    BLOCKING_CONDITION = "BLOCKING_CONDITION"
    """A hard blocker exists. Requires a BLOCKING suitability or risk finding."""

    PENDING_CONDITION = "PENDING_CONDITION"
    DATA_QUALITY_ACCEPTABLE = "DATA_QUALITY_ACCEPTABLE"
    DATA_QUALITY_BLOCKED = "DATA_QUALITY_BLOCKED"
    SCENARIO_SUPPORTED = "SCENARIO_SUPPORTED"
    """A scenario is supported by evidence. Requires both a scenario reference
    and at least one evidence reference."""

    CONTRADICTION_PRESENT = "CONTRADICTION_PRESENT"
    INFORMATION_MISSING = "INFORMATION_MISSING"
    """Something needed is absent. Requires a missing-information reference -
    §21: a model may explain a gap and may never fill one."""

    FACT_RELEVANT = "FACT_RELEVANT"
    """A numeric fact bears on the reading. Requires a `FACT-…` reference; the
    value is rendered from the fact, never from the narrative (§4)."""

    @property
    def is_safety_critical(self) -> bool:
        """Whether an ungrounded claim of this type could change a decision.

        Every type here is checkable, but these are the ones where being wrong
        is not merely inaccurate - it is a permission the model does not have.
        """
        return self in {
            ClaimType.ENTRY_CONFIRMED,
            ClaimType.RISK_PERMITS_POSITION,
            ClaimType.RISK_BLOCKS_POSITION,
            ClaimType.BLOCKING_CONDITION,
            ClaimType.DATA_QUALITY_ACCEPTABLE,
            ClaimType.DATA_QUALITY_BLOCKED,
        }


@dataclass(frozen=True, slots=True)
class Claim:
    """One typed assertion, and the context references that must support it.

    The reference lists are separated by kind rather than pooled into one, so
    the validator can require *the right sort* of support: an
    ENTRY_CONFIRMED claim backed only by a risk reference is not grounded, and
    a single list would have hidden that.
    """

    claim_type: ClaimType
    narrative: str = ""
    """Explanatory prose. Carries no authority: it describes the claim, and the
    claim is established by the references below."""

    evidence_refs: tuple[str, ...] = ()
    contradiction_refs: tuple[str, ...] = ()
    risk_refs: tuple[str, ...] = ()
    suitability_refs: tuple[str, ...] = ()
    missing_refs: tuple[str, ...] = ()
    fact_refs: tuple[str, ...] = ()
    scenario_refs: tuple[str, ...] = ()

    @property
    def all_refs(self) -> tuple[str, ...]:
        return (
            *self.evidence_refs,
            *self.contradiction_refs,
            *self.risk_refs,
            *self.suitability_refs,
            *self.missing_refs,
            *self.fact_refs,
            *self.scenario_refs,
        )

    @property
    def cites_anything(self) -> bool:
        return bool(self.all_refs)

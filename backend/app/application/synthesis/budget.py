"""What to drop when a context will not fit, and what may never be dropped (§18).

Truncation is where safety information quietly disappears. A prompt that is too
long gets trimmed, the trim takes the tail, and the tail happens to hold the
risk blocker. Nothing errors, the synthesis reads confidently, and the one fact
that should have stopped it is gone.

So trimming here is explicit, deterministic and refuses rather than guesses.

## Mandatory context

Some entries are never candidates for removal, whatever the budget:

* risk blockers and suitability blockers - the reasons an action is forbidden;
* the action envelope and its constraints;
* data-quality blockers;
* missing critical information;
* MAJOR contradictions.

If the mandatory set alone exceeds the budget, this module **fails**. That is
the correct outcome: a synthesis produced without its own blockers is worse
than no synthesis, and §22 already gives the caller an honest way to report
that nothing was produced.

## Optional context

Everything else is trimmed by a fixed priority, and within a priority by the
canonical order assembly already fixed - never by set iteration, never by
whatever the model saw first. The same context over budget always yields the
same trimmed context, which keeps the digest meaningful.

The budget itself is **application policy**, not a market fact: it is
configurable, documented here, and carries no claim about what any provider
accepts.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum, unique

from app.application.synthesis.context import SynthesisContext


class ContextBudgetExceededError(Exception):
    """The mandatory context alone does not fit.

    Raised rather than resolved. Every alternative - dropping a blocker,
    summarising a finding, silently keeping the first N - produces a synthesis
    whose safety information is incomplete in a way the reader cannot see.
    """

    def __init__(self, required: int, budget: int, detail: str) -> None:
        self.required = required
        self.budget = budget
        self.detail = detail
        super().__init__(
            f"mandatory synthesis context needs {required} units "
            f"against a {budget} budget: {detail}"
        )


@unique
class TrimPriority(IntEnum):
    """Trim order for optional context. Lower goes first."""

    NEUTRAL_EVIDENCE = 1
    """Context-setting, and the least load-bearing thing present."""

    VISION_OBSERVATION = 2
    """Supplementary by definition (§1, §65) - it never carries authority."""

    WEAK_SUPPORTING_EVIDENCE = 3
    MINOR_CONTRADICTION = 4
    QUALITY_COMPONENT = 5
    """Individual component breakdowns; the total score is carried separately
    and is not a trim candidate."""


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """How much context a synthesis request may carry.

    Units are **entries**, not tokens. Counting tokens would tie this policy to
    one provider's tokeniser and make the limit a vendor fact rather than an
    application one; entries are provider-neutral and are what the trim
    actually removes. A 7B adapter that needs a token ceiling converts at its
    own boundary, where the tokeniser is known.
    """

    max_entries: int = 120
    keep_neutral_evidence: bool = True
    keep_vision_observations: bool = True

    def __post_init__(self) -> None:
        if self.max_entries < 1:
            raise ValueError("a context budget must allow at least one entry")


@dataclass(frozen=True, slots=True)
class TrimResult:
    """A trimmed context, and an honest record of what left."""

    context: SynthesisContext
    removed: tuple[str, ...] = ()
    """Reference ids that were dropped, so the audit record shows the gap
    rather than merely showing fewer entries."""

    @property
    def was_trimmed(self) -> bool:
        return bool(self.removed)


def mandatory_entries(context: SynthesisContext) -> tuple[str, ...]:
    """Reference ids that may never be dropped."""
    keep: list[str] = []

    keep.extend(
        item.ref.ref_id for item in context.suitability_findings if item.severity == "BLOCKING"
    )
    keep.extend(item.ref.ref_id for item in context.risk_findings if item.severity == "BLOCKING")
    keep.extend(item.ref.ref_id for item in context.missing)
    keep.extend(item.ref.ref_id for item in context.contradictions if item.severity == "MAJOR")
    return tuple(keep)


def fit_to_budget(context: SynthesisContext, budget: ContextBudget | None = None) -> TrimResult:
    """Trim optional context deterministically, or refuse.

    Returns the context unchanged when it already fits. Raises
    `ContextBudgetExceededError` when the mandatory set alone does not.
    """
    policy = budget if budget is not None else ContextBudget()
    mandatory = mandatory_entries(context)

    if len(mandatory) > policy.max_entries:
        raise ContextBudgetExceededError(
            required=len(mandatory),
            budget=policy.max_entries,
            detail=(
                f"{len(mandatory)} entries are blockers, missing information or major "
                "contradictions and cannot be dropped"
            ),
        )

    total = len(context.all_refs)
    if total <= policy.max_entries:
        return TrimResult(context=context)

    removed: list[str] = []
    trimmed = context
    over = total - policy.max_entries

    # Priority 1: neutral evidence.
    if over > 0 and not policy.keep_neutral_evidence and trimmed.neutral_evidence:
        removed.extend(item.ref.ref_id for item in trimmed.neutral_evidence)
        over -= len(trimmed.neutral_evidence)
        trimmed = replace(trimmed, neutral_evidence=())

    # Priority 2: vision observations - supplementary, never authoritative.
    if over > 0 and not policy.keep_vision_observations and trimmed.vision_observations:
        removed.extend(item.ref.ref_id for item in trimmed.vision_observations)
        over -= len(trimmed.vision_observations)
        trimmed = replace(trimmed, vision_observations=())

    # Priority 3: weakest supporting evidence, weakest first, canonical order.
    if over > 0:
        trimmed, dropped = _trim_weak_evidence(trimmed, over, mandatory)
        removed.extend(dropped)
        over -= len(dropped)

    # Priority 4: minor contradictions - never MAJOR, which is mandatory.
    if over > 0 and trimmed.contradictions:
        keepable = tuple(item for item in trimmed.contradictions if item.severity == "MAJOR")
        droppable = tuple(item for item in trimmed.contradictions if item.severity != "MAJOR")
        drop_count = min(over, len(droppable))
        if drop_count:
            dropped_items = droppable[-drop_count:]
            removed.extend(item.ref.ref_id for item in dropped_items)
            kept = tuple(item for item in trimmed.contradictions if item not in dropped_items)
            trimmed = replace(trimmed, contradictions=kept)
            over -= drop_count
            del keepable

    if over > 0:
        raise ContextBudgetExceededError(
            required=len(trimmed.all_refs),
            budget=policy.max_entries,
            detail=(
                "optional context is exhausted and the remainder is mandatory; "
                "safety information will not be dropped to fit a prompt"
            ),
        )

    return TrimResult(context=trimmed, removed=tuple(removed))


_STRENGTH_ORDER = {"WEAK": 0, "MODERATE": 1, "STRONG": 2}


def _trim_weak_evidence(
    context: SynthesisContext, over: int, mandatory: tuple[str, ...]
) -> tuple[SynthesisContext, tuple[str, ...]]:
    """Drop the weakest supporting evidence first, in canonical order.

    Only evidence *supporting* the assessed direction is a candidate. Evidence
    that argues against it is what a Devil's Advocate needs (§13) and what a
    reader is least likely to supply for themselves.
    """
    from app.domain.analysis.evidence import EvidenceDirection  # noqa: PLC0415

    bullish = context.direction is EvidenceDirection.BULLISH
    supporting = context.bull_evidence if bullish else context.bear_evidence
    if not supporting:
        return context, ()

    ranked = sorted(
        supporting,
        key=lambda item: (_STRENGTH_ORDER.get(item.strength, 0), item.ref.ref_id),
    )
    droppable = [item for item in ranked if item.ref.ref_id not in mandatory]
    dropped = droppable[: min(over, len(droppable))]
    if not dropped:
        return context, ()

    dropped_ids = {item.ref.ref_id for item in dropped}
    kept = tuple(item for item in supporting if item.ref.ref_id not in dropped_ids)
    # Two explicit branches rather than `replace(context, **{attr: kept})`:
    # dynamic keyword names defeat the type checker, and this layer decides
    # what safety information survives - it is the last place to accept an
    # untyped shortcut.
    trimmed = (
        replace(context, bull_evidence=kept) if bullish else replace(context, bear_evidence=kept)
    )
    return trimmed, tuple(item.ref.ref_id for item in dropped)

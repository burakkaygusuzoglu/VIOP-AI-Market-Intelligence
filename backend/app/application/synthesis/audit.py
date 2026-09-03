"""Audit metadata for a synthesis attempt (§4, §24).

Enough to reconstruct what was asked, and honest about what cannot be
reproduced.

## What is reproducible, and what is not

§24 asks for this distinction to be stated rather than blurred, so the record
separates them:

**Deterministic** - identical inputs give identical outputs, every time:
context assembly, the canonical form, the digest, the action envelope, the
reference identifiers, and the validator's verdict.

**Provider-dependent** - the natural-language synthesis itself. A model is not
a pure function; the same prompt can return different prose, and this project
does not claim otherwise. `AuditRecord` therefore records what identifies the
*attempt* - context digest, model, prompt and schema versions, provider usage -
so a future reader can establish which inputs and which model produced a stored
output. It does not promise that re-running reproduces it bit for bit, and
nothing in the codebase should be written as though it does.

## What is never recorded

No secrets, no API keys, no raw image bytes. The record holds a digest and
version strings; the context it summarises has no field capable of carrying a
credential or an image, and the canonical form raises on bytes rather than
encoding them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.application.synthesis.canonical import CANONICAL_FORMAT_VERSION, context_digest
from app.application.synthesis.context import CONTEXT_SCHEMA_VERSION, SynthesisContext
from app.application.synthesis.draft import SynthesisStatus
from app.application.synthesis.schemas import OUTPUT_SCHEMA_VERSION
from app.domain.synthesis.actions import FinalAction

PROMPT_VERSION_PLACEHOLDER = "synthesis-prompt/unset"
"""No synthesis prompt exists yet - 7B writes it.

A placeholder rather than an empty string, so a record produced before the
prompt existed is visibly distinguishable from one where the version was lost.
"""

MODEL_PLACEHOLDER = "unset"
"""Likewise: 7A never calls a model, and recording "" would read as a model
whose name went missing."""


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """What was asked, of what, and what came back."""

    context_digest: str
    context_schema_version: str
    canonical_format_version: str
    output_schema_version: str
    prompt_version: str
    model: str

    status: SynthesisStatus
    allowed_actions: tuple[FinalAction, ...]
    proposed_action: FinalAction | None = None
    accepted_action: FinalAction | None = None
    rejection_codes: tuple[str, ...] = ()

    reference_ids: tuple[str, ...] = ()
    """Sorted, so two records over the same facts compare directly."""

    missing_manifest: tuple[str, ...] = ()
    """§4: what was absent, listed rather than implied by omission."""

    trimmed_refs: tuple[str, ...] = ()
    """Anything the budget policy dropped, so a shorter context is visible as
    a decision rather than as an unexplained gap."""

    risk_state: str = ""
    suitability_state: str = ""
    data_quality_state: str = ""

    generated_at: datetime | None = None
    """From a `ClockPort` at the composition root. Never an ambient clock."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    """Provider-reported only. Never fabricated when a provider omits them -
    the Phase 6 rule, kept."""

    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_reproducible_input(self) -> bool:
        """Whether the *input* side can be reconstructed exactly.

        Deliberately named for the input. The model's prose is not reproducible
        and this property does not claim it is.
        """
        return bool(self.context_digest) and self.prompt_version != PROMPT_VERSION_PLACEHOLDER


def build_audit_record(
    context: SynthesisContext,
    status: SynthesisStatus,
    *,
    proposed_action: FinalAction | None = None,
    accepted_action: FinalAction | None = None,
    rejection_codes: tuple[str, ...] = (),
    trimmed_refs: tuple[str, ...] = (),
    prompt_version: str = PROMPT_VERSION_PLACEHOLDER,
    model: str = MODEL_PLACEHOLDER,
    generated_at: datetime | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> AuditRecord:
    """Record one synthesis attempt, successful or not.

    A failed attempt gets a record too. §22 keeps provider failure out of the
    market view, and the way to do that without losing the event is to record
    the failure as a failure.
    """
    return AuditRecord(
        context_digest=context_digest(context),
        context_schema_version=CONTEXT_SCHEMA_VERSION,
        canonical_format_version=CANONICAL_FORMAT_VERSION,
        output_schema_version=OUTPUT_SCHEMA_VERSION,
        prompt_version=prompt_version,
        model=model,
        status=status,
        allowed_actions=context.envelope.allowed,
        proposed_action=proposed_action,
        accepted_action=accepted_action,
        rejection_codes=rejection_codes,
        reference_ids=tuple(sorted(context.ref_ids)),
        missing_manifest=tuple(sorted(item.detail for item in context.missing)),
        trimmed_refs=trimmed_refs,
        risk_state=context.sizing_outcome.value if context.sizing_outcome else "NOT_SUPPLIED",
        suitability_state=(
            "UNDETERMINED" if context.no_trade_state is None else str(context.no_trade_state)
        ),
        data_quality_state=(
            context.data_quality_verdict.value if context.data_quality_verdict else "NOT_SUPPLIED"
        ),
        generated_at=generated_at,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

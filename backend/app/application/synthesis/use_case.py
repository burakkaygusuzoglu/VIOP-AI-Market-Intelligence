"""The synthesis flow, end to end (§22).

    SynthesisContext (already assembled from finished results)
        → deterministic entry trim        blockers never dropped
        → prompt                          one place, one version
        → token estimate vs budget        CONTEXT_TOO_LARGE if it will not fit
        → MarketSynthesisProvider         the only network step
        → deterministic validator         action, claims, references, numbers
        → ValidationReport.accept()       the only door to a usable draft
        → SynthesisOutcome + AuditRecord

Every failure along that path produces a **status**, never an action. There is
no branch in this module that turns a timeout, an oversized context or a
malformed response into WAIT or NO_TRADE, and the tests assert the absence.

## The order matters

The budget check happens **before** the provider is reached. A context that
cannot fit must not cost a paid request, and more importantly must not be sent
in a trimmed form that lost its blockers - so the trim runs first, the estimate
is taken from the real prompt, and an over-budget result stops here.

## What this module does not do

It does not assemble the context - `context.build_synthesis_context` owns that,
reading finished Phase 1-6 results. It does not compute anything financial. It
does not repair, downgrade or re-ask on invalid output: §15 is explicit that a
LONG proposed against a forced NO_TRADE is recorded as invalid, not quietly
converted into the permitted answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.application.ports.synthesis import MarketSynthesisProvider, SynthesisRequest
from app.application.ports.system import ClockPort
from app.application.synthesis.audit import AuditRecord, build_audit_record
from app.application.synthesis.budget import (
    ContextBudget,
    ContextBudgetExceededError,
    fit_to_budget,
)
from app.application.synthesis.context import SynthesisContext
from app.application.synthesis.draft import SynthesisOutcome, SynthesisStatus
from app.application.synthesis.errors import SynthesisFailure, SynthesisProviderError
from app.application.synthesis.prompt import SYNTHESIS_PROMPT_VERSION, build_prompt
from app.application.synthesis.schemas import OUTPUT_SCHEMA_VERSION
from app.application.synthesis.tokens import TokenBudget, estimate_tokens
from app.application.synthesis.validator import validate_synthesis


@dataclass(frozen=True, slots=True)
class SynthesisSettings:
    """Application policy for one synthesis run."""

    model: str = ""
    """No default. §11: an unconfigured deployment returns NOT_CONFIGURED
    rather than silently choosing a model whose behaviour nobody verified."""

    locale: str = "tr"
    max_output_tokens: int = 4096
    entries: ContextBudget = ContextBudget()
    tokens: TokenBudget | None = None
    """The context-window budget. ``None`` means no window was configured.

    Not defaulted, because a default would be an invented provider fact (§3).
    A deployment that has not stated its model's context window is treated
    exactly like one that has not stated its model: NOT_CONFIGURED.
    """

    @property
    def is_configured(self) -> bool:
        """Both a model and a context window are required to attempt a call."""
        return bool(self.model.strip()) and self.tokens is not None


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    """What one attempt produced: an outcome, and the record of how."""

    outcome: SynthesisOutcome
    audit: AuditRecord
    trimmed_refs: tuple[str, ...] = ()

    @property
    def status(self) -> SynthesisStatus:
        return self.outcome.status

    @property
    def final_action(self) -> object | None:
        """The accepted action, or ``None``.

        ``None`` means *no synthesis was accepted* - it does not mean NO_TRADE.
        A caller wanting a deterministic answer reads the `ActionEnvelope`,
        which never needed a model.
        """
        return self.outcome.draft.proposed_action if self.outcome.draft is not None else None


async def run_synthesis(
    context: SynthesisContext,
    provider: MarketSynthesisProvider | None,
    clock: ClockPort,
    settings: SynthesisSettings | None = None,
) -> SynthesisResult:
    """Run one synthesis attempt and return what it produced.

    ``provider`` may be ``None`` for a deployment with no adapter wired; that
    is NOT_CONFIGURED, reported as such, and no request is made.
    """
    policy = settings if settings is not None else SynthesisSettings()

    def failed(
        failure: SynthesisFailure, detail: str, trimmed: tuple[str, ...] = ()
    ) -> SynthesisResult:
        """Build a failed result.

        ``detail`` must already be safe to show a client. A provider's own
        message never reaches here - see the `SynthesisProviderError` handler
        below, which passes `public_detail`.
        """
        return SynthesisResult(
            outcome=SynthesisOutcome(status=failure.status, detail=detail),
            audit=build_audit_record(
                context,
                failure.status,
                rejection_codes=(failure.value,),
                trimmed_refs=trimmed,
                prompt_version=SYNTHESIS_PROMPT_VERSION,
                model=policy.model or "unset",
                generated_at=clock.now(),
            ),
            trimmed_refs=trimmed,
        )

    if provider is None or not policy.is_configured:
        missing = []
        if provider is None:
            missing.append("provider")
        if not policy.model.strip():
            missing.append("model")
        if policy.tokens is None:
            missing.append("context window")
        return failed(
            SynthesisFailure.NOT_CONFIGURED,
            f"synthesis is not configured: no {', no '.join(missing)}",
        )

    budget = policy.tokens
    if budget is None:  # pragma: no cover - `is_configured` already guarantees it
        # Narrowing for the type checker, and a guard that cannot fire: a
        # configured policy always carries a budget. Raising rather than
        # defaulting, because a default here would be the invented provider
        # fact this whole design removes.
        raise RuntimeError("a configured synthesis policy must carry a token budget")

    # Trim first: an over-budget context must never be sent with its blockers
    # removed, and `fit_to_budget` refuses rather than dropping one.
    try:
        trim = fit_to_budget(context, policy.entries)
    except ContextBudgetExceededError as error:
        return failed(SynthesisFailure.CONTEXT_TOO_LARGE, error.detail)

    prompt = build_prompt(trim.context, locale=policy.locale)
    estimate = estimate_tokens(prompt.system + prompt.user)
    if not budget.fits(estimate):
        # Before the provider, so an oversized request costs nothing and,
        # crucially, is never analysed in a reduced form. The whole request is
        # weighed - input, the output allowance and the reserve - not the
        # input alone (§3).
        return failed(
            SynthesisFailure.CONTEXT_TOO_LARGE,
            budget.explain(estimate),
            trim.removed,
        )

    try:
        draft = await provider.synthesize(
            SynthesisRequest(
                context=trim.context,
                prompt=prompt,
                locale=policy.locale,
                max_output_tokens=policy.max_output_tokens,
            )
        )
    except SynthesisProviderError as error:
        # `public_detail`, never `detail`. The internal one may name the
        # provider, a status code or a rejected credential; a test asserts a
        # key-shaped string cannot reach the response through this path.
        return failed(error.failure, error.public_detail, trim.removed)

    report = validate_synthesis(draft, trim.context)
    if not report.is_valid:
        # Recorded as invalid, never repaired and never downgraded into the
        # action the envelope would have permitted (§15).
        return SynthesisResult(
            outcome=SynthesisOutcome(
                status=SynthesisStatus.INVALID_OUTPUT,
                detail="; ".join(item.detail for item in report.rejections),
            ),
            audit=build_audit_record(
                trim.context,
                SynthesisStatus.INVALID_OUTPUT,
                proposed_action=draft.proposed_action,
                rejection_codes=tuple(code.value for code in report.codes),
                trimmed_refs=trim.removed,
                prompt_version=prompt.version,
                model=policy.model,
                generated_at=clock.now(),
            ),
            trimmed_refs=trim.removed,
        )

    accepted = report.accept(draft)
    return SynthesisResult(
        outcome=SynthesisOutcome(status=SynthesisStatus.SUCCESS, draft=accepted),
        audit=build_audit_record(
            trim.context,
            SynthesisStatus.SUCCESS,
            proposed_action=accepted.proposed_action,
            accepted_action=accepted.proposed_action,
            trimmed_refs=trim.removed,
            prompt_version=prompt.version,
            model=policy.model,
            generated_at=clock.now(),
        ),
        trimmed_refs=trim.removed,
    )


__all__ = [
    "OUTPUT_SCHEMA_VERSION",
    "SynthesisResult",
    "SynthesisSettings",
    "run_synthesis",
]

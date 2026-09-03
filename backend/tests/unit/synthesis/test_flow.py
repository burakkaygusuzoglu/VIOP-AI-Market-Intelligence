"""The synthesis flow, end to end (§30, §35, §36, §37).

Action safety, token budgeting, identifier stability and concurrency, all
driven through `run_synthesis` with a fake provider - no key, no network, no
paid request.

The assertion that recurs most is the one that matters most: when a synthesis
does not happen, **no action comes out**. Not WAIT, not NO_TRADE, not a
cautious default. A status, and the deterministic envelope alongside it.

Every value is TEST_FIXTURE data.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.application.ports.synthesis import SynthesisRequest
from app.application.synthesis.budget import ContextBudget
from app.application.synthesis.context import ContextFinding
from app.application.synthesis.draft import SynthesisDraft, SynthesisStatus
from app.application.synthesis.errors import SynthesisFailure, SynthesisProviderError
from app.application.synthesis.schemas import SynthesisOutputSchema
from app.application.synthesis.tokens import (
    TokenBudget,
    TokenBudgetNotConfiguredError,
    TokenEstimate,
    estimate_tokens,
)
from app.application.synthesis.use_case import SynthesisSettings, run_synthesis
from app.domain.analysis.evidence import EvidenceDirection
from app.domain.market.quality import DataQualityVerdict
from app.domain.risk.sizing import SizingOutcome
from app.domain.synthesis.actions import FinalAction
from app.domain.synthesis.references import ReferenceKind
from tests.factories_synthesis import (
    blocking_assessment,
    data_quality,
    make_ref,
    minimal_context,
    pending_assessment,
    sizing,
)
from tests.unit.synthesis.test_adapter import response_payload
from tests.unit.synthesis.test_context import context_with_evidence

MODEL = "fixture-synthesis-model"
BACKEND_ROOT = Path(__file__).resolve().parents[3]


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 3, 2, 12, 0, tzinfo=UTC)


@dataclass
class FakeProvider:
    """A `MarketSynthesisProvider` returning a draft the test chooses."""

    action: FinalAction = FinalAction.WAIT
    error: SynthesisProviderError | None = None
    payload: dict[str, object] | None = None
    seen: list[SynthesisRequest] = field(default_factory=list)

    async def synthesize(self, request: SynthesisRequest) -> SynthesisDraft:
        self.seen.append(request)
        if self.error is not None:
            raise self.error
        body = self.payload or response_payload(proposed_action=self.action.value)
        return SynthesisOutputSchema.model_validate(json.loads(json.dumps(body))).to_draft()


FIXTURE_WINDOW = TokenBudget(context_window=200_000, max_output_tokens=4_096, safety_reserve=2_000)
"""A configured window for tests.

Explicit rather than a default: production has no default either, because a
model's context size is a provider fact this project does not invent.
"""


def settings(**kwargs: object) -> SynthesisSettings:
    base: dict[str, object] = {"model": MODEL, "tokens": FIXTURE_WINDOW}
    base.update(kwargs)
    return SynthesisSettings(**base)  # type: ignore[arg-type]


async def run(context: object, provider: object, **kwargs: object):  # type: ignore[no-untyped-def]
    return await run_synthesis(context, provider, FixedClock(), settings(**kwargs))  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# §30: action safety end to end
# ----------------------------------------------------------------------


@pytest.mark.unit
async def test_risk_not_permitted_plus_long_is_rejected() -> None:
    context = minimal_context(position_sizing=sizing(SizingOutcome.NOT_PERMITTED))
    result = await run(context, FakeProvider(action=FinalAction.LONG))

    assert result.status is SynthesisStatus.INVALID_OUTPUT
    assert result.final_action is None
    assert "ACTION_NOT_PERMITTED" in result.audit.rejection_codes


@pytest.mark.unit
async def test_risk_not_permitted_plus_wait_is_rejected() -> None:
    """NO_TRADE is forced, so WAIT is not on offer either."""
    context = minimal_context(position_sizing=sizing(SizingOutcome.NOT_PERMITTED))
    result = await run(context, FakeProvider(action=FinalAction.WAIT))
    assert result.status is SynthesisStatus.INVALID_OUTPUT


@pytest.mark.unit
async def test_risk_not_permitted_plus_no_trade_is_accepted() -> None:
    context = minimal_context(position_sizing=sizing(SizingOutcome.NOT_PERMITTED))
    result = await run(context, FakeProvider(action=FinalAction.NO_TRADE))

    assert result.status is SynthesisStatus.SUCCESS
    assert result.final_action is FinalAction.NO_TRADE


@pytest.mark.unit
async def test_pending_confirmation_plus_long_is_rejected() -> None:
    context = minimal_context(no_trade_assessment=pending_assessment())
    result = await run(context, FakeProvider(action=FinalAction.LONG))

    assert result.status is SynthesisStatus.INVALID_OUTPUT
    assert result.final_action is None


@pytest.mark.unit
async def test_pending_confirmation_plus_wait_is_accepted() -> None:
    context = minimal_context(no_trade_assessment=pending_assessment())
    result = await run(context, FakeProvider(action=FinalAction.WAIT))

    assert result.status is SynthesisStatus.SUCCESS
    assert result.final_action is FinalAction.WAIT


@pytest.mark.unit
async def test_a_clean_envelope_accepts_a_grounded_long() -> None:
    result = await run(minimal_context(), FakeProvider(action=FinalAction.LONG))
    assert result.status is SynthesisStatus.SUCCESS
    assert result.final_action is FinalAction.LONG


@pytest.mark.unit
async def test_a_clean_bearish_envelope_accepts_a_grounded_short() -> None:
    context = minimal_context(direction=EvidenceDirection.BEARISH)
    result = await run(context, FakeProvider(action=FinalAction.SHORT))
    assert result.status is SynthesisStatus.SUCCESS
    assert result.final_action is FinalAction.SHORT


@pytest.mark.unit
async def test_a_rejected_action_is_never_converted_into_a_permitted_one() -> None:
    """§15: recorded as invalid, not quietly turned into WAIT."""
    context = minimal_context(no_trade_assessment=pending_assessment())
    result = await run(context, FakeProvider(action=FinalAction.LONG))

    assert result.final_action is None
    assert result.outcome.draft is None
    assert result.audit.accepted_action is None
    assert result.audit.proposed_action is FinalAction.LONG, "the proposal is still recorded"


# ----------------------------------------------------------------------
# §30: failure is never an action
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failure", "status"),
    (
        (SynthesisFailure.TIMEOUT, SynthesisStatus.PROVIDER_FAILURE),
        (SynthesisFailure.NETWORK, SynthesisStatus.PROVIDER_FAILURE),
        (SynthesisFailure.RATE_LIMITED, SynthesisStatus.PROVIDER_FAILURE),
        (SynthesisFailure.AUTHENTICATION, SynthesisStatus.PROVIDER_FAILURE),
        (SynthesisFailure.PROVIDER_REJECTED, SynthesisStatus.PROVIDER_FAILURE),
        (SynthesisFailure.INVALID_OUTPUT, SynthesisStatus.INVALID_OUTPUT),
    ),
)
async def test_a_provider_failure_produces_a_status_and_no_action(
    failure: SynthesisFailure, status: SynthesisStatus
) -> None:
    provider = FakeProvider(error=SynthesisProviderError(failure, "internal detail"))
    result = await run(minimal_context(), provider)

    assert result.status is status
    assert result.final_action is None
    assert result.outcome.draft is None


@pytest.mark.unit
async def test_an_unconfigured_deployment_reports_not_configured() -> None:
    result = await run_synthesis(minimal_context(), None, FixedClock(), SynthesisSettings())
    assert result.status is SynthesisStatus.NOT_CONFIGURED
    assert result.final_action is None


@pytest.mark.unit
async def test_a_missing_model_reports_not_configured_without_calling_the_provider() -> None:
    provider = FakeProvider()
    result = await run_synthesis(
        minimal_context(), provider, FixedClock(), SynthesisSettings(model="")
    )
    assert result.status is SynthesisStatus.NOT_CONFIGURED
    assert provider.seen == [], "an unconfigured deployment must not call a provider"


@pytest.mark.unit
async def test_the_envelope_is_still_reported_when_synthesis_fails() -> None:
    """The honest fallback: deterministic policy never needed a model."""
    context = minimal_context(no_trade_assessment=blocking_assessment())
    provider = FakeProvider(error=SynthesisProviderError(SynthesisFailure.TIMEOUT, "x"))
    result = await run(context, provider)

    assert result.audit.allowed_actions == (FinalAction.NO_TRADE,)
    assert result.final_action is None


# ----------------------------------------------------------------------
# §35: token budget
# ----------------------------------------------------------------------


def _blocking_context(bull_count: int = 30):  # type: ignore[no-untyped-def]
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


@pytest.mark.unit
async def test_a_small_context_fits_and_reaches_the_provider() -> None:
    provider = FakeProvider()
    result = await run(minimal_context(), provider)

    assert result.status is SynthesisStatus.SUCCESS
    assert len(provider.seen) == 1


TIGHT = TokenBudget(context_window=1_000, max_output_tokens=100, safety_reserve=10)
"""A window far too small for any real prompt."""


@pytest.mark.unit
async def test_an_oversized_prompt_is_refused_before_the_provider() -> None:
    """§12 and §35: the request must cost nothing and must not be trimmed unsafely."""
    provider = FakeProvider()
    result = await run(minimal_context(), provider, tokens=TIGHT)

    assert result.status is SynthesisStatus.CONTEXT_TOO_LARGE
    assert provider.seen == [], "an over-budget request must never reach the provider"


@pytest.mark.unit
async def test_context_too_large_is_not_a_market_view() -> None:
    result = await run(minimal_context(), FakeProvider(), tokens=TIGHT)
    assert result.final_action is None
    assert result.status.value not in {item.value for item in FinalAction}


@pytest.mark.unit
async def test_an_unconfigured_context_window_is_not_configured_not_a_guess() -> None:
    """§3: a window is a provider fact, so an unset one blocks the call."""
    provider = FakeProvider()
    result = await run_synthesis(
        minimal_context(), provider, FixedClock(), SynthesisSettings(model=MODEL, tokens=None)
    )

    assert result.status is SynthesisStatus.NOT_CONFIGURED
    assert "context window" in result.outcome.detail
    assert provider.seen == []


# ----------------------------------------------------------------------
# §3: the whole request is budgeted, not the input alone
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_input_may_fit_while_input_plus_output_does_not() -> None:
    """The classic error this accounting exists to prevent.

    A prompt occupying almost the whole window leaves no room for the answer;
    budgeting the input alone would pass it and the provider would refuse the
    call after it had been paid for.
    """
    budget = TokenBudget(context_window=10_000, max_output_tokens=4_000, safety_reserve=1_000)
    estimate = TokenEstimate(characters=0, estimated_tokens=5_500)

    assert estimate.estimated_tokens < budget.context_window, "the input alone would fit"
    assert budget.total_for(estimate) > budget.context_window
    assert not budget.fits(estimate)


@pytest.mark.unit
def test_the_exact_configured_boundary_fits_and_one_more_does_not() -> None:
    budget = TokenBudget(context_window=10_000, max_output_tokens=4_000, safety_reserve=1_000)
    assert budget.derived_input_allowance == 5_000

    assert budget.fits(TokenEstimate(characters=0, estimated_tokens=5_000))
    assert not budget.fits(TokenEstimate(characters=0, estimated_tokens=5_001))


@pytest.mark.unit
def test_the_safety_reserve_is_actually_withheld() -> None:
    generous = TokenBudget(context_window=10_000, max_output_tokens=4_000, safety_reserve=0)
    cautious = TokenBudget(context_window=10_000, max_output_tokens=4_000, safety_reserve=1_000)

    assert generous.derived_input_allowance - cautious.derived_input_allowance == 1_000
    boundary = TokenEstimate(characters=0, estimated_tokens=5_500)
    assert generous.fits(boundary)
    assert not cautious.fits(boundary), "the reserve must actually cost allowance"


@pytest.mark.unit
def test_an_output_allowance_cannot_silently_consume_the_whole_window() -> None:
    """§3: output configuration may not exceed the total budget."""
    with pytest.raises(ValueError, match="leaves no room"):
        TokenBudget(context_window=5_000, max_output_tokens=5_000, safety_reserve=0)
    with pytest.raises(ValueError, match="leaves no room"):
        TokenBudget(context_window=5_000, max_output_tokens=4_000, safety_reserve=1_000)


@pytest.mark.unit
def test_an_application_cap_may_lower_the_allowance_but_never_raise_it() -> None:
    budget = TokenBudget(
        context_window=10_000,
        max_output_tokens=4_000,
        safety_reserve=1_000,
        max_prompt_tokens=2_000,
    )
    assert budget.usable_prompt_tokens == 2_000

    with pytest.raises(ValueError, match="never raise it"):
        TokenBudget(
            context_window=10_000,
            max_output_tokens=4_000,
            safety_reserve=1_000,
            max_prompt_tokens=9_000,
        )


@pytest.mark.unit
def test_a_missing_context_window_refuses_to_construct() -> None:
    """No default, because a default would be an invented provider fact."""
    with pytest.raises(TokenBudgetNotConfiguredError, match="does not invent"):
        TokenBudget(context_window=0, max_output_tokens=100)


@pytest.mark.unit
def test_the_failure_explanation_names_all_four_quantities() -> None:
    budget = TokenBudget(context_window=10_000, max_output_tokens=4_000, safety_reserve=1_000)
    detail = budget.explain(TokenEstimate(characters=0, estimated_tokens=9_000))

    assert "estimated input 9000" in detail
    assert "output allowance 4000" in detail
    assert "safety reserve 1000" in detail
    assert "10000 token window" in detail
    assert "estimate, not a token count" in detail


@pytest.mark.unit
async def test_mandatory_context_over_the_entry_budget_fails_explicitly() -> None:
    provider = FakeProvider()
    result = await run(_blocking_context(), provider, entries=ContextBudget(max_entries=1))

    assert result.status is SynthesisStatus.CONTEXT_TOO_LARGE
    assert provider.seen == []


@pytest.mark.unit
async def test_a_blocker_survives_trimming_and_reaches_the_prompt() -> None:
    context = _blocking_context(bull_count=40)
    provider = FakeProvider(action=FinalAction.NO_TRADE)
    result = await run(
        context,
        provider,
        entries=ContextBudget(
            max_entries=10, keep_neutral_evidence=False, keep_vision_observations=False
        ),
    )

    assert result.trimmed_refs, "optional evidence should have been trimmed"
    sent = provider.seen[0]
    assert context.suitability_findings[0].ref.ref_id in sent.context.ref_ids
    assert context.missing[0].ref.ref_id in sent.context.ref_ids
    assert context.contradictions[0].ref.ref_id in sent.context.ref_ids


@pytest.mark.unit
async def test_trimming_is_deterministic_across_runs() -> None:
    context = _blocking_context(bull_count=40)
    budget = ContextBudget(max_entries=12)
    removed = set()
    for _ in range(3):
        result = await run(context, FakeProvider(action=FinalAction.NO_TRADE), entries=budget)
        removed.add(result.trimmed_refs)
    assert len(removed) == 1


@pytest.mark.unit
def test_the_token_count_is_labelled_an_estimate() -> None:
    """§13: no claim of exactness anywhere."""
    estimate = estimate_tokens("x" * 1000)
    assert not estimate.is_exact
    assert "estimate" in estimate.method
    assert estimate.estimated_tokens > 1000 / 2.5, "the safety margin must be applied"


@pytest.mark.unit
async def test_a_provider_is_never_invoked_when_the_total_budget_fails() -> None:
    """§3: not merely refused afterwards - never called at all."""
    provider = FakeProvider()
    result = await run(_blocking_context(bull_count=5), provider, tokens=TIGHT)

    assert result.status is SynthesisStatus.CONTEXT_TOO_LARGE
    assert provider.seen == []


# ----------------------------------------------------------------------
# §36: identifier stability
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_adding_unrelated_evidence_does_not_renumber_existing_facts() -> None:
    """The defect this phase fixed, asserted directly.

    Phase 7A numbered sequentially, so inserting one item shifted every id and
    a stored citation silently re-pointed at a different fact.
    """
    from app.application.synthesis.context import _to_context_evidence  # noqa: PLC0415
    from app.domain.analysis.evidence import EvidenceCategory  # noqa: PLC0415
    from app.domain.synthesis.references import AuthorityClass  # noqa: PLC0415
    from tests.factories_synthesis import evidence  # noqa: PLC0415

    a = evidence(reason="A", category=EvidenceCategory.TREND)
    b = evidence(reason="B", category=EvidenceCategory.STRUCTURE)
    inserted = evidence(reason="NEW", category=EvidenceCategory.MOMENTUM)

    def ids(items: tuple[object, ...]) -> dict[str, str]:
        built = _to_context_evidence(
            items,  # type: ignore[arg-type]
            ReferenceKind.BULL_EVIDENCE,
            AuthorityClass.CALCULATED_METRIC,
        )
        return {item.reason: item.ref.ref_id for item in built}

    before = ids((a, b))
    after = ids((a, inserted, b))

    assert before["A"] == after["A"], "an unrelated insertion renumbered A"
    assert before["B"] == after["B"], "an unrelated insertion renumbered B"
    assert after["NEW"] not in before.values()


@pytest.mark.unit
def test_input_order_does_not_change_identifiers() -> None:
    from app.application.synthesis.context import _to_context_evidence  # noqa: PLC0415
    from app.domain.analysis.evidence import EvidenceCategory  # noqa: PLC0415
    from app.domain.synthesis.references import AuthorityClass  # noqa: PLC0415
    from tests.factories_synthesis import evidence  # noqa: PLC0415

    a = evidence(reason="A", category=EvidenceCategory.TREND)
    b = evidence(reason="B", category=EvidenceCategory.STRUCTURE)

    def ids(items: tuple[object, ...]) -> set[str]:
        return {
            item.ref.ref_id
            for item in _to_context_evidence(
                items,  # type: ignore[arg-type]
                ReferenceKind.BULL_EVIDENCE,
                AuthorityClass.CALCULATED_METRIC,
            )
        }

    assert ids((a, b)) == ids((b, a))


@pytest.mark.unit
def test_identifiers_are_stable_across_a_fresh_process() -> None:
    """No dependence on Python's randomised `hash()`.

    Run twice in separate interpreters, which is where a `hash()`-derived
    identifier would differ and a digest-derived one cannot.
    """
    script = (
        "from app.domain.synthesis.references import ReferenceKind, content_ref_id;"
        "print(content_ref_id(ReferenceKind.BULL_EVIDENCE, 'TREND', 'EMA_ALIGNMENT', '1H'))"
    )
    outputs = set()
    for _ in range(2):
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=BACKEND_ROOT,
            check=True,
        )
        outputs.add(result.stdout.strip())

    assert len(outputs) == 1, f"identifier differed between processes: {outputs}"
    assert next(iter(outputs)).startswith("EV-BULL-")


@pytest.mark.unit
def test_different_content_yields_different_identifiers() -> None:
    from app.domain.synthesis.references import content_ref_id  # noqa: PLC0415

    first = content_ref_id(ReferenceKind.BULL_EVIDENCE, "AB", "C")
    second = content_ref_id(ReferenceKind.BULL_EVIDENCE, "A", "BC")
    assert first != second, "the separator must prevent concatenation collisions"


@pytest.mark.unit
def test_the_same_kind_and_content_always_agree() -> None:
    from app.domain.synthesis.references import content_ref_id  # noqa: PLC0415

    assert content_ref_id(ReferenceKind.BULL_EVIDENCE, "X") == content_ref_id(
        ReferenceKind.BULL_EVIDENCE, "X"
    )
    assert content_ref_id(ReferenceKind.BULL_EVIDENCE, "X") != content_ref_id(
        ReferenceKind.BEAR_EVIDENCE, "X"
    ), "the kind namespace must separate identical content"


@pytest.mark.unit
def test_a_context_refuses_duplicate_identifiers() -> None:
    context = context_with_evidence()
    with pytest.raises(ValueError, match="duplicate reference id"):
        replace(context, bull_evidence=context.bull_evidence + context.bull_evidence)


# ----------------------------------------------------------------------
# §37: concurrency
# ----------------------------------------------------------------------


@pytest.mark.unit
async def test_concurrent_syntheses_do_not_cross_contexts() -> None:
    """Phase 6 found a real process-global defect; this checks for another.

    Two simultaneous runs over different contexts must each see their own
    envelope, their own prompt and their own digest.
    """
    blocked = minimal_context(position_sizing=sizing(SizingOutcome.NOT_PERMITTED))
    clean = minimal_context()

    blocked_provider = FakeProvider(action=FinalAction.NO_TRADE)
    clean_provider = FakeProvider(action=FinalAction.LONG)

    results = await asyncio.gather(
        *[
            run(blocked, blocked_provider) if index % 2 else run(clean, clean_provider)
            for index in range(12)
        ]
    )

    for result in results:
        assert result.status is SynthesisStatus.SUCCESS

    blocked_actions = {r.final_action for r in results[1::2]}
    clean_actions = {r.final_action for r in results[0::2]}
    assert blocked_actions == {FinalAction.NO_TRADE}
    assert clean_actions == {FinalAction.LONG}

    for request in blocked_provider.seen:
        assert request.context.envelope.allowed == (FinalAction.NO_TRADE,)
    for request in clean_provider.seen:
        assert FinalAction.LONG in request.context.envelope.allowed


@pytest.mark.unit
async def test_concurrent_runs_produce_their_own_digests() -> None:
    contexts = [
        minimal_context(),
        minimal_context(direction=EvidenceDirection.BEARISH),
        minimal_context(quality=data_quality(DataQualityVerdict.BLOCKED)),
    ]
    providers = [
        FakeProvider(action=FinalAction.LONG),
        FakeProvider(action=FinalAction.SHORT),
        FakeProvider(action=FinalAction.NO_TRADE),
    ]
    results = await asyncio.gather(
        *[run(ctx, provider) for ctx, provider in zip(contexts, providers, strict=True)]
    )

    digests = [result.audit.context_digest for result in results]
    assert len(set(digests)) == 3, "concurrent runs shared a digest"


@pytest.mark.unit
def test_no_synthesis_module_holds_mutable_global_state() -> None:
    """§37: a per-request write to a module global is how Phase 6 broke."""
    import ast  # noqa: PLC0415

    roots = (
        BACKEND_ROOT / "app" / "application" / "synthesis",
        BACKEND_ROOT / "app" / "domain" / "synthesis",
        BACKEND_ROOT / "app" / "adapters" / "synthesis",
    )
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                for inner in ast.walk(node):
                    assert not isinstance(inner, ast.Global), (
                        f"{path.name}:{node.name} mutates a module global"
                    )

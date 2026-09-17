"""On-demand analysis of user-supplied historical data (§18, §20).

One request in, one finished analysis out. Nothing is stored, and there is
deliberately no `GET /analysis/{id}` — an id you can fetch back is a promise of
persistence this phase does not implement, and Phase 7 already removed one
endpoint for looking operational without being reachable.

## Order of operations

    body (extra="forbid")     -> a forged derived field is a 422, not a value
      -> AnalysisRequest      -> only user-suppliable things exist on it
      -> run_analysis         -> every number, from the existing engines
      -> synthesis (optional) -> from a context built out of that result
      -> project              -> the public shape

Synthesis failing, or not being configured, changes nothing above it. The
deterministic analysis is returned either way, with a typed synthesis status,
because a provider outage is a fact about this system and not about the market.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_clock
from app.api.schemas.analysis import (
    AnalysisRequestBody,
    AnalysisResponse,
    SegmentResponse,
    SynthesisResponse,
)
from app.api.schemas.analysis_projection import project
from app.application.analysis.limits import InputTooLargeError
from app.application.analysis.orchestrator import (
    AnalysisInputError,
    AnalysisOutcome,
    run_analysis,
)
from app.application.analysis.request import (
    AccountCurrency,
    AnalysisRequest,
    TimeframeDataset,
)
from app.application.ports.contract_metadata import ContractMetadataProvider
from app.application.ports.market_data import CandleTextParser
from app.application.ports.synthesis import MarketSynthesisProvider
from app.application.ports.system import ClockPort
from app.application.synthesis.context import SynthesisContext, build_synthesis_context
from app.application.synthesis.rendering import (
    FactSegment,
    NarrativeSegment,
    ObservationSegment,
    render_segments,
)
from app.application.synthesis.use_case import SynthesisSettings, run_synthesis
from app.domain.analysis.evidence import EvidenceDirection
from app.domain.analysis.scenarios import ScenarioCase
from app.domain.common.enums import Timeframe
from app.domain.risk.sizing import AccountState, RiskInputError, RiskMode, RiskPolicy

router = APIRouter(prefix="/analysis", tags=["analysis"])

logger = logging.getLogger("app.api.analysis")

_TIMEFRAMES = {
    "1D": Timeframe.D1,
    "1H": Timeframe.H1,
    "15M": Timeframe.M15,
    "5M": Timeframe.M5,
}


def get_candle_parser() -> CandleTextParser:
    """The parser for user-supplied OHLCV text.

    Unimplemented here on purpose. The composition root binds the CSV adapter;
    leaving no default means a route cannot silently acquire a parser of its
    own, which is how the API layer would end up importing infrastructure.
    """
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "PARSER_NOT_CONFIGURED", "detail": "Veri okuyucu yapılandırılmamış."},
    )


def get_contract_metadata() -> ContractMetadataProvider | None:
    """Contract metadata, when a provider is composed.

    ``None`` is a legitimate answer and is what this deployment returns: no
    verified VİOP contract specifications have been loaded, so every
    futures-dependent calculation reports itself unavailable rather than being
    computed from a default (§10).
    """
    return None


def get_synthesizer() -> MarketSynthesisProvider | None:
    """The synthesis provider, or ``None`` when unconfigured."""
    return None


def get_synthesis_settings() -> SynthesisSettings:
    return SynthesisSettings()


@router.post("", response_model=AnalysisResponse, status_code=status.HTTP_200_OK)
async def analyse(
    body: AnalysisRequestBody,
    clock: Annotated[ClockPort, Depends(get_clock)],
    parser: Annotated[CandleTextParser, Depends(get_candle_parser)],
    contracts: Annotated[ContractMetadataProvider | None, Depends(get_contract_metadata)],
    synthesizer: Annotated[MarketSynthesisProvider | None, Depends(get_synthesizer)],
    synthesis_settings: Annotated[SynthesisSettings, Depends(get_synthesis_settings)],
) -> AnalysisResponse:
    """Analyse the supplied historical data and return an ephemeral snapshot."""
    request = _to_domain_request(body)

    try:
        outcome = await run_analysis(request, parser=parser, clock=clock, contracts=contracts)
    except AnalysisInputError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": error.code, "detail": str(error)},
        ) from error
    except InputTooLargeError as error:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": error.code, "detail": str(error)},
        ) from error

    synthesis = await _synthesise(outcome, synthesizer, synthesis_settings, clock)
    return project(outcome, synthesis)


def _to_domain_request(body: AnalysisRequestBody) -> AnalysisRequest:
    """Map the wire body onto the application request.

    Every failure here is the client's input being wrong, so each becomes a 422
    with a stable code. Nothing is coerced into working.
    """
    account: AccountState | None = None
    currency: AccountCurrency | None = None
    if body.account is not None:
        try:
            account = AccountState(
                equity=_decimal(body.account.equity, "equity"),
                used_margin=_decimal(body.account.used_margin, "used_margin"),
            )
        except RiskInputError as error:
            raise _invalid("INVALID_ACCOUNT", str(error)) from error
        if body.account.currency is not None:
            currency = AccountCurrency(code=body.account.currency)

    policy: RiskPolicy | None = None
    if body.risk is not None:
        try:
            policy = RiskPolicy(
                mode=RiskMode(body.risk.mode),
                fixed_risk=_optional_decimal(body.risk.fixed_risk, "fixed_risk"),
                risk_ratio=_optional_decimal(body.risk.risk_ratio, "risk_ratio"),
                max_contracts=body.risk.max_contracts,
            )
        except (RiskInputError, ValueError) as error:
            raise _invalid("INVALID_RISK_SETTINGS", str(error)) from error

    return AnalysisRequest(
        symbol=body.symbol.strip(),
        datasets=tuple(
            TimeframeDataset(
                timeframe=_TIMEFRAMES[item.timeframe],
                content=item.content,
                source_name=item.source_name.strip() or item.timeframe,
            )
            for item in body.datasets
        ),
        account=account,
        risk_policy=policy,
        currency=currency,
        entry_price=_optional_decimal(body.entry_price, "entry_price"),
        stop_price=_optional_decimal(body.stop_price, "stop_price"),
    )


def _invalid(code: str, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": code, "detail": detail},
    )


def _decimal(raw: str, name: str) -> Decimal:
    try:
        value = Decimal(raw.strip())
    except (InvalidOperation, ValueError) as error:
        raise _invalid("INVALID_NUMBER", f"{name} is not a number") from error
    if not value.is_finite():
        raise _invalid("INVALID_NUMBER", f"{name} must be finite")
    return value


def _optional_decimal(raw: str | None, name: str) -> Decimal | None:
    if raw is None or not raw.strip():
        return None
    return _decimal(raw, name)


def _narrated_direction(outcome: AnalysisOutcome) -> EvidenceDirection | None:
    """Which case synthesis should narrate.

    A **presentation** choice, not a market judgement, and it changes nothing
    deterministic: both directional cases are returned in the response either
    way, each with its own scenario, quality and suitability verdict.

    The user's own intent wins when they supplied an entry and a stop — the
    direction their trade implies is not something to guess at. Otherwise the
    case with the higher setup-quality score is narrated, which is a number the
    Phase 4 engine already produced rather than a new comparison invented here.
    Ties resolve to the bull case by fixed convention, so the choice stays
    deterministic for the same input.
    """
    if outcome.risk.direction is not None:
        return (
            EvidenceDirection.BULLISH
            if outcome.risk.direction.value == "LONG"
            else EvidenceDirection.BEARISH
        )
    analysis = outcome.analysis
    if analysis is None:
        return None
    bull = analysis.scenarios.case(ScenarioCase.BULL).quality
    bear = analysis.scenarios.case(ScenarioCase.BEAR).quality
    bull_score = bull.score if bull is not None else -1
    bear_score = bear.score if bear is not None else -1
    return EvidenceDirection.BEARISH if bear_score > bull_score else EvidenceDirection.BULLISH


async def _synthesise(
    outcome: AnalysisOutcome,
    provider: MarketSynthesisProvider | None,
    settings: SynthesisSettings,
    clock: ClockPort,
) -> SynthesisResponse:
    """Run synthesis over the real analysis, or report why it did not.

    Every early return is a *system* status. None of them is an action, and in
    particular none of them is WAIT — a provider that did not answer has said
    nothing about the market (§22, §33).
    """
    analysis = outcome.analysis
    direction = _narrated_direction(outcome)
    if analysis is None or direction is None:
        return SynthesisResponse(
            status="NOT_APPLICABLE",
            detail="Sentez için yeterli deterministik analiz yok.",
        )

    assessment = next(
        (verdict for candidate, verdict in outcome.suitability if candidate is direction),
        None,
    )
    if assessment is None:
        return SynthesisResponse(
            status="NOT_APPLICABLE",
            detail="Uygunluk değerlendirmesi üretilemedi.",
        )

    scenario_case = (
        ScenarioCase.BULL if direction is EvidenceDirection.BULLISH else ScenarioCase.BEAR
    )
    scenario = analysis.scenarios.case(scenario_case)

    # Assembling the context is the last thing here that can raise, and it
    # must not be able to cost the caller the analysis.
    #
    # Found by the §4 bound audit: an oscillating price series produced two
    # evidence items identical in every field the context shows, the context
    # refused the duplicate reference id, and the `ValueError` left the route as
    # a 500 - discarding a complete, correct deterministic analysis because an
    # optional narration layer could not be built. The duplicate itself is fixed
    # in `_to_context_evidence`; this is the guard that keeps the *class* of
    # failure contained, because synthesis being unable to run is a system
    # status and never an opinion about the market (§22, §33).
    try:
        context = build_synthesis_context(
            analysis,
            direction,
            assessment,
            contradictions=analysis.contradictions,
            setup_quality=scenario.quality,
            entry_quality=scenario.entry,
            scenario_set=analysis.scenarios,
            sizing=outcome.risk.sizing,
        )
    except (ValueError, TypeError, KeyError) as error:
        logger.warning(
            "synthesis context could not be built",
            extra={"reason": type(error).__name__},
        )
        return SynthesisResponse(
            status="CONTEXT_UNAVAILABLE",
            detail=("Sentez bağlamı oluşturulamadı; deterministik analiz etkilenmedi."),
        )

    result = await run_synthesis(context, provider, clock, settings)
    envelope = context.envelope

    if result.outcome.status.value != "SUCCESS" or result.outcome.draft is None:
        return SynthesisResponse(
            status=result.outcome.status.value,
            detail=result.outcome.detail,
            allowed_actions=tuple(action.value for action in envelope.allowed),
            context_digest=result.audit.context_digest,
        )

    draft = result.outcome.draft
    return SynthesisResponse(
        status="SUCCESS",
        detail="",
        final_action=draft.proposed_action.value,
        allowed_actions=tuple(action.value for action in envelope.allowed),
        summary=_segments(draft.summary, context),
        bull_case=_segments(draft.bull.narrative, context),
        bear_case=_segments(draft.bear.narrative, context),
        neutral_case=_segments(draft.neutral.narrative, context),
        devils_advocate=_segments(draft.devils_advocate.challenge, context),
        context_digest=result.audit.context_digest,
    )


def _segments(text: str, context: SynthesisContext) -> tuple[SegmentResponse, ...]:
    """Resolve a model's narrative into typed segments for the wire.

    `render_segments` replaces every `{{FACT-...}}` / `{{VIS-...}}` placeholder
    with the deterministic value from the context registry. So a number the
    client displays came from the analysis, not from anything the model typed —
    the Phase 7 guarantee, carried across the API boundary rather than
    re-established on the other side.

    Observations stay a distinct kind from facts. Both are Python-rendered and
    neither can be altered by prose, but one was calculated and the other was
    read off a picture, and the client must be able to say which.
    """
    return tuple(_segment(item) for item in render_segments(text, context))


def _segment(segment: NarrativeSegment) -> SegmentResponse:
    if isinstance(segment, FactSegment):
        return SegmentResponse(
            kind="fact",
            fact_id=segment.ref_id,
            fact_label=segment.name,
            fact_value=segment.value + (f" {segment.unit}" if segment.unit else ""),
            fact_source="CALCULATED",
        )
    if isinstance(segment, ObservationSegment):
        return SegmentResponse(
            kind="observation",
            fact_id=segment.ref_id,
            fact_label=segment.field_name,
            fact_value=segment.value,
            fact_source=segment.source,
        )
    return SegmentResponse(kind="text", text=segment.text)

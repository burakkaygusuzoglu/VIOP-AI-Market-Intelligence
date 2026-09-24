"""Domain to Shadow response, and nothing else (Phase 14 Part 2A).

Every function here is a rename. Nothing computes, compares, sums, averages or
decides: a value that is not in the stored record does not appear in the
response, and a value that is ``None`` stays ``None`` rather than becoming a
zero, an empty string or a dash. That rule is what keeps "no approved
quantity" from being rendered as "approved 0".

Decimals are formatted with ``str``, never a float: a price that went through
binary floating point on its way to a browser would no longer be the price the
rules saw.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.api.schemas.shadow import (
    EntrySketchResponse,
    PriceDevelopmentResponse,
    ShadowCapabilityResponse,
    ShadowEntryResponse,
    ShadowEvidenceResponse,
    ShadowJournalPageResponse,
    ShadowLimitsResponse,
    ShadowOutcomePageResponse,
    ShadowOutcomeResponse,
    ShadowRunListResponse,
    ShadowRunResponse,
    ShadowStrategyResponse,
    TimeframeEvidenceResponse,
    TimeframeReadingsResponse,
)
from app.application.shadow.ports import StoredShadowRun
from app.application.shadow.workspace import ShadowCapability
from app.domain.shadow.decision import (
    EntrySketch,
    JournalEntryKind,
    ShadowDecision,
    ShadowEvidence,
    TimeframeEvidence,
    TimeframeReadings,
)
from app.domain.shadow.outcome import PriceDevelopment, ShadowOutcomeRecord
from app.domain.shadow.run import EndReason, ShadowRunStatus

__all__ = [
    "capability_response",
    "journal_page",
    "outcome_page",
    "run_list",
    "run_response",
]

_COMPLETE = {EndReason.STREAM_ENDED}
_PARTIAL = {EndReason.CANCELLED, EndReason.OBSERVATION_LIMIT, EndReason.SHUTDOWN}


def _moment(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _amount(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _completeness(run: StoredShadowRun) -> str:
    """How much of the intended observation actually happened.

    Decided here from the stored end reason, never by a client and never by
    counting rows: a run that stopped early is not COMPLETE because it has
    entries, and one that failed says INTERRUPTED rather than going quiet.
    """
    if run.status is ShadowRunStatus.OBSERVING:
        return "OBSERVING"
    if run.end_reason in _COMPLETE:
        return "COMPLETE"
    if run.end_reason in _PARTIAL:
        return "PARTIAL"
    return "INTERRUPTED"


def run_response(run: StoredShadowRun) -> ShadowRunResponse:
    return ShadowRunResponse(
        run_id=run.run_id,
        configuration=run.configuration,
        source_id=run.source_id,
        instrument_label=run.instrument_label,
        provenance=run.provenance,  # type: ignore[arg-type]
        market_currency=run.market_currency,  # type: ignore[arg-type]
        strategy_id=run.strategy_id,
        strategy_version=run.strategy_version,
        strategy_parameters=dict(run.strategy_parameters),
        driver=run.driver.value,  # type: ignore[arg-type]
        timeframes=[timeframe.value for timeframe in run.timeframes],  # type: ignore[misc]
        required_timeframes=[t.value for t in run.required_timeframes],  # type: ignore[misc]
        status=run.status.value,
        end_reason=None if run.end_reason is None else run.end_reason.value,
        failure_code=run.failure_code,
        observations=run.observations,
        decisions=run.decisions,
        entries=run.entries,
        first_boundary=_moment(run.first_boundary),
        last_boundary=_moment(run.last_boundary),
        started_at=run.started_at.isoformat(),
        ended_at=_moment(run.ended_at),
        completeness=_completeness(run),  # type: ignore[arg-type]
    )


def run_list(runs: tuple[StoredShadowRun, ...], total: int) -> ShadowRunListResponse:
    return ShadowRunListResponse(items=[run_response(run) for run in runs], total=total)


def _readings(item: TimeframeReadings) -> TimeframeReadingsResponse:
    return TimeframeReadingsResponse(
        timeframe=item.timeframe.value,  # type: ignore[arg-type]
        ema_fast=item.ema_fast,
        ema_slow=item.ema_slow,
        rsi=item.rsi,
        atr=item.atr,
        adx=item.adx,
    )


def _timeframe(item: TimeframeEvidence) -> TimeframeEvidenceResponse:
    return TimeframeEvidenceResponse(
        timeframe=item.timeframe.value,  # type: ignore[arg-type]
        available=item.available,
        freshness=item.freshness,
        integrity=item.integrity,
        confirmed_count=item.confirmed_count,
        reasons=list(item.reasons),
        last_coverage_end=_moment(item.last_coverage_end),
    )


def _evidence(evidence: ShadowEvidence | None) -> ShadowEvidenceResponse | None:
    if evidence is None:
        return None
    return ShadowEvidenceResponse(
        provenance=evidence.provenance,  # type: ignore[arg-type]
        market_currency=evidence.market_currency,  # type: ignore[arg-type]
        connection=evidence.connection,
        bars_available=evidence.bars_available,
        included=[timeframe.value for timeframe in evidence.included],  # type: ignore[misc]
        excluded=[_timeframe(item) for item in evidence.excluded],
        timeframes=[_timeframe(item) for item in evidence.timeframes],
        readings=[_readings(item) for item in evidence.readings],
        regime=evidence.regime,
        suitability=evidence.suitability,
        setup_quality=evidence.setup_quality,
        analysis_market_as_of=_moment(evidence.analysis_market_as_of),
    )


def _entry(entry: EntrySketch | None) -> EntrySketchResponse | None:
    if entry is None:
        return None
    return EntrySketchResponse(
        direction=entry.direction.value,  # type: ignore[arg-type]
        intended_entry=str(entry.intended_entry),
        stop=str(entry.stop),
        targets=[(str(price), ordinal) for price, ordinal in entry.targets],
        requested_quantity=entry.requested_quantity,
        approved_quantity=entry.approved_quantity,
    )


def _development(
    development: PriceDevelopment,
    *,
    recorded_at: datetime | None = None,
    boundary: datetime | None = None,
) -> PriceDevelopmentResponse:
    return PriceDevelopmentResponse(
        state=development.state.value,
        event=development.event.value,
        rules=development.rules,
        observed_from=_moment(development.observed_from),
        observed_to=_moment(development.observed_to),
        candles_observed=development.candles_observed,
        event_at=_moment(development.event_at),
        target_ordinal=development.target_ordinal,
        best_price=_amount(development.best_price),
        worst_price=_amount(development.worst_price),
        last_close=_amount(development.last_close),
        ambiguous=development.ambiguous,
        unresolved_reason=development.unresolved_reason,
        recorded_at=_moment(recorded_at),
        decision_boundary=_moment(boundary),
    )


def _entry_response(
    item: ShadowDecision, outcome: ShadowOutcomeRecord | None
) -> ShadowEntryResponse:
    return ShadowEntryResponse(
        kind=item.kind.value,
        sequence=item.sequence,
        decision_key=item.decision_key,
        market_boundary=_moment(item.market_boundary),
        recorded_at=item.recorded_at.isoformat(),
        outcome=None if item.outcome is None else item.outcome.value,
        operational=None if item.operational is None else item.operational.value,
        strategy_kind=item.strategy_kind,
        reason=item.reason,
        direction=None if item.direction is None else item.direction.value,  # type: ignore[arg-type]
        entry=_entry(item.entry),
        financial_state=item.financial_state.value,
        risk_outcome=item.risk_outcome,
        risk_reason=item.risk_reason,
        evidence=_evidence(item.evidence),
        input_fingerprint=item.input_fingerprint,
        development=(
            None
            if outcome is None
            else _development(
                outcome.development,
                recorded_at=outcome.recorded_at,
                boundary=outcome.decision_boundary,
            )
        ),
    )


def journal_page(
    run_id: str,
    entries: tuple[ShadowDecision, ...],
    total: int,
    developments: dict[str, ShadowOutcomeRecord],
    *,
    server_time: datetime,
) -> ShadowJournalPageResponse:
    items = [
        _entry_response(
            item,
            developments.get(item.decision_key) if item.kind is JournalEntryKind.DECISION else None,
        )
        for item in entries
    ]
    return ShadowJournalPageResponse(
        run_id=run_id,
        items=items,
        total=total,
        next_after=items[-1].sequence if items else None,
        server_time=server_time.isoformat(),
    )


def outcome_page(
    run_id: str,
    records: tuple[ShadowOutcomeRecord, ...],
    total: int,
    *,
    server_time: datetime,
) -> ShadowOutcomePageResponse:
    items = [
        ShadowOutcomeResponse(
            decision_key=record.decision_key,
            sequence=record.sequence,
            outcome_key=record.outcome_key,
            recorded_at=record.recorded_at.isoformat(),
            decision_boundary=record.decision_boundary.isoformat(),
            direction=record.direction.value,  # type: ignore[arg-type]
            development=_development(record.development),
        )
        for record in records
    ]
    return ShadowOutcomePageResponse(
        run_id=run_id,
        items=items,
        total=total,
        next_after=items[-1].sequence if items else None,
        server_time=server_time.isoformat(),
    )


def capability_response(
    capability: ShadowCapability, *, server_time: datetime
) -> ShadowCapabilityResponse:
    limits = capability.limits
    return ShadowCapabilityResponse(
        available=capability.available,
        reason=capability.reason,
        provenance=capability.provenance,  # type: ignore[arg-type]
        market_currency=capability.market_currency,  # type: ignore[arg-type]
        financial_metadata_available=capability.financial_metadata,
        strategies=[
            ShadowStrategyResponse(strategy_id=identifier, versions=list(versions))
            for identifier, versions in sorted(capability.strategies.items())
        ],
        limits=ShadowLimitsResponse(
            max_runs=limits.max_runs,
            max_observations=limits.max_observations,
            max_journal_page=limits.max_journal_page,
            max_outcome_page=limits.max_outcome_page,
            max_outcome_window=limits.max_outcome_window,
            max_open_watches=limits.max_open_watches,
        ),
        server_time=server_time.isoformat(),
    )

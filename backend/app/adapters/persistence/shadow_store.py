"""PostgreSQL shadow journal and outcome store (Phase 14 Parts 1 and 2A).

Append-only and idempotent. An entry is inserted with ``ON CONFLICT DO
NOTHING`` on ``(run_id, decision_key)``, so writing the same observation twice
- a retry, a reconnect, a consumer resumed after a restart - records it once
and reports that nothing new was written. The trigger in migration 0007
refuses UPDATE and DELETE outright, so there is no path that edits a decision
after it was published. Published developments work the same way, through
``uq_shadow_outcome_run_key`` and the trigger in migration 0008.

Decimals are stored as text and read back as ``Decimal``: a price that went
through a float on its way to disk would no longer be the price the rule saw.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.adapters.persistence.database import Database
from app.adapters.persistence.shadow_models import (
    ShadowJournalRow,
    ShadowOutcomeRow,
    ShadowRunAttemptRow,
    ShadowRunRow,
)
from app.application.shadow.ports import (
    AttemptKeyHeldError,
    ShadowStoreUnavailableError,
    StoredShadowRun,
)
from app.domain.common.enums import Direction, Timeframe
from app.domain.shadow.decision import (
    EntrySketch,
    FinancialState,
    JournalEntryKind,
    OperationalKind,
    ShadowDecision,
    ShadowEvidence,
    ShadowOutcome,
    TimeframeEvidence,
    TimeframeReadings,
)
from app.domain.shadow.outcome import (
    LevelEvent,
    OutcomeState,
    PriceDevelopment,
    ShadowOutcomeRecord,
)
from app.domain.shadow.run import EndReason, ShadowError, ShadowRunStatus

__all__ = ["SqlAlchemyShadowStore"]


@asynccontextmanager
async def _reachable() -> AsyncIterator[None]:
    try:
        yield
    except SQLAlchemyError as error:
        raise ShadowStoreUnavailableError("the shadow journal is unreachable") from error


class SqlAlchemyShadowStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_run(
        self, run: StoredShadowRun, *, attempt_key: str | None = None
    ) -> StoredShadowRun:
        async with _reachable(), self._database.session() as session:
            existing = await session.get(ShadowRunRow, run.run_id)
            if existing is not None:
                raise ShadowError(
                    "SHADOW_RUN_EXISTS",
                    "a shadow run with this id already exists; two observations are "
                    "never merged into one record",
                )
            session.add(
                ShadowRunRow(
                    id=run.run_id,
                    configuration=run.configuration,
                    source_id=run.source_id,
                    instrument_label=run.instrument_label,
                    provenance=run.provenance,
                    market_currency=run.market_currency,
                    strategy_id=run.strategy_id,
                    strategy_version=run.strategy_version,
                    strategy_parameters=dict(run.strategy_parameters),
                    driver_timeframe=run.driver.value,
                    timeframes=[tf.value for tf in run.timeframes],
                    required_timeframes=[tf.value for tf in run.required_timeframes],
                    risk=dict(run.risk),
                    account=dict(run.account),
                    status=run.status.value,
                    started_at=run.started_at,
                    observations=run.observations,
                    decisions=run.decisions,
                    entries=run.entries,
                )
            )
            if attempt_key is not None:
                # The run row must exist before the key can refer to it, and
                # both must commit or neither: a flush, then the claim, then
                # one commit. A lost claim rolls the run row back with it.
                await session.flush()
                won = (
                    await session.execute(
                        insert(ShadowRunAttemptRow)
                        .values(
                            attempt_key=attempt_key,
                            run_id=run.run_id,
                            configuration=run.configuration,
                        )
                        .on_conflict_do_nothing(index_elements=[ShadowRunAttemptRow.attempt_key])
                        .returning(ShadowRunAttemptRow.attempt_key)
                    )
                ).scalar_one_or_none()
                if won is None:
                    await session.rollback()
                    held = await session.get(ShadowRunAttemptRow, attempt_key)
                    if held is None:  # pragma: no cover - attempt rows are never deleted
                        raise ShadowStoreUnavailableError("the attempt key vanished")
                    raise AttemptKeyHeldError(held.run_id, held.configuration)
            await session.commit()
        return run

    async def append(self, run_id: str, entries: Sequence[ShadowDecision]) -> int:
        if not entries:
            return 0
        rows = [_row_of(run_id, entry) for entry in entries]
        statement = (
            insert(ShadowJournalRow)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_shadow_journal_run_decision")
            .returning(ShadowJournalRow.id)
        )
        async with _reachable(), self._database.session() as session:
            result = await session.execute(statement)
            written = len(result.scalars().all())
            await session.commit()
        return written

    async def finish_run(
        self,
        run_id: str,
        *,
        status: ShadowRunStatus,
        end_reason: EndReason,
        ended_at: datetime,
        observations: int,
        decisions: int,
        entries: int,
        first_boundary: datetime | None,
        last_boundary: datetime | None,
        failure_code: str | None = None,
    ) -> StoredShadowRun:
        async with _reachable(), self._database.session() as session:
            row = await session.get(ShadowRunRow, run_id)
            if row is None:
                raise ShadowError("SHADOW_RUN_NOT_FOUND", "no such shadow run")
            row.status = status.value
            row.end_reason = end_reason.value
            row.ended_at = ended_at
            row.observations = observations
            row.decisions = decisions
            row.entries = entries
            row.first_boundary = first_boundary
            row.last_boundary = last_boundary
            row.failure_code = failure_code
            await session.commit()
            return _to_run(row)

    async def get_run(self, run_id: str) -> StoredShadowRun | None:
        async with _reachable(), self._database.session() as session:
            row = await session.get(ShadowRunRow, run_id)
            return None if row is None else _to_run(row)

    async def list_runs(
        self, *, offset: int, limit: int
    ) -> tuple[tuple[StoredShadowRun, ...], int]:
        async with _reachable(), self._database.session() as session:
            total = await session.scalar(select(func.count()).select_from(ShadowRunRow))
            rows = (
                (
                    await session.execute(
                        select(ShadowRunRow)
                        .order_by(ShadowRunRow.started_at.desc(), ShadowRunRow.id)
                        .offset(offset)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return tuple(_to_run(row) for row in rows), int(total or 0)

    async def read_entries(
        self, run_id: str, *, after_sequence: int, limit: int
    ) -> tuple[tuple[ShadowDecision, ...], int]:
        async with _reachable(), self._database.session() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(ShadowJournalRow)
                .where(ShadowJournalRow.run_id == run_id)
            )
            rows = (
                (
                    await session.execute(
                        select(ShadowJournalRow)
                        .where(
                            ShadowJournalRow.run_id == run_id,
                            ShadowJournalRow.sequence > after_sequence,
                        )
                        .order_by(ShadowJournalRow.sequence, ShadowJournalRow.id)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return tuple(_to_entry(row) for row in rows), int(total or 0)

    async def last_sequence(self, run_id: str) -> int:
        async with _reachable(), self._database.session() as session:
            value = await session.scalar(
                select(func.max(ShadowJournalRow.sequence)).where(ShadowJournalRow.run_id == run_id)
            )
            return int(value or 0)

    # -- outcomes --------------------------------------------------------

    async def append_outcomes(self, run_id: str, records: Sequence[ShadowOutcomeRecord]) -> int:
        if not records:
            return 0
        statement = (
            insert(ShadowOutcomeRow)
            .values([_outcome_row(run_id, record) for record in records])
            .on_conflict_do_nothing(constraint="uq_shadow_outcome_run_key")
            .returning(ShadowOutcomeRow.id)
        )
        async with _reachable(), self._database.session() as session:
            result = await session.execute(statement)
            written = len(result.scalars().all())
            await session.commit()
        return written

    async def read_outcomes(
        self, run_id: str, *, after_sequence: int, limit: int
    ) -> tuple[tuple[ShadowOutcomeRecord, ...], int]:
        async with _reachable(), self._database.session() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(ShadowOutcomeRow)
                .where(ShadowOutcomeRow.run_id == run_id)
            )
            rows = (
                (
                    await session.execute(
                        select(ShadowOutcomeRow)
                        .where(
                            ShadowOutcomeRow.run_id == run_id,
                            ShadowOutcomeRow.sequence > after_sequence,
                        )
                        .order_by(ShadowOutcomeRow.sequence, ShadowOutcomeRow.id)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return tuple(_to_outcome(row) for row in rows), int(total or 0)

    async def latest_outcomes(
        self, run_id: str, decision_keys: Sequence[str]
    ) -> Mapping[str, ShadowOutcomeRecord]:
        """One page's worth of current answers, in a single query.

        Deliberately not a lookup per decision: a journal page asking the
        database once per row is the N+1 that makes a research view unusable.
        """
        if not decision_keys:
            return {}
        async with _reachable(), self._database.session() as session:
            rows = (
                (
                    await session.execute(
                        select(ShadowOutcomeRow)
                        .where(
                            ShadowOutcomeRow.run_id == run_id,
                            ShadowOutcomeRow.decision_key.in_(list(decision_keys)),
                        )
                        .order_by(ShadowOutcomeRow.sequence, ShadowOutcomeRow.id)
                    )
                )
                .scalars()
                .all()
            )
        # Ordered ascending, so the last one seen for a decision is the newest.
        # Every earlier record stays in the table; this is the current answer.
        return {row.decision_key: _to_outcome(row) for row in rows}

    async def last_outcome_sequence(self, run_id: str) -> int:
        async with _reachable(), self._database.session() as session:
            value = await session.scalar(
                select(func.max(ShadowOutcomeRow.sequence)).where(ShadowOutcomeRow.run_id == run_id)
            )
            return int(value or 0)

    async def find_attempt(self, attempt_key: str) -> tuple[str, str] | None:
        async with _reachable(), self._database.session() as session:
            held = await session.get(ShadowRunAttemptRow, attempt_key)
            return None if held is None else (held.run_id, held.configuration)

    # -- restart reconciliation -----------------------------------------

    async def observing_runs(self, *, limit: int) -> tuple[StoredShadowRun, ...]:
        async with _reachable(), self._database.session() as session:
            rows = (
                (
                    await session.execute(
                        select(ShadowRunRow)
                        .where(ShadowRunRow.status == ShadowRunStatus.OBSERVING.value)
                        .order_by(ShadowRunRow.started_at, ShadowRunRow.id)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return tuple(_to_run(row) for row in rows)

    async def interrupt(
        self, run_id: str, *, ended_at: datetime, end_reason: EndReason
    ) -> StoredShadowRun:
        decision = ShadowJournalRow.entry_kind == JournalEntryKind.DECISION.value
        async with _reachable(), self._database.session() as session:
            row = await session.get(ShadowRunRow, run_id, with_for_update=True)
            if row is None:
                raise ShadowError("SHADOW_RUN_NOT_FOUND", "no such shadow run")
            if row.status != ShadowRunStatus.OBSERVING.value:
                return _to_run(row)  # somebody already closed it; say so, change nothing
            of_run = ShadowJournalRow.run_id == run_id
            entries = await session.scalar(select(func.count()).where(of_run))
            observations = await session.scalar(select(func.count()).where(of_run, decision))
            decided = await session.scalar(
                select(func.count()).where(
                    of_run, decision, ShadowJournalRow.outcome != ShadowOutcome.UNAVAILABLE.value
                )
            )
            first, last = (
                await session.execute(
                    select(
                        func.min(ShadowJournalRow.market_boundary),
                        func.max(ShadowJournalRow.market_boundary),
                    ).where(of_run, decision)
                )
            ).one()
            row.status = ShadowRunStatus.ENDED.value
            row.end_reason = end_reason.value
            row.ended_at = ended_at
            row.entries = int(entries or 0)
            row.observations = int(observations or 0)
            row.decisions = int(decided or 0)
            row.first_boundary = first
            row.last_boundary = last
            await session.commit()
            return _to_run(row)


# ----------------------------------------------------------------------


def _row_of(run_id: str, entry: ShadowDecision) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "sequence": entry.sequence,
        "decision_key": entry.decision_key,
        "entry_kind": entry.kind.value,
        "outcome": None if entry.outcome is None else entry.outcome.value,
        "operational": None if entry.operational is None else entry.operational.value,
        "strategy_kind": entry.strategy_kind,
        "reason": entry.reason,
        "market_boundary": entry.market_boundary,
        "recorded_at": entry.recorded_at,
        "direction": None if entry.direction is None else entry.direction.value,
        "entry": _entry_json(entry.entry),
        "financial_state": entry.financial_state.value,
        "risk_outcome": entry.risk_outcome,
        "risk_reason": entry.risk_reason,
        "evidence": _evidence_json(entry.evidence),
        "input_fingerprint": entry.input_fingerprint,
        "supersedes": entry.supersedes,
        "fields": dict(entry.fields),
    }


def _entry_json(entry: EntrySketch | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    return {
        "direction": entry.direction.value,
        "intended_entry": str(entry.intended_entry),
        "stop": str(entry.stop),
        "targets": [[str(price), quantity] for price, quantity in entry.targets],
        "requested_quantity": entry.requested_quantity,
        "approved_quantity": entry.approved_quantity,
    }


def _evidence_json(evidence: ShadowEvidence | None) -> dict[str, Any] | None:
    if evidence is None:
        return None
    return {
        "provenance": evidence.provenance,
        "market_currency": evidence.market_currency,
        "connection": evidence.connection,
        "bars_available": evidence.bars_available,
        "included": [tf.value for tf in evidence.included],
        "excluded": [_timeframe_json(item) for item in evidence.excluded],
        "timeframes": [_timeframe_json(item) for item in evidence.timeframes],
        "readings": [
            {
                "timeframe": item.timeframe.value,
                "ema_fast": item.ema_fast,
                "ema_slow": item.ema_slow,
                "rsi": item.rsi,
                "atr": item.atr,
                "adx": item.adx,
            }
            for item in evidence.readings
        ],
        "regime": evidence.regime,
        "suitability": evidence.suitability,
        "setup_quality": evidence.setup_quality,
        "analysis_market_as_of": (
            None
            if evidence.analysis_market_as_of is None
            else evidence.analysis_market_as_of.isoformat()
        ),
    }


def _timeframe_json(item: TimeframeEvidence) -> dict[str, Any]:
    return {
        "timeframe": item.timeframe.value,
        "available": item.available,
        "freshness": item.freshness,
        "integrity": item.integrity,
        "confirmed_count": item.confirmed_count,
        "reasons": list(item.reasons),
        "last_coverage_end": (
            None if item.last_coverage_end is None else item.last_coverage_end.isoformat()
        ),
    }


def _to_run(row: ShadowRunRow) -> StoredShadowRun:
    return StoredShadowRun(
        run_id=row.id,
        configuration=row.configuration,
        source_id=row.source_id,
        instrument_label=row.instrument_label,
        provenance=row.provenance,
        market_currency=row.market_currency,
        strategy_id=row.strategy_id,
        strategy_version=row.strategy_version,
        strategy_parameters={str(k): str(v) for k, v in dict(row.strategy_parameters).items()},
        driver=Timeframe(row.driver_timeframe),
        timeframes=tuple(Timeframe(str(value)) for value in row.timeframes),
        required_timeframes=tuple(Timeframe(str(value)) for value in row.required_timeframes),
        risk={str(k): str(v) for k, v in dict(row.risk).items()},
        account={str(k): str(v) for k, v in dict(row.account).items()},
        status=ShadowRunStatus(row.status),
        started_at=row.started_at,
        ended_at=row.ended_at,
        end_reason=None if row.end_reason is None else EndReason(row.end_reason),
        observations=row.observations,
        decisions=row.decisions,
        entries=row.entries,
        first_boundary=row.first_boundary,
        last_boundary=row.last_boundary,
        failure_code=row.failure_code,
    )


def _to_entry(row: ShadowJournalRow) -> ShadowDecision:
    return ShadowDecision(
        kind=JournalEntryKind(row.entry_kind),
        sequence=row.sequence,
        decision_key=row.decision_key,
        market_boundary=row.market_boundary,
        recorded_at=row.recorded_at,
        outcome=None if row.outcome is None else ShadowOutcome(row.outcome),
        reason=row.reason,
        strategy_kind=row.strategy_kind,
        operational=None if row.operational is None else OperationalKind(row.operational),
        direction=None if row.direction is None else Direction(row.direction),
        entry=_entry_of(row.entry),
        financial_state=FinancialState(row.financial_state),
        risk_outcome=row.risk_outcome,
        risk_reason=row.risk_reason,
        evidence=_evidence_of(row.evidence),
        input_fingerprint=row.input_fingerprint,
        supersedes=row.supersedes,
        fields={str(k): str(v) for k, v in dict(row.fields).items()},
    )


def _entry_of(payload: Mapping[str, Any] | None) -> EntrySketch | None:
    if payload is None:
        return None
    return EntrySketch(
        direction=Direction(str(payload["direction"])),
        intended_entry=Decimal(str(payload["intended_entry"])),
        stop=Decimal(str(payload["stop"])),
        targets=tuple(
            (Decimal(str(price)), int(quantity)) for price, quantity in payload["targets"]
        ),
        requested_quantity=int(payload["requested_quantity"]),
        approved_quantity=(
            None if payload["approved_quantity"] is None else int(payload["approved_quantity"])
        ),
    )


def _evidence_of(payload: Mapping[str, Any] | None) -> ShadowEvidence | None:
    if payload is None:
        return None
    return ShadowEvidence(
        provenance=str(payload["provenance"]),
        market_currency=str(payload["market_currency"]),
        connection=str(payload["connection"]),
        bars_available=int(payload["bars_available"]),
        included=tuple(Timeframe(str(value)) for value in payload["included"]),
        excluded=tuple(_timeframe_of(item) for item in payload["excluded"]),
        timeframes=tuple(_timeframe_of(item) for item in payload["timeframes"]),
        readings=tuple(
            TimeframeReadings(
                timeframe=Timeframe(str(item["timeframe"])),
                ema_fast=item["ema_fast"],
                ema_slow=item["ema_slow"],
                rsi=item["rsi"],
                atr=item["atr"],
                adx=item["adx"],
            )
            for item in payload["readings"]
        ),
        regime=payload["regime"],
        suitability=payload["suitability"],
        setup_quality=payload["setup_quality"],
        analysis_market_as_of=(
            None
            if payload["analysis_market_as_of"] is None
            else datetime.fromisoformat(str(payload["analysis_market_as_of"]))
        ),
    )


def _timeframe_of(payload: Mapping[str, Any]) -> TimeframeEvidence:
    return TimeframeEvidence(
        timeframe=Timeframe(str(payload["timeframe"])),
        available=bool(payload["available"]),
        freshness=str(payload["freshness"]),
        integrity=str(payload["integrity"]),
        confirmed_count=int(payload["confirmed_count"]),
        reasons=tuple(str(value) for value in payload["reasons"]),
        last_coverage_end=(
            None
            if payload["last_coverage_end"] is None
            else datetime.fromisoformat(str(payload["last_coverage_end"]))
        ),
    )


def _outcome_row(run_id: str, record: ShadowOutcomeRecord) -> dict[str, Any]:
    development = record.development
    return {
        "run_id": run_id,
        "decision_key": record.decision_key,
        "sequence": record.sequence,
        "outcome_key": record.outcome_key,
        "recorded_at": record.recorded_at,
        "decision_boundary": record.decision_boundary,
        "direction": record.direction.value,
        "state": development.state.value,
        "event": development.event.value,
        "rules": development.rules,
        "observed_from": development.observed_from,
        "observed_to": development.observed_to,
        "candles_observed": development.candles_observed,
        "event_at": development.event_at,
        "target_ordinal": development.target_ordinal,
        "best_price": development.best_price,
        "worst_price": development.worst_price,
        "last_close": development.last_close,
        "ambiguous": development.ambiguous,
        "unresolved_reason": development.unresolved_reason,
    }


def _to_outcome(row: ShadowOutcomeRow) -> ShadowOutcomeRecord:
    return ShadowOutcomeRecord(
        run_id=row.run_id,
        decision_key=row.decision_key,
        sequence=row.sequence,
        outcome_key=row.outcome_key,
        recorded_at=row.recorded_at,
        decision_boundary=row.decision_boundary,
        direction=Direction(row.direction),
        development=PriceDevelopment(
            state=OutcomeState(row.state),
            event=LevelEvent(row.event),
            rules=row.rules,
            observed_from=row.observed_from,
            observed_to=row.observed_to,
            candles_observed=row.candles_observed,
            event_at=row.event_at,
            target_ordinal=row.target_ordinal,
            best_price=row.best_price,
            worst_price=row.worst_price,
            last_close=row.last_close,
            ambiguous=row.ambiguous,
            unresolved_reason=row.unresolved_reason,
        ),
    )

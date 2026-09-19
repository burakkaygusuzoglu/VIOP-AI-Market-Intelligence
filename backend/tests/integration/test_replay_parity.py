"""Replay adds no engine of its own (Phase 11).

Three parity proofs, one per engine replay drives. Each builds the same input
twice - once through the replay path, once through the ordinary Phase 8/9/10
path - and requires the answers to be identical.

The reconstructions here deliberately do **not** reuse replay's own helpers.
A parity test that serialised the prefix with the same function replay uses
would prove that one function is consistent with itself; truncating the
original fixture CSV by hand proves that replay analysed the same market.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.adapters.market_data.csv_provider import CsvCandleTextParser
from app.adapters.performance.paper_source import SqlPaperPerformanceSource
from app.adapters.persistence.database import Database
from app.adapters.persistence.journal_store import SqlAlchemyJournalStore
from app.adapters.products.futures import FuturesSnapshotCodec
from app.application.analysis.orchestrator import AnalysisOutcome, run_analysis
from app.application.analysis.request import AnalysisRequest, TimeframeDataset
from app.application.paper.service import CreatePaperPosition
from app.application.performance.ports import OutcomeFilters
from app.application.performance.service import PerformanceService, PerformanceView
from app.application.ports.paper import StoredPosition
from app.application.replay.service import ReplayClock
from app.domain.common.enums import Direction, Timeframe
from app.domain.paper import SimulationPolicy, TargetSpec
from app.domain.risk.sizing import AccountState, RiskMode, RiskPolicy
from tests.factories_replay import BASE, dataset
from tests.integration.paper_support import FixedClock
from tests.integration.paper_support import service as paper_service
from tests.integration.replay_support import WALL, create_command, replay_service

pytestmark = pytest.mark.integration

PARSER = CsvCandleTextParser()
ACCOUNT = AccountState(equity=Decimal("100000"))
RISK = RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("1000"))


def prefix_of(content: str, as_of: datetime, timeframe: Timeframe) -> str:
    """The rows of a fixture CSV whose coverage had ended by ``as_of``.

    Written out longhand, from the uploaded text, so it shares no code with the
    thing it is checking.
    """
    header, *rows = content.strip().splitlines()
    kept = []
    for row in rows:
        opened = datetime.fromisoformat(row.split(",")[0])
        if opened + timedelta(minutes=timeframe.minutes) <= as_of:
            kept.append(row)
    return "\n".join([header, *kept]) + "\n"


class TestAnalysisIsThePhase8Analysis:
    async def test_replay_analysis_equals_a_direct_analysis_of_the_same_prefix(
        self, database: Database
    ) -> None:
        content = dataset(288)
        service = replay_service(database)
        session, _ = await service.create(
            create_command(
                "parity-analysis-001", content=content, replay_start=BASE + timedelta(hours=8)
            )
        )
        stored, replayed = await service.analyse(session.session_id)
        as_of = stored.cursor.as_of

        direct = await run_analysis(
            AnalysisRequest(
                symbol=stored.plan.symbol,
                datasets=tuple(
                    TimeframeDataset(
                        timeframe=timeframe,
                        content=prefix_of(text, as_of, timeframe),
                        source_name=f"direct {timeframe.value}",
                    )
                    for timeframe, text in sorted(content.items(), key=lambda item: item[0].value)
                ),
                account=None,
                risk_policy=None,
                entry_price=None,
                stop_price=None,
            ),
            parser=PARSER,
            clock=ReplayClock(as_of),
            contracts=None,
        )

        assert _analysis_fingerprint(replayed) == _analysis_fingerprint(direct)

    async def test_the_two_paths_disagree_when_the_prefix_differs(self, database: Database) -> None:
        """The control. Without it, two identically broken paths would pass."""
        content = dataset(288)
        service = replay_service(database)
        session, _ = await service.create(
            create_command(
                "parity-analysis-002", content=content, replay_start=BASE + timedelta(hours=8)
            )
        )
        stored, replayed = await service.analyse(session.session_id)
        longer = stored.cursor.as_of + timedelta(hours=4)

        direct = await run_analysis(
            AnalysisRequest(
                symbol=stored.plan.symbol,
                datasets=tuple(
                    TimeframeDataset(
                        timeframe=timeframe,
                        content=prefix_of(text, longer, timeframe),
                        source_name=f"direct {timeframe.value}",
                    )
                    for timeframe, text in sorted(content.items(), key=lambda item: item[0].value)
                ),
                account=None,
                risk_policy=None,
                entry_price=None,
                stop_price=None,
            ),
            parser=PARSER,
            clock=ReplayClock(longer),
            contracts=None,
        )
        assert _analysis_fingerprint(replayed) != _analysis_fingerprint(direct)

    async def test_the_analysis_is_anchored_to_replay_time_not_the_wall_clock(
        self, database: Database
    ) -> None:
        """Freshness is judged against the replay moment.

        Run against the process clock, an eight-hour-old bar would look months
        stale and every timeframe would be excluded as incoherent.
        """
        service = replay_service(database)
        session, _ = await service.create(
            create_command("parity-analysis-003", replay_start=BASE + timedelta(hours=8))
        )
        stored, outcome = await service.analyse(session.session_id)
        assert stored.cursor.as_of < WALL
        assert any(item.usable for item in outcome.timeframes)
        assert all(item.temporal_exclusion is None for item in outcome.timeframes if item.usable)


class TestPaperTradingIsThePhase9Engine:
    async def test_a_replay_position_matches_a_direct_one_given_the_same_bars(
        self, database: Database
    ) -> None:
        """Same spec, same bars, same order - so the ledgers must agree."""
        content = dataset(288)
        service = replay_service(database)
        session, _ = await service.create(
            create_command(
                "parity-paper-00001", content=content, replay_start=BASE + timedelta(hours=8)
            )
        )
        stored = await service.get(session.session_id)
        decision = stored.session.cursor.as_of

        _s, replay_position = await service.open_paper_position(
            session.session_id,
            idempotency_key="parity-paper-pos-0001",
            direction=Direction.LONG,
            quantity=2,
            intended_entry=Decimal("100"),
            stop=Decimal("90"),
            targets=(TargetSpec(Decimal("130"), 2),),
            account=ACCOUNT,
            risk=RISK,
            policy=SimulationPolicy(),
        )
        after = await service.step(
            session.session_id, steps=12, command_key=None, expected_version=None
        )
        replay_final = (await service.positions(session.session_id))[0]

        # The replay fed each bar at the moment it closed; the direct path gets
        # them in one upload, so its clock must stand at the end of that window.
        direct_service = paper_service(database, clock=FixedClock(after.view.session.cursor.as_of))
        direct = await direct_service.create(
            CreatePaperPosition(
                idempotency_key="parity-paper-pos-0002",
                symbol=stored.session.plan.symbol,
                direction=Direction.LONG,
                quantity=2,
                intended_entry=Decimal("100"),
                stop=Decimal("90"),
                targets=(TargetSpec(Decimal("130"), 2),),
                timeframe=Timeframe.M5,
                decision_time=decision,
                account=ACCOUNT,
                risk=RISK,
                policy=SimulationPolicy(),
            )
        )
        bars = prefix_of(content[Timeframe.M5], after.view.session.cursor.as_of, Timeframe.M5)
        window = _rows_after(bars, decision)
        direct_final = (
            await direct_service.observe(direct.stored.position_id, window, "direct")
        ).stored

        assert _ledger(replay_final) == _ledger(direct_final)
        assert replay_final.projection.state == direct_final.projection.state
        assert replay_final.projection.realized_gross == direct_final.projection.realized_gross
        assert replay_final.projection.unrealized_gross == direct_final.projection.unrealized_gross
        assert replay_position.projection.state == "PENDING_ENTRY"

    async def test_an_advance_delivers_every_intermediate_bar(self, database: Database) -> None:
        """One advance of twelve must equal twelve advances of one."""
        content = dataset(288)
        service = replay_service(database)
        together, _ = await service.create(
            create_command(
                "parity-paper-00002", content=content, replay_start=BASE + timedelta(hours=8)
            )
        )
        separately, _ = await service.create(
            create_command(
                "parity-paper-00003", content=content, replay_start=BASE + timedelta(hours=8)
            )
        )
        for index, session_id in enumerate((together.session_id, separately.session_id)):
            await service.open_paper_position(
                session_id,
                idempotency_key=f"parity-paper-pos-100{index}",
                direction=Direction.LONG,
                quantity=2,
                intended_entry=Decimal("100"),
                stop=Decimal("90"),
                targets=(TargetSpec(Decimal("130"), 2),),
                account=ACCOUNT,
                risk=RISK,
                policy=SimulationPolicy(),
            )

        await service.step(together.session_id, steps=12, command_key=None, expected_version=None)
        for _ in range(12):
            await service.step(separately.session_id, command_key=None, expected_version=None)

        bulk = (await service.positions(together.session_id))[0]
        single = (await service.positions(separately.session_id))[0]
        assert _ledger(bulk) == _ledger(single)
        assert bulk.projection.realized_gross == single.projection.realized_gross
        assert bulk.projection.last_bar_time == single.projection.last_bar_time

    async def test_the_decision_time_is_replay_time_not_the_wall_clock(
        self, database: Database
    ) -> None:
        service = replay_service(database)
        session, _ = await service.create(
            create_command("parity-paper-00004", replay_start=BASE + timedelta(hours=8))
        )
        view = await service.get(session.session_id)
        _s, position = await service.open_paper_position(
            session.session_id,
            idempotency_key="parity-paper-pos-2001",
            direction=Direction.LONG,
            quantity=2,
            intended_entry=Decimal("100"),
            stop=Decimal("90"),
            targets=(TargetSpec(Decimal("130"), 2),),
            account=ACCOUNT,
            risk=RISK,
            policy=SimulationPolicy(),
        )
        created = next(event for event in position.events if event.type.value == "POSITION_CREATED")
        # The creation event is stamped with the decision time itself.
        assert created.market_time == view.session.cursor.as_of
        assert created.market_time < WALL

    async def test_causality_still_holds_inside_a_replay(self, database: Database) -> None:
        """Phase 9's rule, unchanged: no bar before the decision may fill it."""
        service = replay_service(database)
        session, _ = await service.create(
            create_command("parity-paper-00005", replay_start=BASE + timedelta(hours=8))
        )
        view = await service.get(session.session_id)
        decision = view.session.cursor.as_of
        await service.open_paper_position(
            session.session_id,
            idempotency_key="parity-paper-pos-3001",
            direction=Direction.LONG,
            quantity=2,
            intended_entry=Decimal("100"),
            stop=Decimal("90"),
            targets=(TargetSpec(Decimal("130"), 2),),
            account=ACCOUNT,
            risk=RISK,
            policy=SimulationPolicy(),
        )
        await service.step(session.session_id, steps=6, command_key=None, expected_version=None)
        position = (await service.positions(session.session_id))[0]
        applied = [event.market_time for event in position.events if event.market_time is not None]
        assert applied, "the advance should have delivered bars"
        assert all(moment >= decision for moment in applied)


class TestPerformanceIsThePhase10Engine:
    async def test_session_performance_equals_phase_10_over_the_same_positions(
        self, database: Database
    ) -> None:
        service = replay_service(database)
        session, _ = await service.create(
            create_command("parity-perf-000001", replay_start=BASE + timedelta(hours=8))
        )
        await service.open_paper_position(
            session.session_id,
            idempotency_key="parity-perf-pos-0001",
            direction=Direction.LONG,
            quantity=2,
            intended_entry=Decimal("100"),
            stop=Decimal("90"),
            targets=(TargetSpec(Decimal("130"), 2),),
            account=ACCOUNT,
            risk=RISK,
            policy=SimulationPolicy(),
        )
        await service.step(session.session_id, steps=30, command_key=None, expected_version=None)

        _stored, ids, replay_view = await service.performance(session.session_id)
        direct = PerformanceService(
            source=SqlPaperPerformanceSource(database, FuturesSnapshotCodec()),
            journal=SqlAlchemyJournalStore(database),
            clock=FixedClock(WALL),
        )
        direct_view = await direct.summary(OutcomeFilters(position_ids=ids))
        assert _metrics(replay_view) == _metrics(direct_view)

    async def test_one_session_cannot_see_another_session_s_trade(self, database: Database) -> None:
        """Membership is a stored link, not a guess from symbol or timestamp."""
        service = replay_service(database)
        mine, _ = await service.create(
            create_command("parity-perf-000002", replay_start=BASE + timedelta(hours=8))
        )
        theirs, _ = await service.create(
            create_command("parity-perf-000003", replay_start=BASE + timedelta(hours=8))
        )
        for index, session_id in enumerate((mine.session_id, theirs.session_id)):
            await service.open_paper_position(
                session_id,
                idempotency_key=f"parity-perf-pos-200{index}",
                direction=Direction.LONG,
                quantity=2,
                intended_entry=Decimal("100"),
                stop=Decimal("90"),
                targets=(TargetSpec(Decimal("130"), 2),),
                account=ACCOUNT,
                risk=RISK,
                policy=SimulationPolicy(),
            )
        await service.step(mine.session_id, steps=30, command_key=None, expected_version=None)

        _a, my_ids, my_view = await service.performance(mine.session_id)
        _b, their_ids, their_view = await service.performance(theirs.session_id)
        assert set(my_ids).isdisjoint(their_ids)
        assert my_view.summary.counts.total == 1
        assert their_view.summary.counts.total == 1

        everything = PerformanceService(
            source=SqlPaperPerformanceSource(database, FuturesSnapshotCodec()),
            journal=SqlAlchemyJournalStore(database),
            clock=FixedClock(WALL),
        )
        combined = await everything.summary(OutcomeFilters())
        assert combined.summary.counts.total == 2

    async def test_a_position_belongs_to_one_session(self, database: Database) -> None:
        service = replay_service(database)
        mine, _ = await service.create(
            create_command("parity-perf-000004", replay_start=BASE + timedelta(hours=8))
        )
        theirs, _ = await service.create(
            create_command("parity-perf-000005", replay_start=BASE + timedelta(hours=8))
        )
        _s, position = await service.open_paper_position(
            mine.session_id,
            idempotency_key="parity-perf-pos-3001",
            direction=Direction.LONG,
            quantity=2,
            intended_entry=Decimal("100"),
            stop=Decimal("90"),
            targets=(TargetSpec(Decimal("130"), 2),),
            account=ACCOUNT,
            risk=RISK,
            policy=SimulationPolicy(),
        )
        from app.application.replay.service import ReplayServiceError

        with pytest.raises(ReplayServiceError) as error:
            await service.close_position(theirs.session_id, position.position_id)
        assert error.value.code == "POSITION_NOT_IN_SESSION"


# ----------------------------------------------------------------------


def _rows_after(content: str, decision: datetime) -> str:
    header, *rows = content.strip().splitlines()
    kept = [row for row in rows if datetime.fromisoformat(row.split(",")[0]) >= decision]
    return "\n".join([header, *kept]) + "\n"


def _analysis_fingerprint(outcome: AnalysisOutcome) -> str:
    parts: list[str] = []
    for item in outcome.timeframes:
        parts.append(f"{item.timeframe.value}:{item.usable}:{item.temporal_exclusion}")
        if item.series is not None:
            parts.append(str(len(item.series.candles)))
        parts.append(repr(item.technicals))
        parts.append(repr(item.structure))
    analysis = outcome.analysis
    if analysis is not None:
        parts.append(repr(analysis.evidence))
        parts.append(repr(analysis.contract_evidence))
        parts.append(repr(analysis.contradictions))
        parts.append(repr(analysis.fused))
        parts.append(repr(analysis.scenarios))
    return "|".join(parts)


def _ledger(position: StoredPosition) -> list[tuple[str, str | None, tuple[tuple[str, str], ...]]]:
    return [
        (
            event.type.value,
            None if event.market_time is None else event.market_time.isoformat(),
            tuple(sorted(event.data.items())),
        )
        for event in position.events
    ]


def _metrics(view: PerformanceView) -> str:
    """Every number Phase 10 states, as one comparable string."""
    summary = view.summary
    return repr(
        tuple(
            repr(getattr(summary, name))
            for name in (
                "basis",
                "counts",
                "sample_size",
                "fill_count",
                "wins",
                "losses",
                "breakevens",
                "fee_coverage",
                "accounting",
                "realized_gross",
                "fees_known",
                "realized_net",
                "unrealized_gross_open",
                "win_rate",
                "average_win",
                "average_loss",
                "profit_factor",
                "expectancy",
                "max_drawdown_absolute",
                "drawdown_percentage",
                "realized_r_expectancy",
                "streaks",
                "timeline",
            )
        )
    )

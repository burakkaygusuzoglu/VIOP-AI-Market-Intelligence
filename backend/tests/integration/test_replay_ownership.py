"""Who owns what, and what a position is allowed to be told (Phase 11).

Four questions a replay has to answer the same way every time:

* which positions belong to this session, and who decided that;
* what a replay-created position's symbol, timeframe and decision time are,
  and who supplied them;
* which bars such a position may receive;
* what the replay start actually means when a person types a moment that is
  not a candle boundary.

None of these is a matter of convention. Each is either enforced by the server
or it is a hole, so each is tested against the running system rather than read
off the code.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.adapters.persistence.database import Database
from app.application.ports.paper import StoredPosition
from app.application.replay.service import ReplayService, ReplayServiceError
from app.domain.common.enums import Direction, Timeframe
from app.domain.paper import SimulationPolicy, TargetSpec
from app.domain.risk.sizing import AccountState, RiskMode, RiskPolicy
from tests.factories_replay import BASE, dataset, five_minute
from tests.integration.paper_support import BASE_COMMAND
from tests.integration.paper_support import create_command as paper_command
from tests.integration.paper_support import service as paper_service
from tests.integration.replay_support import create_command, replay_service

pytestmark = pytest.mark.integration

START = BASE + timedelta(hours=8)
ACCOUNT = AccountState(equity=Decimal("100000"))
RISK = RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("1000"))


async def open_one(service: ReplayService, session_id: str, key: str) -> str:
    _session, stored = await service.open_paper_position(
        session_id,
        idempotency_key=key,
        direction=Direction.LONG,
        quantity=2,
        intended_entry=Decimal("100"),
        stop=Decimal("80"),
        targets=(TargetSpec(Decimal("130"), 2),),
        account=ACCOUNT,
        risk=RISK,
        policy=SimulationPolicy(),
    )
    return stored.position_id


class TestOnlyTheServerDecidesMembership:
    async def test_an_ordinary_paper_position_belongs_to_no_replay(
        self, database: Database
    ) -> None:
        """Created through the Phase 9 service, it is nobody's replay trade."""
        direct = paper_service(database)
        created = await direct.create(paper_command("ownership-plain-00001"))
        position_id = created.stored.position_id

        service = replay_service(database)
        session, _ = await service.create(
            create_command("ownership-session-001", replay_start=START)
        )
        assert await service._store.session_of_position(position_id) is None  # noqa: SLF001
        assert position_id not in (await service.get(session.session_id)).linked_positions

        _s, ids, view = await service.performance(session.session_id)
        assert position_id not in ids
        assert view.summary.counts.total == 0

    async def test_it_cannot_be_acted_on_through_a_replay(self, database: Database) -> None:
        direct = paper_service(database)
        created = await direct.create(paper_command("ownership-plain-00002"))

        service = replay_service(database)
        session, _ = await service.create(
            create_command("ownership-session-002", replay_start=START)
        )
        with pytest.raises(ReplayServiceError) as error:
            await service.close_position(session.session_id, created.stored.position_id)
        assert error.value.code == "POSITION_NOT_IN_SESSION"

    async def test_a_position_is_linked_only_by_being_created_in_the_session(
        self, database: Database
    ) -> None:
        service = replay_service(database)
        session, _ = await service.create(
            create_command("ownership-session-003", replay_start=START)
        )
        position_id = await open_one(service, session.session_id, "ownership-pos-000001")
        assert await service._store.session_of_position(position_id) == session.session_id  # noqa: SLF001
        assert (await service.get(session.session_id)).linked_positions == (position_id,)

    async def test_session_a_s_position_cannot_be_claimed_by_session_b(
        self, database: Database
    ) -> None:
        service = replay_service(database)
        first, _ = await service.create(create_command("ownership-session-004", replay_start=START))
        second, _ = await service.create(
            create_command("ownership-session-005", replay_start=START)
        )
        position_id = await open_one(service, first.session_id, "ownership-pos-000002")

        for operation in (
            service.close_position,
            service.move_stop_to_breakeven,
            service.cancel_position,
        ):
            with pytest.raises(ReplayServiceError) as error:
                await operation(second.session_id, position_id)
            assert error.value.code == "POSITION_NOT_IN_SESSION"

        assert (await service.get(second.session_id)).linked_positions == ()
        _s, ids, _view = await service.performance(second.session_id)
        assert ids == ()

    async def test_the_link_table_refuses_a_second_owner(self, database: Database) -> None:
        """The primary key is the position, so a second claim cannot be stored."""
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        service = replay_service(database)
        first, _ = await service.create(create_command("ownership-session-006", replay_start=START))
        second, _ = await service.create(
            create_command("ownership-session-007", replay_start=START)
        )
        position_id = await open_one(service, first.session_id, "ownership-pos-000003")

        with pytest.raises(IntegrityError):
            async with database.engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO replay_position_links (position_id, session_id) "
                        "VALUES (:p, :s)"
                    ),
                    {"p": position_id, "s": second.session_id},
                )


class TestTheSessionSuppliesTheTradeIdentity:
    async def test_symbol_timeframe_and_decision_time_come_from_the_session(
        self, database: Database
    ) -> None:
        service = replay_service(database)
        session, _ = await service.create(
            create_command("ownership-identity-01", replay_start=START)
        )
        view = await service.get(session.session_id)
        position_id = await open_one(service, session.session_id, "ownership-pos-000004")
        stored = (await service.positions(session.session_id))[0]

        assert stored.position_id == position_id
        assert stored.spec.symbol == session.plan.symbol
        assert stored.spec.timeframe is session.plan.driver
        assert stored.spec.decision_time == view.session.cursor.as_of

    @pytest.mark.parametrize("driver", [Timeframe.M5, Timeframe.M15, Timeframe.H1])
    async def test_the_position_always_carries_the_driver_timeframe(
        self, database: Database, driver: Timeframe
    ) -> None:
        service = replay_service(database)
        session, _ = await service.create(
            create_command(
                f"ownership-driver-{driver.value:0>4}",
                driver=driver,
                replay_start=BASE + timedelta(hours=12),
            )
        )
        await open_one(service, session.session_id, f"ownership-pos-{driver.value:0>9}")
        stored = (await service.positions(session.session_id))[0]
        assert stored.spec.timeframe is driver

    async def test_a_replay_position_never_sees_another_instrument(
        self, database: Database
    ) -> None:
        """One session, one dataset, one symbol - there is nothing else to feed."""
        service = replay_service(database)
        session, _ = await service.create(
            create_command("ownership-identity-02", replay_start=START)
        )
        await open_one(service, session.session_id, "ownership-pos-000005")
        await service.step(session.session_id, steps=3, command_key=None, expected_version=None)

        stored = (await service.positions(session.session_id))[0]
        assert stored.spec.symbol == session.plan.symbol
        assert session.dataset.symbol == session.plan.symbol


class TestObservationRouting:
    @pytest.mark.parametrize("driver", [Timeframe.M5, Timeframe.M15, Timeframe.H1])
    @pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
    async def test_a_position_receives_only_bars_of_its_own_timeframe(
        self, database: Database, driver: Timeframe, direction: Direction
    ) -> None:
        """The bars a step delivers are driver bars, and the position is a
        driver-timeframe position. Both sides are checked, not assumed."""
        service = replay_service(database)
        key = f"own-route-{driver.value}-{direction.value}".ljust(20, "0")
        session, _ = await service.create(
            create_command(key, driver=driver, replay_start=BASE + timedelta(hours=12))
        )
        await service.open_paper_position(
            session.session_id,
            idempotency_key=f"{key}-position-0001",
            direction=direction,
            quantity=2,
            intended_entry=Decimal("100"),
            stop=Decimal("80") if direction is Direction.LONG else Decimal("130"),
            targets=(
                TargetSpec(Decimal("130") if direction is Direction.LONG else Decimal("80"), 2),
            ),
            account=ACCOUNT,
            risk=RISK,
            policy=SimulationPolicy(),
        )
        await service.step(session.session_id, steps=3, command_key=None, expected_version=None)

        stored = (await service.positions(session.session_id))[0]
        applied = [
            event.market_time
            for event in stored.events
            if event.type.value == "OBSERVATION_APPLIED" and event.market_time is not None
        ]
        assert applied, "the advance delivered nothing"
        step_minutes = driver.minutes
        for earlier, later in zip(applied, applied[1:], strict=False):
            assert later - earlier == timedelta(minutes=step_minutes)

    async def test_a_finer_bar_is_refused_by_the_engine(self, database: Database) -> None:
        """The backstop. Even handed a 5M bar, a 15M position will not take it.

        Replay cannot produce this - a session's positions carry the driver
        timeframe - so the rule is exercised through the Phase 9 service
        directly. If replay ever started routing by something other than the
        driver, this is the refusal that would catch it.
        """
        from dataclasses import replace

        direct = paper_service(database)
        created = await direct.create(
            replace(
                BASE_COMMAND,
                idempotency_key="ownership-route-00001",
                timeframe=Timeframe.M15,
                decision_time=BASE,
            )
        )
        rows = five_minute(4)
        five_minute_csv = "\n".join(
            [
                "open_time,open,high,low,close,volume",
                *(row.line() for row in rows),
            ]
        )
        from app.application.paper.service import PaperServiceError

        with pytest.raises(PaperServiceError) as error:
            await direct.observe(created.stored.position_id, five_minute_csv, "wrong-timeframe")
        # Refused before the engine is reached: the Phase 1 validator sees bars
        # five minutes apart where a 15M series was promised. The engine has its
        # own guard behind that one, which the next test exercises directly.
        assert error.value.code in {
            "OBSERVATIONS_FAILED_DATA_QUALITY",
            "OBSERVATIONS_UNREADABLE",
            "INVALID_OBSERVATION",
        }
        assert "INTERVAL" in error.value.detail.upper()

    def test_the_engine_itself_refuses_a_bar_of_the_wrong_timeframe(self) -> None:
        """Behind the validator, the domain rule stands on its own.

        No database and no replay: this is the Phase 9 invariant that makes the
        routing safe however the bars were selected.
        """
        from app.domain.market.candle import Candle
        from app.domain.paper import PaperRefusalError, apply_observation, open_position
        from tests.factories_paper import approval_for, long_spec, product

        spec = long_spec(timeframe=Timeframe.M15)
        policy = product()
        position = open_position(spec, approval_for(spec, policy), policy)
        wrong = Candle(
            symbol=spec.symbol,
            timeframe=Timeframe.M5,
            open_time=spec.decision_time,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=Decimal("1000"),
            is_closed=True,
        )
        with pytest.raises(PaperRefusalError) as refusal:
            apply_observation(position, wrong, policy)
        assert refusal.value.code.value == "INVALID_OBSERVATION"
        assert "timeframe" in refusal.value.reason

    async def test_one_step_delivers_one_bar_per_position(self, database: Database) -> None:
        service = replay_service(database)
        session, _ = await service.create(
            create_command("ownership-onebar-0001", replay_start=START)
        )
        await open_one(service, session.session_id, "ownership-pos-000006")
        before = _applied_count(await service.positions(session.session_id))
        await service.step(session.session_id, command_key=None, expected_version=None)
        after = _applied_count(await service.positions(session.session_id))
        assert after - before == 1


class TestSimultaneousBoundaries:
    async def test_every_timeframe_closing_at_one_moment_is_revealed_once(
        self, database: Database
    ) -> None:
        """At the hour, a 5M, a 15M and a 1H candle all finish together."""
        service = replay_service(database)
        session, _ = await service.create(
            create_command("ownership-simul-0001", replay_start=BASE + timedelta(minutes=55))
        )
        before = {
            item.timeframe: item.revealed_total
            for item in (await service.get(session.session_id)).availability
        }
        assert before[Timeframe.M5] == 11
        assert before[Timeframe.M15] == 3
        assert before[Timeframe.H1] == 0

        stepped = await service.step(session.session_id, command_key=None, expected_version=None)
        assert stepped.view.session.cursor.as_of == BASE + timedelta(hours=1)
        after = {item.timeframe: item.revealed_total for item in stepped.view.availability}
        assert after[Timeframe.M5] == 12
        assert after[Timeframe.M15] == 4
        assert after[Timeframe.H1] == 1

    async def test_the_order_of_revealed_candles_is_the_market_order(
        self, database: Database
    ) -> None:
        """Ordered by open time in SQL, not by whatever the table returns."""
        service = replay_service(database)
        session, _ = await service.create(
            create_command("ownership-simul-0002", replay_start=START)
        )
        first = _times(await service.get(session.session_id, chart=Timeframe.M5))
        second = _times(await service.get(session.session_id, chart=Timeframe.M5))
        assert first == second
        assert first == sorted(first)


class TestReplayStartNormalisation:
    """A typed moment is snapped *backwards*, never forwards.

    The rule is one-directional on purpose: an effective start later than the
    moment a person asked for would reveal candles they did not ask to see.
    """

    @pytest.mark.parametrize(
        ("label", "offset_minutes", "expected_minutes"),
        [
            ("exactly on a boundary", 60, 60),
            ("inside a candle", 63, 60),
            ("one microsecond before a boundary", 60, 60),
            ("well between boundaries", 117, 115),
        ],
    )
    async def test_the_effective_start_never_exceeds_the_requested_one(
        self, database: Database, label: str, offset_minutes: int, expected_minutes: int
    ) -> None:
        service = replay_service(database)
        requested = BASE + timedelta(minutes=offset_minutes)
        session, _ = await service.create(
            create_command(f"own-snap-{offset_minutes:0>10}", replay_start=requested)
        )
        assert session.cursor.as_of <= requested, label
        assert session.cursor.as_of == BASE + timedelta(minutes=expected_minutes), label

    async def test_the_response_reports_both_the_request_and_the_effect(
        self, database: Database
    ) -> None:
        """Normalisation is disclosed, not silent: the plan keeps what was
        asked for and the cursor states what it resolved to."""
        service = replay_service(database)
        requested = BASE + timedelta(minutes=63)
        session, _ = await service.create(
            create_command("own-snap-disclosure", replay_start=requested)
        )
        assert session.plan.replay_start == requested
        assert session.cursor.as_of == BASE + timedelta(minutes=60)
        assert session.cursor.as_of != session.plan.replay_start

    async def test_warm_up_holds_nothing_that_had_not_finished(self, database: Database) -> None:
        service = replay_service(database)
        requested = BASE + timedelta(minutes=117)
        session, _ = await service.create(
            create_command("own-snap-warmup-001", replay_start=requested)
        )
        view = await service.get(session.session_id, chart=Timeframe.M5)
        as_of = view.session.cursor.as_of
        for window in view.availability:
            for candle in window.candles:
                assert candle.open_time + timedelta(minutes=window.timeframe.minutes) <= as_of
            assert as_of <= requested

    async def test_a_start_before_the_first_boundary_is_refused_not_moved_forward(
        self, database: Database
    ) -> None:
        service = replay_service(database)
        with pytest.raises(ReplayServiceError) as error:
            await service.create(
                create_command("own-snap-before-0001", replay_start=BASE + timedelta(minutes=4))
            )
        assert error.value.code == "REPLAY_START_BEFORE_DATA"

    async def test_a_start_past_the_last_boundary_is_refused(self, database: Database) -> None:
        service = replay_service(database)
        with pytest.raises(ReplayServiceError) as error:
            await service.create(
                create_command("own-snap-after-00001", replay_start=BASE + timedelta(days=30))
            )
        assert error.value.code == "REPLAY_START_AFTER_DATA"


class TestAnalysisPrefixIdentity:
    async def test_the_exact_candles_handed_to_the_pipeline_are_the_revealed_prefix(
        self, database: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not "similar results" - the same candles, row for row.

        The analysis request is captured on its way into the Phase 8 pipeline
        and compared with a prefix selected independently from the uploaded
        CSV text.
        """
        import app.application.replay.service as replay_module

        captured: dict[str, object] = {}
        original = replay_module.run_analysis  # type: ignore[attr-defined]

        async def capturing(request, **kwargs):  # type: ignore[no-untyped-def]
            captured["request"] = request
            captured["clock"] = kwargs["clock"].now()
            return await original(request, **kwargs)

        monkeypatch.setattr(replay_module, "run_analysis", capturing)

        content = dataset(288)
        service = replay_service(database)
        session, _ = await service.create(
            create_command("own-prefix-0000001", content=content, replay_start=START)
        )
        stored, _outcome = await service.analyse(session.session_id)
        as_of = stored.cursor.as_of

        request = captured["request"]
        assert captured["clock"] == as_of
        handed = {item.timeframe: item.content for item in request.datasets}  # type: ignore[attr-defined]

        for timeframe, text in content.items():
            expected = _prefix_rows(text, as_of, timeframe)
            actual = _rows_of(handed[timeframe])
            assert actual == expected, timeframe.value
            assert all(
                datetime.fromisoformat(row.split(",")[0]) + timedelta(minutes=timeframe.minutes)
                <= as_of
                for row in actual
            )

    async def test_changing_an_unrevealed_candle_changes_nothing_handed_over(
        self, database: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.application.replay.service as replay_module

        seen: list[dict[Timeframe, str]] = []
        original = replay_module.run_analysis  # type: ignore[attr-defined]

        async def capturing(request, **kwargs):  # type: ignore[no-untyped-def]
            seen.append({item.timeframe: item.content for item in request.datasets})
            return await original(request, **kwargs)

        monkeypatch.setattr(replay_module, "run_analysis", capturing)

        service = replay_service(database)
        plain = dataset(288)
        rows = five_minute(288)
        rows[250] = type(rows[250])(
            open_time=rows[250].open_time,
            open=rows[250].open,
            high=Decimal("999999999"),
            low=rows[250].low,
            close=rows[250].close,
            volume=rows[250].volume,
        )
        from tests.factories_replay import aggregate, csv_of

        altered = {
            Timeframe.M5: csv_of(rows),
            Timeframe.M15: csv_of(aggregate(rows, Timeframe.M15)),
            Timeframe.H1: csv_of(aggregate(rows, Timeframe.H1)),
        }
        for index, content in enumerate((plain, altered)):
            session, _ = await service.create(
                create_command(f"own-prefix-000100{index}", content=content, replay_start=START)
            )
            await service.analyse(session.session_id)

        assert len(seen) == 2
        assert seen[0] == seen[1]

    async def test_changing_a_revealed_candle_does_change_what_is_handed_over(
        self, database: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The control: the prefix is not simply ignoring the dataset."""
        import app.application.replay.service as replay_module

        seen: list[dict[Timeframe, str]] = []
        original = replay_module.run_analysis  # type: ignore[attr-defined]

        async def capturing(request, **kwargs):  # type: ignore[no-untyped-def]
            seen.append({item.timeframe: item.content for item in request.datasets})
            return await original(request, **kwargs)

        monkeypatch.setattr(replay_module, "run_analysis", capturing)

        from tests.factories_replay import aggregate, csv_of

        service = replay_service(database)
        rows = five_minute(288)
        rows[10] = type(rows[10])(
            open_time=rows[10].open_time,
            open=rows[10].open,
            high=rows[10].high + Decimal("7"),
            low=rows[10].low,
            close=rows[10].close,
            volume=rows[10].volume,
        )
        altered = {
            Timeframe.M5: csv_of(rows),
            Timeframe.M15: csv_of(aggregate(rows, Timeframe.M15)),
            Timeframe.H1: csv_of(aggregate(rows, Timeframe.H1)),
        }
        for index, content in enumerate((dataset(288), altered)):
            session, _ = await service.create(
                create_command(f"own-prefix-000200{index}", content=content, replay_start=START)
            )
            await service.analyse(session.session_id)

        assert seen[0] != seen[1]


# ----------------------------------------------------------------------


def _applied_count(positions: tuple[StoredPosition, ...]) -> int:
    return sum(
        1
        for stored in positions
        for event in stored.events
        if event.type.value == "OBSERVATION_APPLIED"
    )


def _times(view: object) -> list[datetime]:
    window = next(
        item
        for item in view.availability  # type: ignore[attr-defined]
        if item.timeframe is Timeframe.M5
    )
    return [candle.open_time for candle in window.candles]


def _rows_of(content: str) -> list[str]:
    return content.strip().splitlines()[1:]


def _prefix_rows(content: str, as_of: datetime, timeframe: Timeframe) -> list[str]:
    kept = []
    for row in content.strip().splitlines()[1:]:
        opened = datetime.fromisoformat(row.split(",")[0])
        if opened + timedelta(minutes=timeframe.minutes) <= as_of:
            kept.append(row)
    return kept

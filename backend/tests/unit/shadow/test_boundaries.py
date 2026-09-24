"""What Shadow Mode refuses, and what it can never reach (Phase 14 Part 1).

Cases J-T of the plan. The financial half is exercised with a **test-only**
metadata provider, because production composes none - the test that proves
that absence is the one immediately below it.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.contract_metadata.manual_provider import ManualContractMetadataProvider
from app.adapters.products.futures import FuturesProductResolver
from app.domain.risk.sizing import AccountState, RiskMode, RiskPolicy
from app.domain.shadow.decision import FinancialState, OperationalKind, ShadowOutcome
from app.domain.shadow.run import EndReason, ShadowError, ShadowLimits
from tests.factories_paper import paper_contract
from tests.unit.shadow.support import (
    CONNECTED,
    END,
    RUN_ID,
    MemoryJournal,
    ScriptedStrategy,
    bar,
    decisions_of,
    observe,
    request_for,
    runner_for,
)

pytestmark = pytest.mark.unit

APP = Path(__file__).resolve().parents[3] / "app"
BIG_ACCOUNT = AccountState(equity=Decimal("500000"), used_margin=Decimal("0"))
TINY_ACCOUNT = AccountState(equity=Decimal("100"), used_margin=Decimal("0"))
RISK = RiskPolicy(mode=RiskMode.PERCENTAGE, risk_ratio=Decimal("0.01"))


def resolver() -> FuturesProductResolver:
    """Test-only verified metadata. Production composes none."""
    return FuturesProductResolver(ManualContractMetadataProvider([paper_contract()]))


class TestJUnknownRules:
    @pytest.mark.parametrize(
        ("identifier", "version", "code"),
        [
            ("test-shadow", "9.9.9", "STRATEGY_VERSION_UNSUPPORTED"),
            ("not-a-strategy", "1.0.0", "STRATEGY_UNSUPPORTED"),
        ],
    )
    async def test_rules_this_build_does_not_have_are_refused(
        self, identifier: str, version: str, code: str
    ) -> None:
        runner = runner_for(MemoryJournal())
        strategy = ScriptedStrategy(identifier=identifier, version=version)

        with pytest.raises(ShadowError) as caught:
            await runner.open(request_for(strategy), run_id=RUN_ID)

        assert caught.value.code == code
        assert len(runner) == 0

    async def test_the_shipped_registry_is_the_one_the_application_uses(self) -> None:
        """A widened table reaching a deployment would make the check decorative."""
        service = (APP / "application" / "shadow" / "service.py").read_text(encoding="utf-8")

        assert "SUPPORTED_STRATEGIES if supported is None else supported" in service
        for banned in ("importlib", "__import__", "eval(", "exec(", "getattr(strategy"):
            assert banned not in service


class TestKMetadataAndLRisk:
    async def test_without_a_resolver_an_entry_is_recorded_as_metadata_unavailable(
        self,
    ) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal)  # no resolver, as production composes none

        await observe(
            runner,
            request_for(ScriptedStrategy(["ENTRY_INTENT"]), account=BIG_ACCOUNT, risk=RISK),
            [CONNECTED, bar(0), END],
        )

        decision = decisions_of(journal)[0]
        assert decision.outcome is ShadowOutcome.ENTRY_INTENT
        assert decision.financial_state is FinancialState.METADATA_UNAVAILABLE
        assert decision.entry is not None
        assert decision.entry.approved_quantity is None
        assert decision.risk_reason == "no verified contract metadata provider is configured"

    async def test_verified_metadata_and_a_sized_account_can_be_approved(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal, resolver=resolver())

        await observe(
            runner,
            request_for(ScriptedStrategy(["ENTRY_INTENT"]), account=BIG_ACCOUNT, risk=RISK),
            [CONNECTED, bar(0), END],
        )

        decision = decisions_of(journal)[0]
        assert decision.financial_state is FinancialState.APPROVED
        assert decision.entry is not None
        assert decision.entry.approved_quantity == 1
        assert decision.outcome is ShadowOutcome.ENTRY_INTENT

    async def test_a_risk_refusal_is_the_outcome_and_approves_nothing(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal, resolver=resolver())

        await observe(
            runner,
            request_for(ScriptedStrategy(["ENTRY_INTENT"]), account=TINY_ACCOUNT, risk=RISK),
            [CONNECTED, bar(0), END],
        )

        decision = decisions_of(journal)[0]
        assert decision.outcome is ShadowOutcome.REFUSED  # never ENTRY_INTENT
        assert decision.financial_state is FinancialState.REFUSED
        assert decision.entry is not None
        assert decision.entry.approved_quantity is None
        assert decision.risk_outcome == "NOT_PERMITTED"
        assert decision.risk_reason

    async def test_an_observation_only_run_asks_nothing_financial(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal, resolver=resolver())

        await observe(
            runner, request_for(ScriptedStrategy(["ENTRY_INTENT"])), [CONNECTED, bar(0), END]
        )

        decision = decisions_of(journal)[0]
        assert decision.financial_state is FinancialState.NOT_CONFIGURED
        assert decision.risk_outcome is None

    async def test_an_incomplete_risk_configuration_is_refused_up_front(self) -> None:
        runner = runner_for(MemoryJournal())

        with pytest.raises(ShadowError) as caught:
            await runner.open(request_for(ScriptedStrategy(), account=BIG_ACCOUNT), run_id=RUN_ID)

        assert caught.value.code == "INCOMPLETE_RISK_CONFIGURATION"

    async def test_the_instrument_label_alone_establishes_no_contract(self) -> None:
        """The stream's symbol is a label. Without a provider it stays one."""
        journal = MemoryJournal()
        runner = runner_for(journal)

        run, _ = await observe(
            runner,
            request_for(ScriptedStrategy(["ENTRY_INTENT"]), account=BIG_ACCOUNT, risk=RISK),
            [CONNECTED, bar(0), END],
        )

        assert run.instrument_label == "TEST_FIXTURE_FUT"
        decision = decisions_of(journal)[0]
        assert decision.financial_state is FinancialState.METADATA_UNAVAILABLE
        for banned in ("multiplier", "tick", "margin", "expiry"):
            assert banned not in str(decision).lower()


class TestMNONothingIsCreated:
    @pytest.mark.parametrize(
        "banned",
        [
            "PaperPosition",
            "open_position(",
            "PaperStore",
            "BacktestRunner",
            "RunRequest(",
            "place_order",
            "submit_order",
            "BrokerPort",
            "OrderExecutionPort",
            "Midas",
        ],
    )
    def test_no_shadow_module_can_create_or_execute_anything(self, banned: str) -> None:
        sources = [
            *(APP / "domain" / "shadow").rglob("*.py"),
            *(APP / "application" / "shadow").rglob("*.py"),
        ]
        offenders = [path.name for path in sources if banned in path.read_text(encoding="utf-8")]
        assert offenders == []

    async def test_an_approved_entry_still_creates_no_position_or_run(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal, resolver=resolver())

        await observe(
            runner,
            request_for(ScriptedStrategy(["ENTRY_INTENT"]), account=BIG_ACCOUNT, risk=RISK),
            [CONNECTED, bar(0), END],
        )

        decision = decisions_of(journal)[0]
        assert decision.financial_state is FinancialState.APPROVED
        # The journal is the only thing written, and it holds no position id.
        assert set(journal.runs) == {RUN_ID}
        assert not hasattr(decision, "position_id")
        assert "position" not in str(decision.fields).lower()


class TestRRunIsolation:
    async def test_two_runs_over_one_stream_keep_separate_journals(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal)
        first = "SR-" + "a" * 24
        second = "SR-" + "b" * 24

        await observe(
            runner,
            request_for(ScriptedStrategy(["ENTRY_INTENT"])),
            [CONNECTED, bar(0), bar(1), END],
            run_id=first,
        )
        await observe(
            runner,
            request_for(ScriptedStrategy(["NO_SIGNAL", "WAIT"])),
            [CONNECTED, bar(0), bar(1), END],
            run_id=second,
        )

        assert [entry.outcome for entry in decisions_of(journal, first)] == [
            ShadowOutcome.ENTRY_INTENT,
            ShadowOutcome.NO_SIGNAL,
        ]
        assert [entry.outcome for entry in decisions_of(journal, second)] == [
            ShadowOutcome.NO_SIGNAL,
            ShadowOutcome.WAIT,
        ]
        assert journal.runs[first].configuration != journal.runs[second].configuration
        keys = journal.keys
        assert not (keys[first] & keys[second])  # no key is shared between runs

    async def test_a_second_run_with_the_same_id_is_refused(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal)
        await observe(runner, request_for(ScriptedStrategy()), [CONNECTED, bar(0), END])

        with pytest.raises(AssertionError):
            await observe(runner, request_for(ScriptedStrategy()), [CONNECTED, bar(0), END])


class TestSBounds:
    async def test_the_run_stops_at_its_observation_bound_and_says_so(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal, limits=ShadowLimits(max_observations=2))

        run, _ = await observe(
            runner,
            request_for(ScriptedStrategy()),
            [CONNECTED, bar(0), bar(1), bar(2), bar(3), END],
        )

        assert run.observations == 2
        assert run.end_reason is EndReason.OBSERVATION_LIMIT
        assert len(decisions_of(journal)) == 2
        assert any(
            entry.operational is OperationalKind.RUN_ENDED and "bound" in entry.reason
            for entry in journal.entries[RUN_ID]
        )

    async def test_a_consumer_that_falls_behind_ends_the_run_rather_than_skip(self) -> None:
        """Part 2B: the pending-record bound had no test. A backlog past it ends
        the run with a stated reason - never a journal with a silent hole, and
        never a run described as complete."""
        from app.api.schemas.shadow_projection import run_response

        journal = MemoryJournal()
        runner = runner_for(journal, limits=ShadowLimits(max_pending_records=3))

        run, _ = await observe(
            runner,
            request_for(ScriptedStrategy()),
            [CONNECTED, *(bar(i) for i in range(20)), END],
        )

        assert run.end_reason is EndReason.PROVIDER_ERROR
        assert run.failure_code == "PENDING_OVERFLOW"
        assert run_response(run).completeness == "INTERRUPTED"
        assert any(
            entry.operational is OperationalKind.EVALUATION_FAILED and "behind" in entry.reason
            for entry in journal.entries[RUN_ID]
        )
        assert len(decisions_of(journal)) < 20  # it stopped; it did not pretend to keep up

    async def test_the_number_of_concurrent_runs_is_bounded(self) -> None:
        runner = runner_for(MemoryJournal(), limits=ShadowLimits(max_runs=1))
        await runner.open(request_for(ScriptedStrategy()), run_id="SR-" + "c" * 24)

        with pytest.raises(ShadowError) as caught:
            await runner.open(request_for(ScriptedStrategy()), run_id="SR-" + "d" * 24)

        assert caught.value.code == "SHADOW_CAPACITY"

    async def test_a_reason_is_stored_bounded(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal, limits=ShadowLimits(max_reason_length=20))

        await observe(runner, request_for(ScriptedStrategy()), [CONNECTED, bar(0), END])

        assert all(len(entry.reason) <= 20 for entry in journal.entries[RUN_ID])


class TestTFailureSemantics:
    async def test_a_failing_strategy_ends_the_run_and_leaks_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)
        journal = MemoryJournal()
        runner = runner_for(journal)

        run, _ = await observe(
            runner,
            request_for(ScriptedStrategy(["NO_SIGNAL"], fail_at=1)),
            [CONNECTED, bar(0), bar(1), bar(2), END],
        )

        assert run.end_reason is EndReason.EVALUATION_ERROR
        assert run.failure_code == "STRATEGY_FAILED"
        assert len(decisions_of(journal)) == 1  # the boundary that failed has no decision
        assert any(
            entry.operational is OperationalKind.EVALUATION_FAILED
            for entry in journal.entries[RUN_ID]
        )
        assert "sk-shadow-do-not-leak" not in caplog.text
        assert "secrets" not in caplog.text
        assert all(record.exc_info is None for record in caplog.records)

    async def test_a_partial_run_is_never_described_as_complete(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal)

        run, _ = await observe(
            runner,
            request_for(ScriptedStrategy(["NO_SIGNAL"], fail_at=1)),
            [CONNECTED, bar(0), bar(1), bar(2), END],
        )

        assert run.status.value == "ENDED"
        assert run.end_reason is not EndReason.STREAM_ENDED
        assert "COMPLETE" not in run.status.value
        assert run.observations >= run.decisions

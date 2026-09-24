"""Following a decision through the runner (Phase 14 Part 2A).

The domain engine is tested in ``test_outcome.py``. What is pinned here is the
wiring: which decisions get followed, when a development is published, that it
is published once, and that a correction invalidates the follow-up instead of
quietly letting it resolve on contested evidence.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.adapters.contract_metadata.manual_provider import ManualContractMetadataProvider
from app.adapters.live.mock_stream import candle_event
from app.adapters.products.futures import FuturesProductResolver
from app.domain.risk.sizing import AccountState, RiskMode, RiskPolicy
from app.domain.shadow.decision import FinancialState, ShadowOutcome
from app.domain.shadow.outcome import LevelEvent, OutcomeState, ShadowOutcomeRecord
from app.domain.shadow.run import ShadowLimits
from tests.factories_paper import paper_contract
from tests.unit.live.support import SYMBOL, at
from tests.unit.shadow.support import (
    CONNECTED,
    END,
    M5,
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


def quiet(index: int) -> object:
    """A candle that reaches neither the stop nor the target.

    The scripted policy proposes stop = close - 1 and target = close + 2 from a
    close of 100.5, so this range stays clear of both.
    """
    return candle_event(
        SYMBOL, M5, at(5 * index), "100.5", "100.6", "100.4", "100.5", sequence=index
    )


def spike(index: int) -> object:
    """A candle whose high reaches the scripted target of 102.5."""
    return candle_event(SYMBOL, M5, at(5 * index), "100.5", "103", "100.4", "100.5", sequence=index)


def verified_metadata() -> FuturesProductResolver:
    """Test-only verified metadata. Production composes none."""
    return FuturesProductResolver(ManualContractMetadataProvider([paper_contract()]))


def outcomes_of(journal: MemoryJournal, run_id: str = RUN_ID) -> list[ShadowOutcomeRecord]:
    return journal.outcomes[run_id]


class TestWhichDecisionsAreFollowed:
    async def test_an_entry_intent_is_followed(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal)

        await observe(
            runner,
            request_for(ScriptedStrategy(["ENTRY_INTENT"])),
            [CONNECTED, quiet(0), quiet(1), spike(2), END],
        )

        published = outcomes_of(journal)
        assert len(published) == 1
        assert published[0].development.event is LevelEvent.TARGET_LEVEL_TOUCHED
        assert published[0].development.state is OutcomeState.OBSERVED
        # Tied to the decision it followed, and to market time.
        assert published[0].decision_key == decisions_of(journal)[0].decision_key
        assert published[0].decision_boundary == at(5)
        assert published[0].development.event_at == at(15)

    async def test_a_quiet_decision_is_never_followed(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal)

        await observe(
            runner,
            request_for(ScriptedStrategy(["NO_SIGNAL", "WAIT"])),
            [CONNECTED, quiet(0), quiet(1), quiet(2), END],
        )

        assert outcomes_of(journal) == []  # no population of imaginary trades

    async def test_a_risk_refused_signal_is_not_followed(self) -> None:
        """Nothing was proposed to the market, so a development afterwards
        would read as a position that existed and then moved."""
        journal = MemoryJournal()
        runner = runner_for(journal, resolver=verified_metadata())

        await observe(
            runner,
            request_for(
                ScriptedStrategy(["ENTRY_INTENT"]),
                account=AccountState(equity=Decimal("100"), used_margin=Decimal("0")),
                risk=RiskPolicy(mode=RiskMode.PERCENTAGE, risk_ratio=Decimal("0.01")),
            ),
            [CONNECTED, quiet(0), quiet(1), spike(2), END],
        )

        assert decisions_of(journal)[0].outcome is ShadowOutcome.REFUSED
        assert outcomes_of(journal) == []

    async def test_an_intent_without_verified_metadata_is_still_followed(self) -> None:
        """Price development is a fact about price. The decision carries
        METADATA_UNAVAILABLE, so nothing here implies a trade was possible."""
        journal = MemoryJournal()
        runner = runner_for(journal)

        await observe(
            runner,
            request_for(ScriptedStrategy(["ENTRY_INTENT"])),
            [CONNECTED, quiet(0), quiet(1), spike(2), END],
        )

        decision = decisions_of(journal)[0]
        assert decision.outcome is ShadowOutcome.ENTRY_INTENT
        assert decision.financial_state is FinancialState.NOT_CONFIGURED
        assert len(outcomes_of(journal)) == 1


class TestNoLookaheadThroughTheRunner:
    async def test_a_development_cites_only_candles_after_the_decision(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal)

        await observe(
            runner,
            request_for(ScriptedStrategy(["ENTRY_INTENT"])),
            [CONNECTED, spike(0), quiet(1), quiet(2), quiet(3), END],
        )

        published = outcomes_of(journal)[0]
        # The spike at index 0 is the decision's own candle. Its high of 103
        # would have touched the target, and it must not count.
        assert published.development.observed_from == at(10)
        assert published.development.event is not LevelEvent.TARGET_LEVEL_TOUCHED

    async def test_a_decision_is_not_resolved_by_its_own_boundary(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal)

        await observe(
            runner, request_for(ScriptedStrategy(["ENTRY_INTENT"])), [CONNECTED, spike(0), END]
        )

        published = outcomes_of(journal)[0]
        assert published.development.state is OutcomeState.UNAVAILABLE
        assert published.development.candles_observed == 0


class TestPublishingIsIdempotentAndBounded:
    async def test_a_development_is_published_once(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal)

        await observe(
            runner,
            request_for(ScriptedStrategy(["ENTRY_INTENT"])),
            [CONNECTED, quiet(0), spike(1), quiet(2), quiet(3), END],
        )

        assert len(outcomes_of(journal)) == 1
        assert len({record.outcome_key for record in outcomes_of(journal)}) == 1

    async def test_an_unclosed_window_ends_unavailable_not_none_reached(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal, limits=ShadowLimits(max_outcome_window=50))

        await observe(
            runner,
            request_for(ScriptedStrategy(["ENTRY_INTENT"])),
            [CONNECTED, quiet(0), quiet(1), quiet(2), END],
        )

        development = outcomes_of(journal)[0].development
        assert development.state is OutcomeState.UNAVAILABLE
        assert "never closed" in (development.unresolved_reason or "")

    async def test_a_closed_window_with_nothing_reached_is_observed(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal, limits=ShadowLimits(max_outcome_window=2))

        await observe(
            runner,
            request_for(ScriptedStrategy(["ENTRY_INTENT"])),
            [CONNECTED, quiet(0), quiet(1), quiet(2), quiet(3), END],
        )

        development = outcomes_of(journal)[0].development
        assert development.state is OutcomeState.OBSERVED
        assert development.event is LevelEvent.NONE_REACHED
        assert development.candles_observed == 2

    async def test_the_number_of_followed_decisions_is_bounded(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal, limits=ShadowLimits(max_open_watches=1))

        await observe(
            runner,
            request_for(ScriptedStrategy(["ENTRY_INTENT", "ENTRY_INTENT", "ENTRY_INTENT"])),
            [CONNECTED, quiet(0), quiet(1), quiet(2), quiet(3), END],
        )

        # Four boundaries, three of them entry intents, and one follow-up
        # slot: the rest are recorded with no development rather than silently
        # queued forever.
        assert len(decisions_of(journal)) == 4
        assert len(outcomes_of(journal)) == 1


class TestACorrectionInvalidatesTheFollowUp:
    async def test_a_contested_decision_stops_being_followed(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal)
        conflicting = replace(bar(1), close=bar(1).low)

        await observe(
            runner,
            request_for(ScriptedStrategy(["NO_SIGNAL", "ENTRY_INTENT"])),
            [CONNECTED, quiet(0), bar(1), conflicting, END],
        )

        published = outcomes_of(journal)
        assert len(published) == 1
        assert published[0].development.state is OutcomeState.INVALIDATED
        reason = published[0].development.unresolved_reason or ""
        # It names the dependency and the exact candle, not a vague "nearby".
        assert "the decision read the corrected 5M candle" in reason
        assert at(5).isoformat() in reason
        # The decision itself is untouched: it still says what it said.
        assert decisions_of(journal)[1].outcome is ShadowOutcome.ENTRY_INTENT

    async def test_an_invalidated_development_claims_nothing_about_price(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal)
        conflicting = replace(bar(1), close=bar(1).low)

        await observe(
            runner,
            request_for(ScriptedStrategy(["NO_SIGNAL", "ENTRY_INTENT"])),
            [CONNECTED, quiet(0), bar(1), conflicting, END],
        )

        development = outcomes_of(journal)[0].development
        assert development.event is LevelEvent.NOT_OBSERVED
        assert development.best_price is None
        assert development.worst_price is None

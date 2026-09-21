"""What the runner refuses to be talked into (Phase 12 Part 1).

Part 2 owns the HTTP surface, so nothing here is about request parsing. These
are the refusals that must hold at the *application* boundary, which is where
they will still have to hold once a route exists in front of them - a guard
that only lives in a schema is a guard that disappears the first time another
caller is added.

The recurring shape: a refusal names what was wrong and does not approximate.
An unknown strategy version is not an old version to be re-interpreted; an
oversized run is not the first N candles; an unverified tick is not a tick.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from app.adapters.persistence.database import Database
from app.application.backtest.service import BacktestErrorKind, BacktestServiceError
from app.domain.backtest.registry import SUPPORTED_STRATEGIES, require_supported_rules
from app.domain.backtest.run import BacktestError, RunInterval
from app.domain.backtest.strategies.ema_crossover import EmaCrossoverSettings
from app.domain.common.enums import Timeframe
from app.domain.paper.rules import (
    FeeMode,
    FeePolicy,
    SimulationPolicy,
    SimulationPolicyError,
    SlippageMode,
    SlippagePolicy,
)
from app.domain.risk.sizing import AccountState, RiskMode, RiskPolicy
from tests.factories_replay import BASE
from tests.integration.backtest_support import (
    ScriptedStrategy,
    flat_rows,
    runner,
    scripted_request,
    seed_dataset,
)

pytestmark = pytest.mark.integration

M5_ONLY = (Timeframe.M5,)


class TestUnknownStrategyRulesAreRefused:
    """Section 24: an unknown version fails explicitly, never silently maps."""

    async def test_an_unknown_strategy_identifier_is_refused(self, database: Database) -> None:
        dataset = await seed_dataset(database, flat_rows(4), timeframes=M5_ONLY)
        service = runner(database)
        strategy = ScriptedStrategy(label="strategy-nobody-wrote")

        with pytest.raises(BacktestServiceError) as raised:
            await service.run(
                scripted_request(dataset, strategy, key="adversarial-strat-1", last=3)
            )

        assert raised.value.code == "STRATEGY_UNSUPPORTED"
        assert "not a strategy this runner implements" in raised.value.detail

    async def test_an_unknown_version_is_not_re_interpreted(self) -> None:
        with pytest.raises(BacktestError) as raised:
            require_supported_rules("ema-crossover-atr", "0.9.0")

        assert raised.value.code == "STRATEGY_VERSION_UNSUPPORTED"
        assert "is not re-interpreted as" in raised.value.reason

    async def test_the_shipped_registry_holds_exactly_the_reference_strategy(self) -> None:
        assert dict(SUPPORTED_STRATEGIES) == {"ema-crossover-atr": frozenset({"1.0.0"})}

    async def test_the_registry_cannot_be_widened_at_runtime(self) -> None:
        """A mutable table would make the whole check advisory."""
        with pytest.raises(TypeError):
            SUPPORTED_STRATEGIES["anything"] = frozenset({"1.0.0"})  # type: ignore[index]

    async def test_the_rules_are_checked_before_any_market_is_read(
        self, database: Database
    ) -> None:
        """A refusal about the rules must not depend on the dataset existing."""
        service = runner(database)
        request = scripted_request(
            await seed_dataset(database, flat_rows(4), timeframes=M5_ONLY),
            ScriptedStrategy(label="strategy-nobody-wrote"),
            key="adversarial-order-1",
            last=3,
        )
        broken = replace(request, dataset_id="RD-does-not-exist")

        with pytest.raises(BacktestServiceError) as raised:
            await service.run(broken)

        assert raised.value.code == "STRATEGY_UNSUPPORTED"


class TestInvalidConfigurationCannotBeConstructed:
    """Section 35: the invalid states are unrepresentable, not merely rejected."""

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("fast_period", 0),
            ("slow_period", 3),
            ("quantity", 0),
            ("atr_stop_multiple", Decimal("0")),
            ("adx_minimum", -1.0),
        ],
    )
    def test_invalid_strategy_parameters_are_refused(self, field: str, value: object) -> None:
        with pytest.raises(Exception) as raised:
            EmaCrossoverSettings(**{field: value})  # type: ignore[arg-type]

        assert raised.type is not AssertionError

    def test_an_unsupported_simulation_rules_version_cannot_exist(self) -> None:
        with pytest.raises(SimulationPolicyError, match="not supported"):
            SimulationPolicy(rules_version="simulation/v99")

    def test_a_slippage_policy_must_agree_with_its_own_mode(self) -> None:
        with pytest.raises(SimulationPolicyError):
            SlippagePolicy(mode=SlippageMode.ZERO, points=Decimal("0.25"))
        with pytest.raises(SimulationPolicyError):
            SlippagePolicy(mode=SlippageMode.FIXED_POINTS, points=None)

    def test_a_fee_policy_must_agree_with_its_own_mode(self) -> None:
        with pytest.raises(SimulationPolicyError):
            FeePolicy(mode=FeeMode.NOT_MODELLED, per_unit=Decimal("1"))
        with pytest.raises(SimulationPolicyError):
            FeePolicy(mode=FeeMode.USER_DEFINED_PER_UNIT, per_unit=None)

    @pytest.mark.parametrize("equity", ["0", "-1"])
    async def test_a_ruined_account_is_representable_and_trades_nothing(
        self, database: Database, equity: str
    ) -> None:
        """Phase 3 deliberately allows zero and negative equity.

        A blown account is a real state, and refusing to *model* it would
        force a caller to round it away. The refusal belongs where the money
        is decided: sizing permits nothing, so the run opens nothing.
        """
        account = AccountState(equity=Decimal(equity))
        dataset = await seed_dataset(database, flat_rows(4), timeframes=M5_ONLY)

        stored = await runner(database).run(
            scripted_request(
                dataset,
                ScriptedStrategy(),
                key=f"adversarial-ruined-{equity.replace('-', 'n')}",
                last=3,
                account=account,
            )
        )

        assert stored.result is not None
        assert stored.result.positions == ()

    @pytest.mark.parametrize("equity", ["NaN", "Infinity"])
    def test_a_non_finite_equity_is_refused_outright(self, equity: str) -> None:
        with pytest.raises(Exception) as raised:
            AccountState(equity=Decimal(equity))

        assert raised.type is not AssertionError

    def test_an_invalid_risk_configuration_is_refused(self) -> None:
        with pytest.raises(Exception) as raised:
            RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("-100"))

        assert raised.type is not AssertionError

    def test_a_backwards_interval_is_refused(self) -> None:
        with pytest.raises(BacktestError) as raised:
            RunInterval(start=BASE + timedelta(hours=2), end=BASE)

        assert raised.value.code == "INTERVAL_INVALID"

    @pytest.mark.parametrize("key", ["", "short", "x" * 200, "has spaces in it", "sql';--"])
    async def test_an_invalid_attempt_key_is_refused(self, database: Database, key: str) -> None:
        dataset = await seed_dataset(database, flat_rows(4), timeframes=M5_ONLY)
        service = runner(database)

        with pytest.raises(BacktestServiceError) as raised:
            await service.run(scripted_request(dataset, ScriptedStrategy(), key=key, last=3))

        assert raised.value.code == "ATTEMPT_KEY_INVALID"
        assert raised.value.kind is BacktestErrorKind.INVALID


class TestForgedMarketFactsAreNotAccepted:
    async def test_a_dataset_that_does_not_exist_is_not_invented(self, database: Database) -> None:
        dataset = await seed_dataset(database, flat_rows(4), timeframes=M5_ONLY)
        request = scripted_request(dataset, ScriptedStrategy(), key="adversarial-ghost-1", last=3)
        forged = replace(request, dataset_id="RD-" + "0" * 32)

        with pytest.raises(BacktestServiceError) as raised:
            await runner(database).run(forged)

        assert raised.value.code == "DATASET_NOT_FOUND"
        assert raised.value.kind is BacktestErrorKind.NOT_FOUND

    async def test_a_driver_timeframe_the_dataset_lacks_is_refused(
        self, database: Database
    ) -> None:
        """Not silently aggregated into existence."""
        dataset = await seed_dataset(database, flat_rows(4), timeframes=M5_ONLY)
        request = scripted_request(dataset, ScriptedStrategy(), key="adversarial-tf-001", last=3)
        forged = replace(request, driver=Timeframe.H1)

        with pytest.raises(BacktestServiceError) as raised:
            await runner(database).run(forged)

        assert raised.value.code in ("DRIVER_TIMEFRAME_MISSING", "INTERVAL_EMPTY")

    async def test_a_run_cannot_be_requested_without_verified_product_metadata(
        self, database: Database
    ) -> None:
        """What a default production deployment composes: no provider at all."""
        dataset = await seed_dataset(database, flat_rows(4), timeframes=M5_ONLY)
        service = runner(database, with_products=False)

        with pytest.raises(BacktestServiceError) as raised:
            await service.run(
                scripted_request(dataset, ScriptedStrategy(), key="adversarial-meta-01", last=3)
            )

        assert raised.value.code == "PRODUCT_METADATA_UNAVAILABLE"
        assert raised.value.kind is BacktestErrorKind.REFUSED

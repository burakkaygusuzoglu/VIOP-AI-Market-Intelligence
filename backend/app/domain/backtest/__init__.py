"""Deterministic backtesting: a strategy vocabulary and one bounded run.

The domain owns three things and no arithmetic: what a strategy may see and
say, what a run is and what it may cost, and what makes two runs the same. The
money, the indicators and the metrics belong to the engines the runner drives.
"""

from app.domain.backtest.fingerprint import (
    canonical_decimal,
    canonical_time,
    configuration_fingerprint,
    result_digest,
    run_id,
)
from app.domain.backtest.policy import (
    DecisionKind,
    EntryIntent,
    Readings,
    StrategyContext,
    StrategyDecision,
    StrategyInputError,
    StrategyPolicy,
    TargetLevel,
)
from app.domain.backtest.run import (
    RUNNER_RULES_VERSION,
    BacktestError,
    DecisionOutcome,
    DecisionRecord,
    RunBounds,
    RunInterval,
    RunPlan,
    RunStatus,
)

__all__ = [
    "RUNNER_RULES_VERSION",
    "BacktestError",
    "DecisionKind",
    "DecisionOutcome",
    "DecisionRecord",
    "EntryIntent",
    "Readings",
    "RunBounds",
    "RunInterval",
    "RunPlan",
    "RunStatus",
    "StrategyContext",
    "StrategyDecision",
    "StrategyInputError",
    "StrategyPolicy",
    "TargetLevel",
    "canonical_decimal",
    "canonical_time",
    "configuration_fingerprint",
    "result_digest",
    "run_id",
]

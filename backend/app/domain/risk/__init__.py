"""Risk engine (master spec sections 42, 44, 46, 47).

Deterministic domain arithmetic in ``Decimal`` throughout. **No LLM computes,
adjusts or overrides anything here** - master spec section 1 makes the risk
engine independent of Claude, which may explain a risk figure but is never its
source.

Two rules shape the whole package:

*Floor, never ceil.* 0.75 contracts is zero contracts. Section 42 makes the
"one contract already exceeds the configured risk" case mandatory, and rounding
up would breach the user's stated limit on the very first trade.

*Missing information stays missing.* An unverified margin does not become a
satisfied constraint, an absent commission does not become zero cost, and a
sizing answer that depends on either says so rather than reporting the half it
could compute.
"""

from app.domain.risk.margin import (
    MarginAssessment,
    RiskWarning,
    RiskWarningCode,
    assess_margin,
    effective_leverage,
    notional_exposure,
)
from app.domain.risk.pnl import (
    CostCompleteness,
    PnLInputError,
    PnLResult,
    TradeCosts,
    calculate_contract_pnl,
    calculate_pnl,
    gross_pnl,
)
from app.domain.risk.reward import RiskReward, risk_reward, risk_reward_targets
from app.domain.risk.sizing import (
    AccountState,
    MarginFeasibility,
    PositionSizing,
    RiskInputError,
    RiskMode,
    RiskPolicy,
    SizingOutcome,
    TickFeasibility,
    size_position,
    stop_distance,
)
from app.domain.risk.whatif import (
    WhatIfResult,
    WhatIfScenario,
    simulate,
    simulate_contract,
)

__all__ = [
    "AccountState",
    "CostCompleteness",
    "MarginAssessment",
    "MarginFeasibility",
    "PnLInputError",
    "PnLResult",
    "PositionSizing",
    "RiskInputError",
    "RiskMode",
    "RiskPolicy",
    "RiskReward",
    "RiskWarning",
    "RiskWarningCode",
    "SizingOutcome",
    "TickFeasibility",
    "TradeCosts",
    "WhatIfResult",
    "WhatIfScenario",
    "assess_margin",
    "calculate_contract_pnl",
    "calculate_pnl",
    "effective_leverage",
    "gross_pnl",
    "notional_exposure",
    "risk_reward",
    "risk_reward_targets",
    "simulate",
    "simulate_contract",
    "size_position",
    "stop_distance",
]

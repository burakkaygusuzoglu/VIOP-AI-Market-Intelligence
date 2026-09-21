"""Which strategy rules this runner is allowed to evaluate (Phase 12).

A stored run records the identifier and version of the rules that produced it.
That record is only worth anything if the pair is checked: otherwise a
re-evaluation of the same stored configuration would silently use whatever
implementation happens to exist now, and two runs carrying the same version
string could mean two different sets of rules.

So the allowed pairs are written down here, as **data**. Nothing in this module
imports by name, constructs a class from a string, or evaluates anything a
caller supplied - the registry answers one question, "may these rules run?",
and the caller still hands in the policy object itself.

Adding a strategy means adding a line here. Changing a strategy's decisions
means bumping its version and adding that version here, which is a visible edit
in a review rather than a silent change in behaviour.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.domain.backtest.run import BacktestError
from app.domain.backtest.strategies.ema_crossover import IDENTIFIER, VERSION

__all__ = ["SUPPORTED_STRATEGIES", "require_supported_rules"]

SUPPORTED_STRATEGIES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        IDENTIFIER: frozenset({VERSION}),
    }
)
"""Identifier to the rule versions this runner implements.

Read-only at runtime. A mutable mapping here would let a caller widen what the
runner accepts, which is the whole thing this table exists to prevent.
"""


def require_supported_rules(
    identifier: str, version: str, *, supported: Mapping[str, frozenset[str]] | None = None
) -> None:
    """Raise unless these exact rules are ones this runner implements.

    Refused rather than mapped: an unknown version is not an old version to be
    approximated with the current code, it is rules this build does not have.

    ``supported`` exists so a test can exercise the runner with a scripted
    policy, the same way it injects its own resource bounds. It defaults to the
    shipped table, and a test asserts that nothing in ``app/`` ever passes a
    different one - a widened table reaching a deployment would make this
    check decorative.
    """
    table = SUPPORTED_STRATEGIES if supported is None else supported
    known = table.get(identifier)
    if known is None:
        raise BacktestError(
            "STRATEGY_UNSUPPORTED",
            (
                f"{identifier!r} is not a strategy this runner implements; "
                f"available: {sorted(table)}"
            ),
        )
    if version not in known:
        raise BacktestError(
            "STRATEGY_VERSION_UNSUPPORTED",
            (
                f"{identifier!r} version {version!r} is not implemented by this build, "
                f"and is not re-interpreted as {sorted(known)}; a run recorded under "
                "other rules must be re-evaluated by a build that has them"
            ),
        )

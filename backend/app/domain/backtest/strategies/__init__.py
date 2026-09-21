"""Deterministic strategies. One per module, each with its own version.

A strategy is registered by identifier and version in
``app.domain.backtest.registry``, and never constructed from a name a client
supplied: no dynamic import, no ``eval``, and no user-provided Python. An
unknown identifier or version is refused there - ``require_supported_rules`` -
rather than mapped to whatever implementation happens to exist now.
"""

from app.domain.backtest.strategies.ema_crossover import (
    EmaCrossoverSettings,
    EmaCrossoverStrategy,
)

__all__ = ["EmaCrossoverSettings", "EmaCrossoverStrategy"]

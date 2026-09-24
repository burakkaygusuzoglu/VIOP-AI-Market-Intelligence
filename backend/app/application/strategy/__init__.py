"""Shared strategy-context construction (Phase 14).

Turning confirmed candles into the scalars a :class:`StrategyPolicy` may see
is the same work wherever it happens: a backtest walks a stored interval, a
shadow run watches a stream arrive. Phase 12 wrote it once inside its runner;
Phase 14 needs the identical construction, so it lives here and both call it.

Nothing here computes an indicator. It calls the Phase 1 engine and reads the
values out at one index.
"""

from app.application.strategy.context import confirmed_higher, readings_at, readings_series

__all__ = ["confirmed_higher", "readings_at", "readings_series"]

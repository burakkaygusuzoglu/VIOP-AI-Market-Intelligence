"""Deterministic interactive replay: availability rules and a forward-only cursor."""

from app.domain.replay.availability import (
    coverage_end,
    duration_of,
    is_available,
    next_boundary,
    revealed,
    revealed_count,
)
from app.domain.replay.session import (
    MAX_ADVANCE_STEPS,
    ReplayCursor,
    ReplayError,
    ReplayPlan,
    ReplayStatus,
    advance,
    start_cursor,
    step,
)

__all__ = [
    "MAX_ADVANCE_STEPS",
    "ReplayCursor",
    "ReplayError",
    "ReplayPlan",
    "ReplayStatus",
    "advance",
    "coverage_end",
    "duration_of",
    "is_available",
    "next_boundary",
    "revealed",
    "revealed_count",
    "start_cursor",
    "step",
]

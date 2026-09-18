"""Paper trading - a deterministic simulation, never a broker (Phase 9).

Nothing in this package places, routes, transmits or acknowledges an order. It
turns a user-authored plan and a sequence of closed historical bars into
simulated fills, and every number it reports is labelled as simulated.

Generic by construction: product economics arrive through
``app.domain.instrument.ProductPolicy`` and money through
``app.domain.risk.pnl.pnl_for_product``. An import contract forbids this package
from importing ``app.domain.futures``.
"""

from app.domain.paper.engine import (
    BreakevenInput,
    CancelInput,
    CloseInput,
    PaperInput,
    PaperRefusalError,
    RefusalCode,
    apply_input,
    apply_observation,
    cancel,
    inputs_from_events,
    move_stop_to_breakeven,
    open_position,
    rebuild,
    replay,
    request_close,
    unrealized_gross,
)
from app.domain.paper.model import (
    INPUT_EVENT_TYPES,
    MAX_NOTE_LENGTH,
    MAX_TARGETS,
    PaperEvent,
    PaperEventType,
    PaperInputError,
    PaperPosition,
    PositionOrigin,
    PositionSpec,
    PositionState,
    RiskApproval,
    TargetSpec,
    TargetState,
)
from app.domain.paper.rules import (
    ENTRY_MODEL,
    MANUAL_EXIT_MODEL,
    SIMULATION_RULES_VERSION,
    STOP_FILL_MODEL,
    SUPPORTED_RULES_VERSIONS,
    TARGET_FILL_MODEL,
    FeeMode,
    FeePolicy,
    SameBarPolicy,
    SimulationPolicy,
    SimulationPolicyError,
    SlippageMode,
    SlippagePolicy,
)

__all__ = [
    "ENTRY_MODEL",
    "INPUT_EVENT_TYPES",
    "MANUAL_EXIT_MODEL",
    "MAX_NOTE_LENGTH",
    "MAX_TARGETS",
    "SIMULATION_RULES_VERSION",
    "STOP_FILL_MODEL",
    "SUPPORTED_RULES_VERSIONS",
    "TARGET_FILL_MODEL",
    "BreakevenInput",
    "CancelInput",
    "CloseInput",
    "FeeMode",
    "FeePolicy",
    "PaperEvent",
    "PaperEventType",
    "PaperInput",
    "PaperInputError",
    "PaperPosition",
    "PaperRefusalError",
    "PositionOrigin",
    "PositionSpec",
    "PositionState",
    "RefusalCode",
    "RiskApproval",
    "SameBarPolicy",
    "SimulationPolicy",
    "SimulationPolicyError",
    "SlippageMode",
    "SlippagePolicy",
    "TargetSpec",
    "TargetState",
    "apply_input",
    "apply_observation",
    "cancel",
    "inputs_from_events",
    "move_stop_to_breakeven",
    "open_position",
    "rebuild",
    "replay",
    "request_close",
    "unrealized_gross",
]

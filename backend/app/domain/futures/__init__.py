"""Futures contract domain (master spec sections 31, 32, 33).

Every mutable exchange fact - multiplier, tick size, tick value, margin,
expiry, settlement, session - arrives wrapped in a ``VerifiedValue`` and is
never hard-coded here. Master spec section 118: a wrong multiplier produces a
wrong number that looks entirely normal, so provenance travels with the value.
"""

from app.domain.futures.basis import (
    BasisContext,
    BasisPolicy,
    BasisResult,
    calculate_basis,
    calculate_contract_basis,
)
from app.domain.futures.contract import (
    ContractExpiry,
    ContractState,
    ContractValidationError,
    FuturesContract,
    FuturesQuote,
    QuoteMismatchError,
    SettlementType,
    UnsupportedValuationModelError,
    ValuationModel,
    require_matching_quote,
    require_same_instrument,
)
from app.domain.futures.open_interest import (
    OpenInterestContext,
    OpenInterestPolicy,
    OpenInterestReading,
    read_contract_open_interest,
    read_open_interest,
)
from app.domain.futures.validation import (
    ContractIssue,
    ContractIssueCode,
    ContractStateResult,
    TickGridResult,
    blocking_issues,
    check_tick_grid,
    check_tick_value,
    contract_issues,
    contract_state,
    implied_tick_value,
    provenance_issues,
    require_calculable,
    ticks_between,
)

__all__ = [
    "BasisContext",
    "BasisPolicy",
    "BasisResult",
    "ContractExpiry",
    "ContractIssue",
    "ContractIssueCode",
    "ContractState",
    "ContractStateResult",
    "ContractValidationError",
    "FuturesContract",
    "FuturesQuote",
    "QuoteMismatchError",
    "OpenInterestContext",
    "OpenInterestPolicy",
    "OpenInterestReading",
    "SettlementType",
    "TickGridResult",
    "UnsupportedValuationModelError",
    "ValuationModel",
    "blocking_issues",
    "calculate_basis",
    "calculate_contract_basis",
    "check_tick_grid",
    "check_tick_value",
    "contract_issues",
    "contract_state",
    "implied_tick_value",
    "provenance_issues",
    "read_contract_open_interest",
    "require_calculable",
    "require_matching_quote",
    "require_same_instrument",
    "read_open_interest",
    "ticks_between",
]

"""Manual contract metadata provider (master spec section 31).

> If no live provider exists initially: implement ManualContractMetadataProvider.

That is where this project is. There is no verified live source of VIOP
contract specifications wired in, and section 118 forbids inventing one, so the
only honest provider is one that serves exactly what a human explicitly gave
it, with the provenance they attached.

**The ship it must not do.** It never upgrades a verification status. A
contract registered with an ``UNVERIFIED`` multiplier is served with an
``UNVERIFIED`` multiplier, however inconvenient that is downstream. The check
is not a comment - ``register`` compares the stored provenance with what was
supplied and the adapter has no code path that can raise a status.

**What it must not become.** It does not scrape Midas, call a broker interface,
touch an unofficial endpoint or ask for credentials - master spec section 120
and the project's standing rules forbid all four. It makes no network call of
any kind.

**No production VIOP contract ships in this repository.** The adapter is empty
until a caller registers something. Test fixtures live in the test suite and
are marked ``TEST_FIXTURE``.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.common.verification import VerificationStatus
from app.domain.futures.contract import FuturesContract
from app.domain.futures.validation import ContractIssue, contract_issues


class DuplicateContractError(ValueError):
    """Raised when a symbol is registered twice.

    Silently replacing the first would make which specification is in force
    depend on registration order - and a multiplier that changes because a
    module was imported in a different sequence is the kind of bug that only
    shows up in production.
    """


class ManualContractMetadataProvider:
    """Serves contract specifications supplied explicitly by a human.

    Implements ``ContractMetadataProvider``. Registration validates the
    contract's internal invariants (through ``FuturesContract`` itself) and
    surfaces its metadata issues, so a caller learns immediately that a margin
    is unverified rather than discovering it when a position size comes back
    undetermined.
    """

    def __init__(self, contracts: Sequence[FuturesContract] = ()) -> None:
        self._contracts: dict[str, FuturesContract] = {}
        for contract in contracts:
            self.register(contract)

    def register(self, contract: FuturesContract) -> tuple[ContractIssue, ...]:
        """Add a contract and report what is wrong or unverified about it.

        Returns the metadata issues rather than raising on them: an unverified
        margin is a real limitation but not a reason to refuse the contract,
        because everything that does not need margin still works. Structural
        impossibilities - a negative multiplier, a zero tick size - were
        already rejected by ``FuturesContract`` construction.
        """
        symbol = contract.symbol.strip()
        if symbol in self._contracts:
            raise DuplicateContractError(
                f"{symbol} is already registered; unregister it first if the "
                "specification genuinely changed"
            )
        self._contracts[symbol] = contract
        return contract_issues(contract)

    def unregister(self, symbol: str) -> None:
        self._contracts.pop(symbol.strip(), None)

    async def get_contract(self, symbol: str) -> FuturesContract | None:
        """Return exactly what was registered, provenance untouched."""
        return self._contracts.get(symbol.strip())

    async def list_symbols(self) -> Sequence[str]:
        return tuple(sorted(self._contracts))

    def verification_summary(self) -> dict[str, dict[str, VerificationStatus]]:
        """Per-symbol provenance of each supplied fact.

        Exists so an operator can see at a glance which specifications are
        genuinely verified and which are development defaults, without opening
        each contract.
        """
        summary: dict[str, dict[str, VerificationStatus]] = {}
        for symbol, contract in self._contracts.items():
            facts: dict[str, VerificationStatus] = {
                "multiplier": contract.multiplier.status,
                "tick_size": contract.tick_size.status,
            }
            if contract.tick_value is not None:
                facts["tick_value"] = contract.tick_value.status
            if contract.initial_margin is not None:
                facts["initial_margin"] = contract.initial_margin.status
            if contract.maintenance_margin is not None:
                facts["maintenance_margin"] = contract.maintenance_margin.status
            if contract.expiry is not None:
                facts["expiry_date"] = contract.expiry.expiry_date.status
            if contract.settlement is not None:
                facts["settlement"] = contract.settlement.status
            if contract.trading_session is not None:
                facts["trading_session"] = contract.trading_session.status
            summary[symbol] = facts
        return summary

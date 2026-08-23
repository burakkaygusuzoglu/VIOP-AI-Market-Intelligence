"""Contract metadata port (master spec sections 73 and 118).

Phase 0 deliberately deferred this port because its return type did not exist:
a ``FuturesContract`` carries multipliers, tick sizes, margins and expiries,
none of which could be typed honestly before Phase 3 defined them, and typing
the port with ``Any`` to create it early would have been worse than waiting.

The port exists now, and it is the **only** way a mutable exchange fact enters
the system. Nothing in the domain hard-codes a multiplier, a tick size, a
margin or a session; anything that needs one asks a provider, and what comes
back carries its own provenance.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.domain.futures.contract import FuturesContract


@runtime_checkable
class ContractMetadataProvider(Protocol):
    """Supplies contract specifications with their verification status.

    Implementations must preserve the ``VerificationStatus`` they were given.
    A provider that promotes ``UNVERIFIED`` to ``VERIFIED_CURRENT_FACT`` -
    because the value looked plausible, or because a calculation downstream
    needed one - defeats the entire section 118 mechanism, which exists
    precisely because a wrong multiplier produces a wrong number that looks
    completely normal.
    """

    async def get_contract(self, symbol: str) -> FuturesContract | None:
        """Return the contract for ``symbol``, or ``None`` when unknown.

        ``None`` means "this provider has nothing for that symbol". It must
        never be a stand-in for a contract assembled from defaults.
        """
        ...

    async def list_symbols(self) -> Sequence[str]:
        """Every symbol this provider can describe."""
        ...

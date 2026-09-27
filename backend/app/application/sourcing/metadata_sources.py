"""Contract metadata from several sources, through one gate (Phase 15 Part 1).

``VerifiedMetadataSources`` is itself a :class:`ContractMetadataProvider`, so
everything downstream - the product resolver, the risk engine, paper trading,
backtesting, shadow - keeps the trust boundary it already has. What changes is
that the record it hands on has passed :func:`assess_contract_metadata`: one
authoritative, current, matching record, chosen by the section 118 rule, or
nothing at all. "Nothing" is ``None``, which every consumer already turns into
``METADATA_UNAVAILABLE``; the reason is available from :meth:`assess`.

Sources are named, and each is asked only for the symbol requested. A source
that raises is recorded as having nothing and logged by exception type only -
its message may carry a URL, an account id or a token.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from app.application.ports.contract_metadata import ContractMetadataProvider
from app.domain.futures.contract import FuturesContract
from app.domain.sourcing.metadata import (
    MetadataCandidate,
    MetadataVerdict,
    assess_contract_metadata,
)

__all__ = ["VerifiedMetadataSources"]

_LOG = logging.getLogger(__name__)


class VerifiedMetadataSources:
    """Implements ``ContractMetadataProvider`` over named sources."""

    def __init__(
        self,
        sources: Sequence[tuple[str, ContractMetadataProvider]],
        *,
        now: Callable[[], datetime],
        max_age: timedelta,
    ) -> None:
        names = [name for name, _ in sources]
        if len(set(names)) != len(names):
            raise ValueError("every metadata source has a distinct name")
        self._sources = tuple(sources)
        self._now = now
        self._max_age = max_age

    async def assess(self, symbol: str) -> MetadataVerdict:
        candidates: list[MetadataCandidate] = []
        for name, source in self._sources:
            try:
                record = await source.get_contract(symbol)
            except Exception as error:  # noqa: BLE001 - reported safely, below
                _LOG.warning(
                    "contract metadata source failed",
                    extra={"source": name, "error_type": type(error).__name__},
                )
                continue
            if record is not None:
                candidates.append(MetadataCandidate(source_id=name, contract=record))
        return assess_contract_metadata(
            symbol, tuple(candidates), at=self._now(), max_age=self._max_age
        )

    async def get_contract(self, symbol: str) -> FuturesContract | None:
        verdict = await self.assess(symbol)
        return verdict.contract if verdict.usable else None

    async def list_symbols(self) -> Sequence[str]:
        symbols: set[str] = set()
        for name, source in self._sources:
            try:
                symbols.update(await source.list_symbols())
            except Exception as error:  # noqa: BLE001 - reported safely, below
                _LOG.warning(
                    "contract metadata source failed",
                    extra={"source": name, "error_type": type(error).__name__},
                )
        return tuple(sorted(symbols))

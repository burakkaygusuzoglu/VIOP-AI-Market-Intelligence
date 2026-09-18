"""Futures implementations of the paper-trading product ports (Phase 9).

``FuturesProductResolver`` is the Phase 8.5 trust path, unchanged: a symbol is
looked up in the composed ``ContractMetadataProvider`` and a returned record is
wrapped in ``FuturesProductPolicy``. No record, no policy. The symbol text alone
never selects anything.

``FuturesSnapshotCodec`` freezes the whole contract record - every fact with its
own status, source, timestamp and note - so a paper position keeps simulating
under exactly the metadata it was opened with, even after the provider's record
changes. It never upgrades a status: an unverified fact is restored unverified.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.application.ports.contract_metadata import ContractMetadataProvider
from app.application.ports.paper import ProductSnapshotError
from app.domain.common.verification import VerificationStatus, VerifiedValue
from app.domain.futures.contract import (
    ContractExpiry,
    FuturesContract,
    SettlementType,
    ValuationModel,
)
from app.domain.futures.policy import FuturesProductPolicy
from app.domain.instrument.asset_class import AssetClass
from app.domain.instrument.policy import ProductPolicy

SNAPSHOT_KIND = "FUTURES_CONTRACT"
SNAPSHOT_VERSION = 1


class FuturesProductResolver:
    """``ProductResolver`` over a contract metadata provider."""

    def __init__(self, provider: ContractMetadataProvider) -> None:
        self._provider = provider

    async def resolve(self, symbol: str) -> ProductPolicy | None:
        record = await self._provider.get_contract(symbol)
        return None if record is None else FuturesProductPolicy(record)


class FuturesSnapshotCodec:
    """``ProductSnapshotCodec`` for futures contracts."""

    def snapshot(self, product: ProductPolicy) -> Mapping[str, Any]:
        if not isinstance(product, FuturesProductPolicy):
            raise ProductSnapshotError(
                f"no snapshot format exists for {type(product).__name__}; only futures "
                "products are implemented"
            )
        contract = product.contract
        return {
            "kind": SNAPSHOT_KIND,
            "version": SNAPSHOT_VERSION,
            "contract": {
                "symbol": contract.symbol,
                "underlying_symbol": contract.underlying_symbol,
                "contract_name": contract.contract_name,
                "multiplier": _fact(contract.multiplier, _decimal_text),
                "tick_size": _fact(contract.tick_size, _decimal_text),
                "tick_value": _optional_fact(contract.tick_value, _decimal_text),
                "initial_margin": _optional_fact(contract.initial_margin, _decimal_text),
                "maintenance_margin": _optional_fact(contract.maintenance_margin, _decimal_text),
                "expiry": None
                if contract.expiry is None
                else {
                    "expiry_date": _fact(contract.expiry.expiry_date, date.isoformat),
                    "last_trading_time": _optional_fact(
                        contract.expiry.last_trading_time, datetime.isoformat
                    ),
                },
                "settlement": _optional_fact(contract.settlement, _enum_text),
                "trading_session": _optional_fact(contract.trading_session, str),
                "valuation": contract.valuation.value,
                "classification": _optional_fact(contract.classification, _enum_text),
            },
        }

    def restore(self, snapshot: Mapping[str, Any]) -> ProductPolicy:
        if snapshot.get("kind") != SNAPSHOT_KIND or snapshot.get("version") != SNAPSHOT_VERSION:
            raise ProductSnapshotError(
                f"unrecognised product snapshot {snapshot.get('kind')!r} "
                f"version {snapshot.get('version')!r}"
            )
        data = snapshot["contract"]
        try:
            expiry = data["expiry"]
            record = FuturesContract(
                symbol=data["symbol"],
                underlying_symbol=data["underlying_symbol"],
                contract_name=data["contract_name"],
                multiplier=_restore(data["multiplier"], _decimal),
                tick_size=_restore(data["tick_size"], _decimal),
                tick_value=_restore_optional(data["tick_value"], _decimal),
                initial_margin=_restore_optional(data["initial_margin"], _decimal),
                maintenance_margin=_restore_optional(data["maintenance_margin"], _decimal),
                expiry=None
                if expiry is None
                else ContractExpiry(
                    expiry_date=_restore(expiry["expiry_date"], date.fromisoformat),
                    last_trading_time=_restore_optional(
                        expiry["last_trading_time"], datetime.fromisoformat
                    ),
                ),
                settlement=_restore_optional(data["settlement"], SettlementType),
                trading_session=_restore_optional(data["trading_session"], str),
                valuation=ValuationModel(data["valuation"]),
                classification=_restore_optional(data["classification"], AssetClass),
            )
        except (KeyError, TypeError, ValueError, InvalidOperation) as error:
            raise ProductSnapshotError(f"stored futures snapshot is unreadable: {error}") from error
        return FuturesProductPolicy(record)


def _fact[T](fact: VerifiedValue[T], encode: Any) -> dict[str, Any]:
    return {
        "value": encode(fact.value),
        "status": fact.status.value,
        "source": fact.source,
        "as_of": None if fact.as_of is None else fact.as_of.isoformat(),
        "note": fact.note,
    }


def _optional_fact[T](fact: VerifiedValue[T] | None, encode: Any) -> dict[str, Any] | None:
    return None if fact is None else _fact(fact, encode)


def _restore(data: Mapping[str, Any], decode: Any) -> VerifiedValue[Any]:
    as_of = data["as_of"]
    return VerifiedValue(
        value=decode(data["value"]),
        status=VerificationStatus(data["status"]),
        source=data["source"],
        as_of=None if as_of is None else datetime.fromisoformat(as_of),
        note=data["note"],
    )


def _restore_optional(data: Mapping[str, Any] | None, decode: Any) -> VerifiedValue[Any] | None:
    return None if data is None else _restore(data, decode)


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _decimal(value: str) -> Decimal:
    if not isinstance(value, str):
        raise TypeError("a stored decimal must be text")
    return Decimal(value)


def _enum_text(value: SettlementType | AssetClass) -> str:
    return value.value

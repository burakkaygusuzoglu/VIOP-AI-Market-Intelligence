"""JSON-compatible encoding of the values a paper position is opened with.

Strings for every number and time. A ``Decimal`` written as a JSON number would
be read back through a float by most JSON libraries, and a paper P&L that
drifted in its last digit on a round trip would no longer match its own ledger.

Decoding is strict: an unknown key, a missing key or an unparseable value is an
error, never a default. A stored position that cannot be read back exactly is
reported as unreadable rather than simulated under guessed rules.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.common.enums import Direction, Timeframe
from app.domain.paper import (
    FeeMode,
    FeePolicy,
    PositionOrigin,
    PositionSpec,
    RiskApproval,
    SameBarPolicy,
    SimulationPolicy,
    SlippageMode,
    SlippagePolicy,
    TargetSpec,
)
from app.domain.risk.sizing import SizingOutcome


class StoredValueError(ValueError):
    """A stored value this build cannot read back exactly."""


def encode_policy(policy: SimulationPolicy) -> dict[str, Any]:
    return {
        "rules_version": policy.rules_version,
        "same_bar": policy.same_bar.value,
        "slippage_mode": policy.slippage.mode.value,
        "slippage_points": _opt(policy.slippage.points),
        "fee_mode": policy.fees.mode.value,
        "fee_per_unit": _opt(policy.fees.per_unit),
    }


def decode_policy(data: Mapping[str, Any]) -> SimulationPolicy:
    _exact_keys(
        data,
        {
            "rules_version",
            "same_bar",
            "slippage_mode",
            "slippage_points",
            "fee_mode",
            "fee_per_unit",
        },
        "simulation policy",
    )
    try:
        return SimulationPolicy(
            rules_version=str(data["rules_version"]),
            same_bar=SameBarPolicy(data["same_bar"]),
            slippage=SlippagePolicy(
                SlippageMode(data["slippage_mode"]), _opt_decimal(data["slippage_points"])
            ),
            fees=FeePolicy(FeeMode(data["fee_mode"]), _opt_decimal(data["fee_per_unit"])),
        )
    except (ValueError, KeyError) as error:
        raise StoredValueError(f"stored simulation policy is unreadable: {error}") from error


def encode_spec(spec: PositionSpec) -> dict[str, Any]:
    return {
        "position_id": spec.position_id,
        "symbol": spec.symbol,
        "direction": spec.direction.value,
        "quantity": spec.quantity,
        "intended_entry": _text(spec.intended_entry),
        "stop": _text(spec.stop),
        "targets": [
            {"price": _text(target.price), "quantity": target.quantity} for target in spec.targets
        ],
        "timeframe": spec.timeframe.value,
        "decision_time": spec.decision_time.isoformat(),
        "policy": encode_policy(spec.policy),
        "origin": spec.origin.value,
        "note": spec.note,
    }


def decode_spec(data: Mapping[str, Any]) -> PositionSpec:
    _exact_keys(
        data,
        {
            "position_id",
            "symbol",
            "direction",
            "quantity",
            "intended_entry",
            "stop",
            "targets",
            "timeframe",
            "decision_time",
            "policy",
            "origin",
            "note",
        },
        "position specification",
    )
    try:
        return PositionSpec(
            position_id=str(data["position_id"]),
            symbol=str(data["symbol"]),
            direction=Direction(data["direction"]),
            quantity=int(data["quantity"]),
            intended_entry=_decimal(data["intended_entry"]),
            stop=_decimal(data["stop"]),
            targets=tuple(
                TargetSpec(price=_decimal(item["price"]), quantity=int(item["quantity"]))
                for item in data["targets"]
            ),
            timeframe=Timeframe(data["timeframe"]),
            decision_time=datetime.fromisoformat(str(data["decision_time"])),
            policy=decode_policy(data["policy"]),
            origin=PositionOrigin(data["origin"]),
            note=None if data["note"] is None else str(data["note"]),
        )
    except (ValueError, KeyError, TypeError) as error:
        raise StoredValueError(f"stored position specification is unreadable: {error}") from error


def encode_approval(approval: RiskApproval) -> dict[str, Any]:
    return {
        "outcome": approval.outcome.value,
        "allowed_units": approval.allowed_units,
        "reason": approval.reason,
    }


def decode_approval(data: Mapping[str, Any]) -> RiskApproval:
    _exact_keys(data, {"outcome", "allowed_units", "reason"}, "risk approval")
    try:
        allowed = data["allowed_units"]
        return RiskApproval(
            outcome=SizingOutcome(data["outcome"]),
            allowed_units=None if allowed is None else int(allowed),
            reason=str(data["reason"]),
        )
    except (ValueError, TypeError) as error:
        raise StoredValueError(f"stored risk approval is unreadable: {error}") from error


def _exact_keys(data: Mapping[str, Any], expected: set[str], what: str) -> None:
    keys = set(data)
    if keys != expected:
        raise StoredValueError(
            f"stored {what} has unexpected fields {sorted(keys - expected)} "
            f"or lacks {sorted(expected - keys)}"
        )


def _text(value: Decimal) -> str:
    return format(value, "f")


def _opt(value: Decimal | None) -> str | None:
    return None if value is None else _text(value)


def _decimal(value: Any) -> Decimal:
    if not isinstance(value, str):
        raise StoredValueError(f"a stored decimal must be text, got {type(value).__name__}")
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise StoredValueError(f"{value!r} is not a decimal") from error


def _opt_decimal(value: Any) -> Decimal | None:
    return None if value is None else _decimal(value)

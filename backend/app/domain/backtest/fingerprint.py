"""What makes two backtests the same backtest.

Two identities, and the difference between them is the point:

* the **configuration fingerprint** answers "would these two runs produce the
  same financial result?" It covers the dataset, the strategy and its version
  and parameters, the interval, the simulation policy, the risk configuration,
  the frozen product snapshot and the runner's own rules version;
* the **run id** answers "which execution was this?" Two runs of one
  configuration are legitimate - a person may simply run it again - so the run
  id is distinct, and comparing results across them is how reproducibility is
  checked rather than something the storage layer prevents.

Audit facts take no part in either: when a run was requested, who looked at it,
how long it took. A fingerprint that moved with the clock would make
reproducibility unprovable.

The digest is taken over one canonical JSON document rather than concatenated
fields. Concatenation hides its own boundaries - a parameter value carrying the
bytes of the next field would collide with a genuinely different configuration
- and Phase 11 already paid for that lesson once.

Pure: stdlib and ``app.domain.common`` only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

CONFIGURATION_PREFIX = "BC-"
RUN_PREFIX = "BR-"


def canonical_decimal(value: Decimal | None) -> str:
    """One spelling per number, so ``1.50`` and ``1.5`` are one parameter."""
    return "" if value is None else format(value.normalize(), "f")


def canonical_time(value: datetime) -> str:
    """One spelling per instant, so two offsets for one moment agree."""
    return value.astimezone(UTC).isoformat()


def configuration_fingerprint(
    *,
    dataset_id: str,
    symbol: str,
    driver: str,
    interval_start: datetime,
    interval_end: datetime,
    strategy_id: str,
    strategy_version: str,
    strategy_parameters: Mapping[str, str],
    simulation: Mapping[str, str],
    risk: Mapping[str, str],
    product_snapshot: Mapping[str, str],
    runner_version: str,
) -> str:
    """The semantic identity of a configuration.

    Every argument is something that can change a number. Adding a field here
    is a deliberate act: it means two configurations that used to be the same
    are now different, and old fingerprints stop matching.
    """
    document = {
        "dataset": {"id": dataset_id, "symbol": symbol.strip(), "driver": driver},
        "interval": {
            "start": canonical_time(interval_start),
            "end": canonical_time(interval_end),
        },
        "strategy": {
            "id": strategy_id,
            "version": strategy_version,
            "parameters": dict(sorted(strategy_parameters.items())),
        },
        "simulation": dict(sorted(simulation.items())),
        "risk": dict(sorted(risk.items())),
        "product": dict(sorted(product_snapshot.items())),
        "runner": runner_version,
    }
    encoded = json.dumps(document, separators=(",", ":"), sort_keys=True)
    return CONFIGURATION_PREFIX + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def run_id(configuration: str, attempt_key: str) -> str:
    """A distinct identity per execution of a configuration.

    Derived from the caller's own idempotency key, so retrying one request
    names the same run while a deliberate second run of the same configuration
    names a different one.
    """
    digest = hashlib.sha256(f"{configuration}|{attempt_key}".encode()).hexdigest()
    return RUN_PREFIX + digest[:24]


def result_digest(
    *,
    boundaries: int,
    decisions: Mapping[str, int],
    positions: Mapping[str, Mapping[str, str]],
) -> str:
    """A canonical digest of everything a run *concluded*.

    Used to compare two runs of one configuration: identical inputs must give
    an identical digest. Ids that carry no meaning - a run id, a row id - are
    excluded by the caller, which passes positions keyed by their ordinal.
    """
    document = {
        "boundaries": boundaries,
        "decisions": dict(sorted(decisions.items())),
        "positions": {key: dict(sorted(value.items())) for key, value in sorted(positions.items())},
    }
    encoded = json.dumps(document, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "CONFIGURATION_PREFIX",
    "RUN_PREFIX",
    "canonical_decimal",
    "canonical_time",
    "configuration_fingerprint",
    "result_digest",
    "run_id",
]

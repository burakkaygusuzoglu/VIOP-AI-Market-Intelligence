"""What makes two shadow observations the same observation (Phase 14 Part 1).

Three identities, kept apart:

* the **configuration fingerprint** (``SC-``) answers "would these two runs
  evaluate the same way?" - the source, the instrument label, the strategy and
  its version and parameters, the driver and subscribed timeframes, the risk
  configuration and the account it was stated against;
* the **run id** (``SR-``) answers "which observation was this?" Two runs of
  one configuration are legitimate: a stream is not a dataset, and the second
  run sees different market moments;
* the **decision key** answers "have we already recorded this observation?" It
  covers the run, the market boundary and a fingerprint of the exact inputs the
  policy was given. A reconnect, an HTTP retry or a replayed notification
  produces the identical key, and the journal's unique constraint makes the
  second write a no-op.

A random identifier would satisfy none of these: it cannot tell a retry from a
new evaluation. Audit times take no part - a key that moved with the clock
would make deduplication impossible.

The digest is taken over one canonical JSON document, not concatenated fields,
for the reason Phase 11 recorded: concatenation hides its own boundaries.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime

from app.domain.backtest.fingerprint import canonical_decimal, canonical_time
from app.domain.shadow.run import CONFIGURATION_PREFIX, RUN_PREFIX

__all__ = [
    "CONFIGURATION_PREFIX",
    "RUN_PREFIX",
    "canonical_decimal",
    "canonical_time",
    "outcome_key",
    "configuration_fingerprint",
    "decision_key",
    "inputs_fingerprint",
]


def _digest(document: object) -> str:
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def configuration_fingerprint(
    *,
    source_id: str,
    instrument_label: str,
    provenance: str,
    strategy_id: str,
    strategy_version: str,
    strategy_parameters: Mapping[str, str],
    driver: str,
    timeframes: Sequence[str],
    risk: Mapping[str, str],
    account: Mapping[str, str],
    evidence_rules: str,
) -> str:
    """Identity of *what would be evaluated*, never of when it ran."""
    return CONFIGURATION_PREFIX + _digest(
        {
            "source_id": source_id,
            "instrument_label": instrument_label,
            "provenance": provenance,
            "strategy": {
                "id": strategy_id,
                "version": strategy_version,
                "parameters": dict(sorted(strategy_parameters.items())),
            },
            "driver": driver,
            "timeframes": sorted(timeframes),
            "risk": dict(sorted(risk.items())),
            "account": dict(sorted(account.items())),
            # What the run records, not only what it decides: two runs that
            # keep different evidence are not one configuration observed twice.
            "evidence_rules": evidence_rules,
            "rules": "shadow/v1",
        }
    )


def inputs_fingerprint(
    *,
    boundary: datetime,
    bar: Mapping[str, str],
    current: Mapping[str, str | None],
    previous: Mapping[str, str | None],
    higher: Mapping[str, Mapping[str, str | None]],
    bars_available: int,
) -> str:
    """Digest of exactly what the policy was handed at this boundary."""
    return _digest(
        {
            "boundary": canonical_time(boundary),
            "bar": dict(sorted(bar.items())),
            "current": dict(sorted(current.items())),
            "previous": dict(sorted(previous.items())),
            "higher": {key: dict(sorted(value.items())) for key, value in sorted(higher.items())},
            "bars_available": bars_available,
        }
    )


def decision_key(
    *,
    run_id: str,
    configuration: str,
    boundary: datetime | None,
    inputs: str | None,
    marker: str = "",
) -> str:
    """Stable identity of one journal entry within one run."""
    return _digest(
        {
            "run_id": run_id,
            "configuration": configuration,
            "boundary": None if boundary is None else canonical_time(boundary),
            "inputs": inputs,
            "marker": marker,
        }
    )


def outcome_key(
    *,
    run_id: str,
    decision_key_value: str,
    state: str,
    event: str,
    observed_to: datetime | None,
    event_at: datetime | None,
) -> str:
    """Stable identity of one published outcome record.

    The same development observed twice - a retry, a resumed consumer, a second
    pass over the same candles - yields the same key and is written once. A
    development that genuinely changed (pending became observed) is a different
    key, so it is a new record rather than an edit of the old one.
    """
    return _digest(
        {
            "run_id": run_id,
            "decision_key": decision_key_value,
            "state": state,
            "event": event,
            "observed_to": None if observed_to is None else canonical_time(observed_to),
            "event_at": None if event_at is None else canonical_time(event_at),
            "rules": "shadow-outcome/v1",
        }
    )

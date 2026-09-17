"""The identity of one analysis run (§7).

An analysis is **ephemeral**: it lives for the request that produced it and is
never stored. That makes its identity more important rather than less, because
nothing else can be consulted later to reconstruct what was analysed.

## Identity is derived from inputs, never from the clock

`analysis_id` is a SHA-256 over the canonical form of the *inputs*: the symbol,
each timeframe's dataset digest, the risk settings and the user-supplied
prices. Two semantically identical requests therefore produce the same id, and
a request that differs anywhere produces a different one.

This repeats a correction Phase 7 already paid for. `SynthesisContext` once
carried `generated_at`, which meant the same deterministic analysis hashed
differently merely because it was run twice — an identifier that changes when
nothing about the market changed identifies nothing. So:

* `analysis_as_of` — the open time of the newest candle read — is a *semantic
  input* and part of the identity.
* `generated_at` — when the run happened — is metadata *about* the run and is
  deliberately outside it.

## No claim of persistence

Nothing here writes anything. `analysis_id` is a content address, not a
database key: it cannot be fetched back, and there is no endpoint that pretends
otherwise.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.domain.common.enums import Timeframe


def _digest(*parts: str) -> str:
    """A short, stable, content-derived hex digest.

    SHA-256 rather than `hash()`, which is randomised per process and would
    make two runs of the same input disagree.
    """
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def dataset_digest(timeframe: Timeframe, content: str) -> str:
    """Identify a timeframe's dataset by its exact bytes.

    Over the raw text, before parsing: two files that parse to the same candles
    but differ in whitespace or row order are not the same input, and the
    Data Quality Engine's findings can differ between them.
    """
    return _digest("dataset", timeframe.value, content)[:16]


@dataclass(frozen=True, slots=True)
class DatasetIdentity:
    """What one timeframe contributed, and how much of it survived validation."""

    timeframe: Timeframe
    digest: str
    source_name: str
    row_count: int
    """Rows parsed into candles. Malformed rows are excluded and reported
    separately, so this is not the file's line count."""

    usable: bool
    """Whether the Data Quality Engine produced a series from it."""


@dataclass(frozen=True, slots=True)
class AnalysisIdentity:
    """The immutable identity of one analysis run."""

    analysis_id: str
    symbol: str
    datasets: tuple[DatasetIdentity, ...]

    analysis_as_of: datetime | None
    """Open time of the newest candle across every usable timeframe — when the
    market data *ends*. A semantic input, part of the id."""

    generated_at: datetime
    """When this run executed. Metadata about the attempt; deliberately **not**
    part of the id, and never used as market-data identity."""

    risk_settings_digest: str | None
    """Identifies the risk configuration without echoing the user's equity."""

    contract_metadata_verified: bool
    """Whether the contract used for futures-dependent calculation came from
    the trusted provider as a current verified fact."""

    @property
    def short_id(self) -> str:
        return self.analysis_id[:12]


def build_identity(
    *,
    symbol: str,
    datasets: tuple[DatasetIdentity, ...],
    analysis_as_of: datetime | None,
    generated_at: datetime,
    risk_settings_digest: str | None,
    contract_metadata_verified: bool,
) -> AnalysisIdentity:
    """Derive the identity from the inputs alone."""
    parts = [
        "analysis/v1",
        symbol,
        analysis_as_of.isoformat() if analysis_as_of is not None else "no-as-of",
        risk_settings_digest or "no-risk-settings",
    ]
    for dataset in datasets:
        parts.extend((dataset.timeframe.value, dataset.digest, str(dataset.row_count)))

    return AnalysisIdentity(
        analysis_id=_digest(*parts),
        symbol=symbol,
        datasets=datasets,
        analysis_as_of=analysis_as_of,
        generated_at=generated_at,
        risk_settings_digest=risk_settings_digest,
        contract_metadata_verified=contract_metadata_verified,
    )


def risk_digest(
    *,
    equity: Decimal | None,
    used_margin: Decimal | None,
    mode: str | None,
    fixed_risk: Decimal | None,
    risk_ratio: Decimal | None,
    max_contracts: int | None,
    entry_price: Decimal | None,
    stop_price: Decimal | None,
) -> str | None:
    """Identify the risk configuration, or ``None`` when there is none.

    The equity *value* participates in the digest — a different account size is
    a different analysis — but the digest is one-way, so the identity can be
    logged and compared without the balance travelling with it.
    """
    if equity is None and mode is None and entry_price is None:
        return None
    return _digest(
        "risk/v1",
        str(equity),
        str(used_margin),
        str(mode),
        str(fixed_risk),
        str(risk_ratio),
        str(max_contracts),
        str(entry_price),
        str(stop_price),
    )[:16]

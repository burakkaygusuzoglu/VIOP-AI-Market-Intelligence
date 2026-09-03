"""Stable identifiers for everything a synthesis may cite (§6).

A model asked to justify a conclusion will produce a justification. Whether the
thing it cites exists is a separate question, and the only way to answer it
mechanically is to give every citable fact an identifier the model did not
choose and cannot mint.

So the synthesis contract is: **narrate freely, cite only by ID.** Every
identifier in an output is looked up in the context that produced it, and an
unknown one makes the whole output invalid rather than merely suspicious.

## Why the identifiers look like this

`EV-BULL-8F2A1C9D0B`, `CON-4E7B22A105`, `SQ-TREND_ALIGNMENT`. Three properties
matter and none is cosmetic:

* **Kind-prefixed.** `ReferenceKind` is recoverable from the identifier itself,
  so a validator can tell that an output cited a contradiction where evidence
  was required without consulting anything else.
* **Deterministic.** The same fact must always produce the same identifier, in
  this process and in a fresh one, or the digest in `canonical.py` changes for
  an unchanged situation and audit reconstruction stops working.
* **Content-derived.** The suffix is a digest of *what the fact is*, never of
  where it appeared in a list.

## Why content-derived, when a counter was simpler

Phase 7A numbered evidence sequentially after a canonical sort. That is
deterministic - the same inputs always gave the same numbering - but it is not
*stable*, and the difference turned out to matter. Measured:

    before:  EV-BULL-001=B   EV-BULL-002=A   EV-BULL-003=C
    after:   EV-BULL-001=NEW EV-BULL-002=B   EV-BULL-003=A   EV-BULL-004=C

Inserting one unrelated observation renumbered every existing one. A stored
synthesis citing `EV-BULL-002` still parses, still validates, and now points at
a different fact. That silently corrupts exactly the things references exist
for: audit records, "why did the analysis change?" diffing, and replay,
backtest or shadow comparisons across runs.

So identity is derived from the fact's own content. Adding evidence adds an
identifier and moves none. The digest is SHA-256 over a canonically joined
description - **never** `hash()`, which is randomised per process and would
make identifiers differ between two workers reading the same market.

Ten hex characters are kept: enough that a collision across the few dozen facts
in one context is not a practical concern, short enough to stay readable in a
prompt and a log. Assembly asserts uniqueness anyway rather than trusting the
arithmetic.

Component-shaped facts keep their component name (`SQ-TREND_ALIGNMENT`) because
that name is already stable, unique and far more readable than a digest.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum, unique


@unique
class ReferenceKind(StrEnum):
    """What sort of fact an identifier points at."""

    BULL_EVIDENCE = "EV-BULL"
    BEAR_EVIDENCE = "EV-BEAR"
    NEUTRAL_EVIDENCE = "EV-NEUTRAL"
    CONTRADICTION = "CON"
    SETUP_QUALITY_COMPONENT = "SQ"
    ENTRY_QUALITY_COMPONENT = "EQ"
    RISK_FINDING = "RISK"
    SUITABILITY_FINDING = "SUIT"
    MISSING_INFORMATION = "MISS"
    VISION_OBSERVATION = "VIS"
    SCENARIO = "SCEN"
    NUMERIC_FACT = "FACT"
    """An authoritative number a synthesis may **cite but not restate** (§4).

    The division of labour: Claude chooses which fact is relevant, Python owns
    the fact's value. A narrative says "the risk/reward is below the policy
    floor, see FACT-…" and the presentation layer renders the figure from the
    fact. Nothing asks the model to repeat a number, so nothing depends on it
    repeating one correctly.
    """

    @property
    def prefix(self) -> str:
        return self.value


@unique
class AuthorityClass(StrEnum):
    """How much weight a fact is entitled to (§3).

    Deliberately *not* a second precedence hierarchy. `DataSourcePriority`
    already ranks sources and stays the authority on ranking; this classifies
    what **kind** of thing a synthesis input is, so a narrative can never
    present a visual inference as a measurement. Where both apply, the
    `DataSourcePriority` on the underlying value continues to decide which
    value wins.
    """

    OBSERVED_FACT = "OBSERVED_FACT"
    """Something read from validated market data."""

    CALCULATED_METRIC = "CALCULATED_METRIC"
    """Deterministic Python output - an indicator, a score, a size."""

    USER_CONFIRMED_OBSERVATION = "USER_CONFIRMED_OBSERVATION"
    """A human vouched for what a screenshot shows. Raises authority over a
    raw reading; never reaches validated market data."""

    VISION_EXTRACTION = "VISION_EXTRACTION"
    """A value a model read off a picture."""

    AI_VISUAL_INFERENCE = "AI_VISUAL_INFERENCE"
    """A judgement a model formed from a picture. The weakest thing here."""

    SCENARIO = "SCENARIO"
    """A deterministic Phase 4 scenario record."""

    RISK = "RISK"
    """A Phase 3 money result."""

    MISSING = "MISSING"
    """Explicitly absent. Never neutral, never weak - see §20."""

    @property
    def is_model_derived(self) -> bool:
        """True for anything a model produced rather than measured.

        The validator uses this to refuse numeric authority: a value carrying a
        model-derived class may be quoted as an observation, never used as the
        figure a calculation rests on.
        """
        return self in {AuthorityClass.VISION_EXTRACTION, AuthorityClass.AI_VISUAL_INFERENCE}


_REFERENCE_PATTERN = re.compile(r"^(?P<prefix>[A-Z]+(?:-[A-Z]+)?)-(?P<suffix>[A-Z0-9_]+)$")


@dataclass(frozen=True, slots=True)
class ContextRef:
    """One citable fact: an identifier, its kind, and a short human label."""

    ref_id: str
    kind: ReferenceKind
    label: str
    authority: AuthorityClass

    def __post_init__(self) -> None:
        if not _REFERENCE_PATTERN.match(self.ref_id):
            raise ValueError(f"{self.ref_id!r} is not a well-formed reference id")
        if not self.ref_id.startswith(f"{self.kind.prefix}-"):
            raise ValueError(f"{self.ref_id!r} does not carry the {self.kind.prefix} prefix")
        if not self.label.strip():
            raise ValueError(f"{self.ref_id} carries no label")


REF_DIGEST_LENGTH = 10
"""Hex characters kept from the content digest.

40 bits. Across the few dozen facts one context holds, a collision is not a
practical concern; assembly checks for one regardless, because "not practical"
is not the same as "impossible" and a silent collision would merge two facts.
"""


def make_ref_id(kind: ReferenceKind, suffix: str | int) -> str:
    """Build an identifier for ``kind`` from an explicit suffix.

    Used for facts whose own name is already stable and unique - a quality
    component, a scenario case. For evidence and findings, whose only natural
    name is their content, use `content_ref_id`.

    An integer suffix is accepted for tests and fixtures and is zero-padded so
    identifiers sort the way the facts do; production assembly does not use it,
    because a positional number is exactly the instability this module now
    avoids.
    """
    if isinstance(suffix, int):
        if suffix < 1:
            raise ValueError("reference numbering starts at 1")
        return f"{kind.prefix}-{suffix:03d}"

    cleaned = suffix.strip().upper().replace(" ", "_")
    if not cleaned:
        raise ValueError("a reference suffix cannot be blank")
    return f"{kind.prefix}-{cleaned}"


def content_ref_id(kind: ReferenceKind, *parts: object) -> str:
    """A stable identifier derived from what the fact *is*.

    ``parts`` must describe the fact's identity and nothing about its position:
    its category, source, timeframe, role and reason - not its index. Two runs
    over the same market produce the same identifier; adding an unrelated fact
    produces one more identifier and changes none.

    Joined with a separator that cannot appear in the rendered parts, so
    ``("AB", "C")`` and ``("A", "BC")`` cannot collide by concatenation.
    """
    rendered = "\x1f".join("" if part is None else str(part) for part in parts)
    material = f"{kind.prefix}\x1e{rendered}".encode()
    digest = hashlib.sha256(material).hexdigest()[:REF_DIGEST_LENGTH].upper()
    return f"{kind.prefix}-{digest}"


def kind_of(ref_id: str) -> ReferenceKind | None:
    """Recover the kind from an identifier, or ``None`` if it names none.

    Longest prefix first: `EV-BULL` must win over any shorter prefix that
    happens to be a prefix of it.
    """
    for kind in sorted(ReferenceKind, key=lambda item: len(item.prefix), reverse=True):
        if ref_id.startswith(f"{kind.prefix}-"):
            return kind
    return None

"""Canonical form and digest for a synthesis context (§4, §5).

A synthesis attempt must be reconstructable: given the audit record, someone
must be able to say *this* context produced *that* request. That requires one
unambiguous serialisation, and it requires two semantically identical contexts
to produce byte-identical output.

## What "semantically identical" has to survive

* **Container order.** Evidence collected into a list in a different order is
  the same evidence. Ordering happens during assembly, and this module never
  iterates a set or a dict whose order was not fixed first.
* **Decimal representation.** `Decimal("61.27")` and `Decimal("61.270")` are
  the same number. They are normalised to one exact text form - never through
  a float, which would make 61.27 unrepresentable and the digest meaningless.
* **Enum identity.** Enums serialise by value, so a renamed member is a real
  change and a reordered enum is not.

## What must never appear

Secrets, API keys, raw image bytes (§4, §17). The context type has no field
that could carry them - vision arrives as already-extracted text - so this is a
property of the design rather than a filter applied here. The test suite asserts
it against a context built with a credential-shaped fixture anyway, because a
future field could break it silently.

## Missing stays visible

A `None` is serialised as an explicit null, not omitted. An absent key and a
key whose value is unknown are different statements (§20), and a canonical form
that erased the difference would let a synthesis treat one as the other.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from app.application.synthesis.context import SynthesisContext
from app.application.synthesis.untrusted import UntrustedText

CANONICAL_FORMAT_VERSION = "synthesis-canonical/1"


def _canonical_value(value: Any) -> Any:
    """Convert one value into a JSON-safe canonical form.

    Ordering is preserved rather than imposed: everything reaching here has
    already been ordered during assembly, and re-sorting at this level would
    hide an assembly bug that produced an unstable order.
    """
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Decimal):
        # Exact, and never via float. `normalize` collapses trailing zeros so
        # 61.270 and 61.27 agree; the "f" format keeps 1E+2 from appearing.
        return format(value.normalize(), "f")
    if isinstance(value, float):
        # No context field is a float today. If one ever appears, fail loudly
        # rather than silently making the digest unreproducible.
        raise TypeError("a float cannot appear in a canonical context; use Decimal")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("a canonical context requires timezone-aware timestamps")
        return value.isoformat()
    if isinstance(value, UntrustedText):
        # Rendered through the trust boundary: what reaches an audit record is
        # what would reach a prompt, delimiters neutralised and all.
        return {
            "origin": value.origin.value,
            "ref_id": value.ref_id,
            "content": value.safe_content,
            "was_neutralised": value.was_neutralised,
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _canonical_value(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_canonical_value(item) for item in value]
    if isinstance(value, bytes | bytearray):
        raise TypeError("raw bytes must never enter a synthesis context")
    raise TypeError(f"{type(value).__name__} has no canonical form")


def canonical_context(context: SynthesisContext) -> dict[str, Any]:
    """The canonical dictionary form of a context."""
    body = {item.name: _canonical_value(getattr(context, item.name)) for item in fields(context)}
    return {"canonical_format": CANONICAL_FORMAT_VERSION, "context": body}


def canonical_json(context: SynthesisContext) -> str:
    """The canonical text form - stable, sorted, and exactly hashable.

    ``sort_keys`` orders mapping keys; sequences keep the order assembly gave
    them. ``ensure_ascii`` is off so Turkish text is stored as itself rather
    than as escapes, and the separators are pinned so no whitespace change can
    alter the digest.
    """
    return json.dumps(
        canonical_context(context),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def context_digest(context: SynthesisContext) -> str:
    """A stable digest of the canonical form.

    SHA-256 over the canonical UTF-8 text. Used for audit correlation, never
    for security: it identifies which context produced a request, and nothing
    about it is a secret.
    """
    return hashlib.sha256(canonical_json(context).encode("utf-8")).hexdigest()

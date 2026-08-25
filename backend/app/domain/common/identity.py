"""Instrument identity — the one rule, in one place.

Phase 3 fixed the project's symbol-equality policy in
`futures.contract.require_matching_quote`:

    Symbols are compared exactly, after stripping surrounding whitespace. **No
    VIOP symbol rule is assumed** - there is no month-code parsing, no root
    extraction, no case folding and no normalisation beyond whitespace, because
    every one of those would be an exchange convention this project has not
    verified. Two symbols match when they are the same string.

That reasoning is not about futures quotes specifically. It is about what this
project actually knows: whether Borsa İstanbul treats a symbol and its
lower-cased spelling as one instrument is an **exchange convention nobody here
has verified**. Case-folding them together is a guess that happens to look
harmless.

(No real instrument code appears in this module, or anywhere else in `app/` -
a contract code in the source would itself be a remembered exchange fact, which
§118 forbids and `test_no_viop_instrument_name_appears_in_the_source_tree`
enforces.)

Phase 6 initially made that guess. Vision output varies in casing, so
`SymbolAgreement` case-folded before comparing and a case-only difference was
reported as agreement. That is a *second, looser* identity policy living
alongside the first — and the looser one sat exactly where a user is told
whether the chart they uploaded shows the instrument they meant.

So the rule lives here, and both callers use it rather than restating it.

Note the asymmetry this preserves: a case-only difference is a **mismatch to be
surfaced**, not a silent merge. The user is asked; the system does not decide.
"""

from __future__ import annotations


def canonical_symbol(symbol: str) -> str:
    """The comparable form of an instrument symbol.

    Whitespace-stripped and **nothing else**. Every further normalisation
    anyone might reach for - upper-casing, root extraction, month-code parsing
    - encodes an exchange convention this project has not verified.
    """
    return symbol.strip()


def same_instrument(first: str, second: str) -> bool:
    """Whether two symbols name the same instrument, by the canonical rule."""
    return canonical_symbol(first) == canonical_symbol(second)

"""Mechanical guards on what presentation text may say (§2, §19, §25, §109).

Wording is the layer where the project's safety rules are easiest to break by
accident. A domain engine that refuses to produce a probability is undone the
moment a sentence says *"%78 ihtimalle yükselir"*, and nothing in the type
system stops a string from saying that.

So the forbidden shapes are listed here, in Turkish and English, and tests
scan every produced sentence and every educational explanation against them.
The list is deliberately about **claims**, not vocabulary: "risk" is a fine
word, "risksiz" is not.

This module holds no policy of its own - it encodes rules the master spec
already states, so each entry cites the section it comes from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NEGATIONS: tuple[str, ...] = (
    r"değil",
    r"etmez",
    r"edilmez",
    r"gelmez",
    r"vermez",
    r"olmaz",
    r"sayılmaz",
    r"göstermez",
    r"yapmaz",
    r"kalmaz",
    r"\bnot\b",
    r"\bnever\b",
    r"\bno\b",
)
"""Markers that turn a forbidden claim into its own denial.

Turkish negates with verb suffixes - *-mez / -maz* - and with *değil*, so
"garanti" and "garanti etmez" share a stem and a naive keyword scan cannot
tell a promise from its refusal. Without this the scanner would flag the very
sentences that exist to say *"this is not a probability"*, which would either
make the guard useless or push an author to stop writing the caveat. The
second outcome is the dangerous one.
"""

NEGATION_WINDOW = 40
"""How far after a match a negation still counts, in characters.

Wide enough for *"başarı olasılığı değildir"*, narrow enough that a denial two
sentences away cannot excuse a promise made here.
"""


@dataclass(frozen=True, slots=True)
class ForbiddenClaim:
    """One shape of sentence that must never reach a user."""

    pattern: str
    """Case-insensitive regular expression."""

    why: str
    section: str

    def matches(self, text: str) -> bool:
        """True when ``text`` makes this claim rather than denying it.

        A safety net, not a proof: the guarantee comes from the tests that
        scan every produced sentence *and* assert the scanner still catches
        known-bad wording, so the negation allowance cannot quietly gut it.
        """
        for found in re.finditer(self.pattern, text, flags=re.IGNORECASE):
            window = text[found.end() : found.end() + NEGATION_WINDOW]
            if any(re.search(marker, window, flags=re.IGNORECASE) for marker in NEGATIONS):
                continue
            return True
        return False


FORBIDDEN_CLAIMS: tuple[ForbiddenClaim, ...] = (
    ForbiddenClaim(
        r"garanti|guaranteed|kesin kâr|kesin kar\b|risksiz|risk-free",
        "No analysis may promise a certain or risk-free outcome.",
        "§2",
    ),
    ForbiddenClaim(
        r"\bkesinlikle (yüksel|düş|artac|azalac)",
        "A certain prediction of direction is forbidden; use conditional wording.",
        "§2",
    ),
    ForbiddenClaim(
        r"%\s*\d+(\.\d+)?\s*(ihtimal|olasılık|şans)|\d+(\.\d+)?%\s*(chance|probability)",
        "A fabricated probability. Analysis scores are heuristic, never calibrated.",
        "§19",
    ),
    ForbiddenClaim(
        r"(kazanma|başarı)\s*(olasılığı|oranı)|win\s*(rate|probability)",
        "A win rate implies calibration against recorded outcomes, which does not exist.",
        "§19",
    ),
    ForbiddenClaim(
        r"\b(hemen|şimdi)\s*(al|sat)\b|\b(buy|sell)\s*now\b",
        "Presentation describes; it does not instruct. The decision is a later phase.",
        "§25",
    ),
    ForbiddenClaim(
        r"RSI\s*>\s*70\s*=\s*(SAT|SELL)|RSI\s*<\s*30\s*=\s*(AL|BUY)",
        "The exact simplistic rule §7 forbids teaching.",
        "§7",
    ),
    ForbiddenClaim(
        r"\bveri yok.{0,20}\bnötr\b|missing.{0,20}\bneutral\b",
        "Missing data must never be presented as a neutral reading.",
        "§2",
    ),
)


def violations(text: str) -> tuple[ForbiddenClaim, ...]:
    """Every forbidden claim ``text`` matches. Empty is the expected result."""
    return tuple(claim for claim in FORBIDDEN_CLAIMS if claim.matches(text))


def is_safe(text: str) -> bool:
    return not violations(text)

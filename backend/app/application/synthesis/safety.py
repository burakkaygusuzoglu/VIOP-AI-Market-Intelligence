"""Two things a synthesis narrative must never do (§14, §15).

**Invent a number.** Not a price, a level, an indicator reading, a stop, a
target, a size or a P&L. If the deterministic context holds the figure, the
narrative cites it; if it does not, the answer is that it is unavailable. §14
lists the forbidden inventions and CLAUDE.md puts it plainly: the model
interprets and explains, and is never the source of truth for a number.

**Claim a probability.** "83% chance of success", "%70 ihtimalle yükselir",
"win rate 4 in 5". This project has no calibrated empirical system and Phase 7
does not add one. Setup Quality is a heuristic 0-100 and says so in its own
label; Vision confidence describes legibility. Neither is a probability of
anything happening in the market, and a sentence that turns one into a
percentage of profit has made a claim nobody can support.

## How the numeric scan avoids being useless

A naive "no digits in prose" rule fails immediately: timeframes are written
`1D`, `15M`, `5M`, indicator periods are `EMA 20` and `RSI 14`, and evidence
identifiers are `EV-BULL-003`. Those are vocabulary, not market claims.

So the scan works from an allow-list:

* every numeric string the deterministic context actually holds
  (`SynthesisContext.allowed_numeric_strings`);
* the timeframe labels and reference identifiers already in the vocabulary;
* small integers used as counts and ordinals.

Anything else that looks like a market figure is an invention, and the output
is invalid rather than merely flagged. False positives are possible and are the
right side to err on: a narrative can always cite a reference instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# ----------------------------------------------------------------------
# Probability language
# ----------------------------------------------------------------------

_PROBABILITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "percentage chance",
        re.compile(
            r"%\s*\d+(?:[.,]\d+)?\s*(?:chance|probability|odds|ihtimal|olasıl|şans)"
            r"|\d+(?:[.,]\d+)?\s*%\s*(?:chance|probability|odds|ihtimal|olasıl|şans)",
            re.IGNORECASE,
        ),
    ),
    (
        "chance of an outcome",
        re.compile(
            r"(?:chance|probability|odds|likelihood)\s+(?:of|that|for)\s+"
            r"(?:success|profit|winning|a win|it works|the trade)",
            re.IGNORECASE,
        ),
    ),
    (
        "turkish probability of an outcome",
        re.compile(
            r"(?:kazanma|kâr|kar|başarı)\s+(?:olasılığı|ihtimali|şansı)",
            re.IGNORECASE,
        ),
    ),
    (
        "win rate",
        re.compile(r"win\s*rate|kazanma\s*oranı|hit\s*rate|success\s*rate", re.IGNORECASE),
    ),
    (
        "quality or confidence as probability",
        re.compile(
            r"(?:setup\s+quality|quality\s+score|confidence|güven)\s+"
            r"(?:is|=|means|of)?\s*\d+(?:[.,]\d+)?\s*%?\s*"
            r"(?:probability|chance|olasılık|ihtimal)",
            re.IGNORECASE,
        ),
    ),
    (
        "expected win percentage",
        re.compile(
            r"(?:expect|expected|beklenen)\s+(?:to\s+win|win|kazanç)\s*\d+(?:[.,]\d+)?\s*%",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class SafetyViolation:
    """One thing a narrative said that it may not say."""

    rule: str
    detail: str
    excerpt: str


def _excerpt(text: str, start: int, end: int, width: int = 40) -> str:
    left = max(0, start - width)
    right = min(len(text), end + width)
    return text[left:right].strip()


def probability_violations(text: str) -> tuple[SafetyViolation, ...]:
    """Every probability-shaped claim in ``text``."""
    found: list[SafetyViolation] = []
    for rule, pattern in _PROBABILITY_PATTERNS:
        for match in pattern.finditer(text):
            found.append(
                SafetyViolation(
                    rule=rule,
                    detail=(
                        "this project has no calibrated probability model; setup quality is a "
                        "heuristic score and vision confidence describes legibility"
                    ),
                    excerpt=_excerpt(text, match.start(), match.end()),
                )
            )
    return tuple(found)


# ----------------------------------------------------------------------
# Invented numeric claims
# ----------------------------------------------------------------------

_NUMBER = re.compile(r"(?<![\w.-])(\d+(?:[.,]\d+)?)(?![\w]*-)")
"""Number-like tokens, ignoring those glued to a word or inside an identifier.

The trailing guard keeps `EV-BULL-003` from contributing `003`, and the leading
guard keeps the `20` of `EMA20` and the `5` of `5M` out - those are read as
vocabulary by `_VOCABULARY` below rather than as figures.
"""

_VOCABULARY = re.compile(
    r"\{\{\s*(?:FACT|VIS)-[A-Z0-9_-]+\s*\}\}"
    r"|\b(?:1D|4H|1H|30M|15M|5M|1M|D1|H4|H1|M30|M15|M5|M1)\b"
    r"|\b(?:FACT|VIS|EV|CON|SQ|EQ|RISK|SUIT|MISS|SCEN)-[A-Z0-9_-]+\b",
    re.IGNORECASE,
)
"""Tokens that contain digits but are not market figures.

Timeframe labels, reference identifiers and rendered placeholders. Removed
before numbers are extracted, so the scan never has to decide whether `15M` is
a price.

**Indicator periods are deliberately no longer exempt.** An earlier version
allowed `RSI\\s*\\d{1,3}` so that "RSI 14" would not be flagged as an invented
figure. That exemption also swallowed `RSI 99` - a *reading*, and precisely the
number this scan exists to catch. Lexically the two are indistinguishable, so
the exemption was removed rather than made cleverer: a narrative that needs to
name a period can spell it in words, and a narrative that needs a reading has
`{{FACT-…}}`. Structural references are the authority; this scan is the net
behind them.
"""

_SMALL_COUNT_LIMIT = 10
"""Integers at or below this are treated as counts or ordinals.

"three of the four timeframes", "the second requirement". A market figure that
happens to be a small integer - a contract count, say - is in the context's
numeric facts anyway, so the allowance costs nothing.
"""


def _normalise_number(token: str) -> str | None:
    """The canonical decimal text for a token, or ``None`` if it is not one."""
    try:
        parsed = Decimal(token.replace(",", "."))
    except InvalidOperation:
        return None
    if not parsed.is_finite():
        return None
    return format(parsed.normalize(), "f")


def numeric_claims(text: str) -> tuple[str, ...]:
    """Every number-like token in ``text``, canonicalised, vocabulary removed."""
    stripped = _VOCABULARY.sub(" ", text)
    found: list[str] = []
    for match in _NUMBER.finditer(stripped):
        normalised = _normalise_number(match.group(1))
        if normalised is not None:
            found.append(normalised)
    return tuple(found)


def unsupported_numeric_violations(
    text: str, allowed: frozenset[str]
) -> tuple[SafetyViolation, ...]:
    """Numbers in ``text`` that the deterministic context does not hold.

    ``allowed`` comes from `SynthesisContext.allowed_numeric_strings`, so the
    only figures a narrative may state are ones an engine produced.
    """
    found: list[SafetyViolation] = []
    for claim in numeric_claims(text):
        if claim in allowed:
            continue
        try:
            value = Decimal(claim)
        except InvalidOperation:  # pragma: no cover - numeric_claims guarantees parseability
            continue
        if value == value.to_integral_value() and abs(value) <= _SMALL_COUNT_LIMIT:
            continue
        found.append(
            SafetyViolation(
                rule="unsupported numeric claim",
                detail=(
                    f"{claim} is not a figure any deterministic engine produced; "
                    "reference an existing fact or say it is unavailable"
                ),
                excerpt=_excerpt(text, text.find(claim.split(".")[0]), len(text)),
            )
        )
    return tuple(found)

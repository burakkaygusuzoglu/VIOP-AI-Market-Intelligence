"""Beginner statements, and the traceability rule that keeps them honest.

The Level 1 layer says things like *"Trend alıcıları destekliyor."* rather than
*"EMA20 > EMA50 > EMA200"*. That simplification is only safe if every sentence
can be walked back to the deterministic observation that produced it, so a
`Statement` **carries its evidence**. Nothing constructs a sentence from
nothing: a statement with no evidence is rejected at construction unless its
topic is explicitly a data gap, which is the one case where the absence *is*
the fact being reported.

This is what stops the beginner layer from drifting into invention. It cannot
mention a number the engines did not compute, because it does not hold one -
it holds the evidence items, and a test asserts every non-gap statement has at
least one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, unique

from app.domain.analysis.evidence import EvidenceItem
from app.domain.analysis.timeframes import TimeframeRole
from app.domain.common.enums import Timeframe


@unique
class StatementTopic(StrEnum):
    """What a sentence is about, so a surface can group or order them."""

    TREND = "TREND"
    MOMENTUM = "MOMENTUM"
    TREND_STRENGTH = "TREND_STRENGTH"
    STRUCTURE = "STRUCTURE"
    REGIME = "REGIME"
    LEVEL = "LEVEL"
    TIMEFRAME_AGREEMENT = "TIMEFRAME_AGREEMENT"
    PULLBACK = "PULLBACK"
    CONTRADICTION = "CONTRADICTION"
    SETUP = "SETUP"
    ENTRY = "ENTRY"
    CAUTION = "CAUTION"

    DATA_GAP = "DATA_GAP"
    """Something could not be measured. The only topic allowed to carry no
    evidence, because the missing measurement is the point."""


@unique
class StatementTone(StrEnum):
    """How a surface should present the sentence. Not a recommendation.

    `CAUTION` marks something the reader must not miss - §109 requires risk
    information to stay visible in both modes - and is deliberately not a
    synonym for "bearish".
    """

    NEUTRAL = "NEUTRAL"
    SUPPORTIVE = "SUPPORTIVE"
    OPPOSING = "OPPOSING"
    CAUTION = "CAUTION"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class Statement:
    """One plain-Turkish sentence, with the facts it came from."""

    topic: StatementTopic
    tone: StatementTone
    text: str
    evidence: tuple[EvidenceItem, ...] = ()
    timeframe: Timeframe | None = None
    role: TimeframeRole | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("a statement must say something")
        if not self.evidence and self.topic is not StatementTopic.DATA_GAP:
            raise ValueError(
                f"statement about {self.topic.value} carries no evidence; only "
                "DATA_GAP may be stated without a deterministic source"
            )

    @property
    def is_traceable(self) -> bool:
        """True when a reader can follow the sentence back to an observation.

        A data gap is traceable in the other direction: it reports that no
        observation exists.
        """
        return bool(self.evidence) or self.topic is StatementTopic.DATA_GAP


@dataclass(frozen=True, slots=True)
class SimpleExplanation:
    """Level 1 of §8: what is happening, in plain Turkish.

    Holds no score, no action and no probability. It is a list of sentences
    and the evidence behind them.
    """

    statements: tuple[Statement, ...] = field(default_factory=tuple)

    def of_topic(self, topic: StatementTopic) -> tuple[Statement, ...]:
        return tuple(item for item in self.statements if item.topic is topic)

    @property
    def cautions(self) -> tuple[Statement, ...]:
        return tuple(item for item in self.statements if item.tone is StatementTone.CAUTION)

    @property
    def data_gaps(self) -> tuple[Statement, ...]:
        return self.of_topic(StatementTopic.DATA_GAP)

    @property
    def cited_evidence(self) -> tuple[EvidenceItem, ...]:
        """Every evidence item any sentence rests on, without duplicates.

        The join between the two layers: §8 requires both to describe the same
        analysis, and a test asserts this set is a subset of what the Pro layer
        reports.
        """
        seen: list[EvidenceItem] = []
        for statement in self.statements:
            for item in statement.evidence:
                if item not in seen:
                    seen.append(item)
        return tuple(seen)

    @property
    def texts(self) -> tuple[str, ...]:
        return tuple(item.text for item in self.statements)

"""Level 1: the simple Turkish explanation (master spec §5, §8).

§5's instruction is concrete. Instead of

    EMA20 > EMA50 > EMA200 · RSI 61.3 · ADX 28

say

    "Trend alıcıları destekliyor."
    "Momentum pozitif."
    "Trend gücü orta."

Every sentence below is a **translation of a fused evidence group**, never a
fresh reading of the market. The builder holds no indicator series, computes
nothing, and cannot mention a value the engines did not produce - it carries
the evidence items and lets `Statement` enforce that each sentence has one.

Three rules shape the wording.

**Simplify without softening.** A range is reported as a range, a contradiction
as a contradiction. §2 forbids manufacturing confidence, and a beginner is the
reader most likely to be harmed by it.

**Missing stays missing.** An unavailable group becomes a sentence saying so.
It never becomes silence, and it never becomes "nötr" - a gap in the data is
not a balanced market.

**No action, ever.** Nothing here says al, sat, gir, çık or bekle. §25's
decision belongs to a later phase; this layer describes.
"""

from __future__ import annotations

from app.application.presentation.statements import (
    SimpleExplanation,
    Statement,
    StatementTone,
    StatementTopic,
)
from app.application.presentation.terms import TermKey, label
from app.domain.analysis.contradictions import ContradictionSeverity
from app.domain.analysis.engine import MultiTimeframeAnalysis
from app.domain.analysis.evidence import EvidenceCategory, EvidenceDirection, EvidenceStrength
from app.domain.analysis.fusion import EvidenceGroup
from app.domain.analysis.quality import SetupQuality
from app.domain.analysis.scenarios import ScenarioState
from app.domain.analysis.timeframes import TimeframeRole

_STRENGTH_WORD: dict[EvidenceStrength, str] = {
    EvidenceStrength.WEAK: "düşük",
    EvidenceStrength.MODERATE: "orta",
    EvidenceStrength.STRONG: "yüksek",
}

_ROLE_WORD: dict[TimeframeRole, str] = {
    TimeframeRole.REGIME: "genel görünüm",
    TimeframeRole.BIAS: "ana yön",
    TimeframeRole.SETUP: "kurulum",
    TimeframeRole.ENTRY: "giriş zamanlaması",
}


def build_simple_explanation(analysis: MultiTimeframeAnalysis) -> SimpleExplanation:
    """Describe ``analysis`` in plain Turkish, sentence by sentence.

    Deterministic in content and order: the builders run in a fixed sequence
    and each reads the fused groups in the order fusion emitted them.
    """
    statements: list[Statement] = []
    statements.extend(_trend(analysis))
    statements.extend(_momentum(analysis))
    statements.extend(_structure(analysis))
    statements.extend(_regime(analysis))
    statements.extend(_levels(analysis))
    statements.extend(_timeframes(analysis))
    statements.extend(_contradictions(analysis))
    statements.extend(_setup(analysis))
    statements.extend(_gaps(analysis))
    return SimpleExplanation(statements=tuple(statements))


def _bias_group(
    analysis: MultiTimeframeAnalysis, category: EvidenceCategory
) -> EvidenceGroup | None:
    """The group a beginner sentence should speak for.

    Prefers the directional-bias timeframe, because §10 makes 1H the primary
    directional read, and falls back to the broadest role available so a
    partial analysis still says something true.
    """
    for role in (TimeframeRole.BIAS, TimeframeRole.REGIME, TimeframeRole.SETUP):
        group = analysis.fused.group_for(role, category)
        if group is not None:
            return group
    return None


def _on(group: EvidenceGroup) -> str:
    """The "1H'de" prefix that says which timeframe a sentence describes.

    Not decoration. A sentence reading *"Trend satıcıları destekliyor"* while
    the daily chart is bullish is true of one timeframe and misleading as a
    summary, and a beginner is exactly the reader who will take it as the
    whole picture. Naming the timeframe costs three characters and removes the
    ambiguity; the agreement and contradiction sentences then say how the
    timeframes relate.
    """
    timeframe = group.items[0].timeframe
    return f"{timeframe.value}'de " if timeframe is not None else ""


def _tone(direction: EvidenceDirection) -> StatementTone:
    if direction is EvidenceDirection.BULLISH:
        return StatementTone.SUPPORTIVE
    if direction is EvidenceDirection.BEARISH:
        return StatementTone.OPPOSING
    if direction is EvidenceDirection.UNAVAILABLE:
        return StatementTone.UNAVAILABLE
    return StatementTone.NEUTRAL


def _trend(analysis: MultiTimeframeAnalysis) -> tuple[Statement, ...]:
    """§5's worked example, in both halves: direction and strength."""
    group = _bias_group(analysis, EvidenceCategory.TREND)
    if group is None:
        return ()

    where = _on(group)
    if group.direction is EvidenceDirection.BULLISH:
        text = f"{where}trend alıcıları destekliyor."
    elif group.direction is EvidenceDirection.BEARISH:
        text = f"{where}trend satıcıları destekliyor."
    elif group.direction is EvidenceDirection.NEUTRAL:
        text = f"{where}trend şu an net bir taraf göstermiyor."
    else:
        text = f"{where}trend hesaplanamıyor: yeterli veri yok."

    found = [
        Statement(
            topic=StatementTopic.TREND,
            tone=_tone(group.direction),
            text=text,
            evidence=group.items,
            timeframe=group.items[0].timeframe,
            role=group.role,
        )
    ]

    if group.strength is not None:
        found.append(
            Statement(
                topic=StatementTopic.TREND_STRENGTH,
                tone=StatementTone.NEUTRAL,
                text=f"{where}trend gücü {_STRENGTH_WORD[group.strength]}.",
                evidence=group.items,
                timeframe=group.items[0].timeframe,
                role=group.role,
            )
        )
    return tuple(found)


def _momentum(analysis: MultiTimeframeAnalysis) -> tuple[Statement, ...]:
    group = _bias_group(analysis, EvidenceCategory.MOMENTUM)
    if group is None:
        return ()

    where = _on(group)
    if group.direction is EvidenceDirection.BULLISH:
        text = f"{where}momentum pozitif."
    elif group.direction is EvidenceDirection.BEARISH:
        text = f"{where}momentum negatif."
    elif group.direction is EvidenceDirection.NEUTRAL:
        text = f"{where}momentum yatay."
    else:
        text = f"{where}momentum hesaplanamıyor: göstergeler henüz ısınma aşamasında."

    return (
        Statement(
            topic=StatementTopic.MOMENTUM,
            tone=_tone(group.direction),
            text=text,
            evidence=group.items,
            timeframe=group.items[0].timeframe,
            role=group.role,
        ),
    )


def _structure(analysis: MultiTimeframeAnalysis) -> tuple[Statement, ...]:
    group = _bias_group(analysis, EvidenceCategory.STRUCTURE)
    if group is None:
        return ()

    name = label(TermKey.MARKET_STRUCTURE).lower()
    where = _on(group)
    if group.direction is EvidenceDirection.BULLISH:
        text = f"{where}{name} yükseliş yönlü: tepeler ve dipler yukarı taşınıyor."
    elif group.direction is EvidenceDirection.BEARISH:
        text = f"{where}{name} düşüş yönlü: tepeler ve dipler aşağı taşınıyor."
    elif group.direction is EvidenceDirection.NEUTRAL:
        text = f"{where}{name} net bir yön göstermiyor."
    else:
        text = f"{where}{name} için yeterli sayıda onaylanmış tepe ve dip yok."

    return (
        Statement(
            topic=StatementTopic.STRUCTURE,
            tone=_tone(group.direction),
            text=text,
            evidence=group.items,
            timeframe=group.items[0].timeframe,
            role=group.role,
        ),
    )


def _regime(analysis: MultiTimeframeAnalysis) -> tuple[Statement, ...]:
    group = _bias_group(analysis, EvidenceCategory.REGIME)
    if group is None:
        return ()

    name = label(TermKey.MARKET_REGIME).lower()
    where = _on(group)
    if group.direction is EvidenceDirection.BULLISH:
        text = f"{where}{name} yukarı yönlü."
    elif group.direction is EvidenceDirection.BEARISH:
        text = f"{where}{name} aşağı yönlü."
    else:
        text = f"{where}{name} bir tarafa yönelmiş değil."

    return (
        Statement(
            topic=StatementTopic.REGIME,
            tone=_tone(group.direction),
            text=text,
            evidence=group.items,
            timeframe=group.items[0].timeframe,
            role=group.role,
        ),
    )


def _levels(analysis: MultiTimeframeAnalysis) -> tuple[Statement, ...]:
    """Nearby zones, named as areas rather than prices.

    No number is quoted here on purpose: §13 treats a zone as a band, and a
    beginner shown a single price reads it as a guarantee. The exact
    boundaries are in the Pro layer.
    """
    found: list[Statement] = []
    for group in analysis.fused.groups:
        if group.category is not EvidenceCategory.LEVEL or not group.is_directional:
            continue
        where = _on(group)
        if group.direction is EvidenceDirection.BEARISH:
            text = (
                f"{where}fiyat önemli bir {label(TermKey.RESISTANCE).lower()} bölgesine "
                "yakın; yükseliş burada zorlanabilir."
            )
        else:
            text = (
                f"{where}fiyat önemli bir {label(TermKey.SUPPORT).lower()} bölgesine "
                "yakın; düşüş burada yavaşlayabilir."
            )
        found.append(
            Statement(
                topic=StatementTopic.LEVEL,
                tone=StatementTone.NEUTRAL,
                text=text,
                evidence=group.items,
                timeframe=group.items[0].timeframe,
                role=group.role,
            )
        )
    return tuple(found)


def _timeframes(analysis: MultiTimeframeAnalysis) -> tuple[Statement, ...]:
    """Whether the timeframes agree, and the pullback case §10 cares about."""
    found: list[Statement] = []
    readings = [item for item in analysis.contradictions.readings if item.is_directional]

    if len(readings) >= 2:
        directions = {item.direction for item in readings}
        evidence = tuple(item for reading in readings for item in reading.evidence)
        if len(directions) == 1:
            text = "Zaman dilimleri aynı yönü gösteriyor."
            tone = StatementTone.SUPPORTIVE
        else:
            text = "Zaman dilimleri farklı yönleri gösteriyor."
            tone = StatementTone.CAUTION
        found.append(
            Statement(
                topic=StatementTopic.TIMEFRAME_AGREEMENT,
                tone=tone,
                text=text,
                evidence=evidence,
            )
        )

    for pullback in analysis.contradictions.pullbacks:
        reading = analysis.contradictions.reading_for(pullback.role)
        if reading is None:
            continue
        found.append(
            Statement(
                topic=StatementTopic.PULLBACK,
                tone=StatementTone.NEUTRAL,
                text=(
                    f"{pullback.timeframe.value} kısa vadede ters yönde hareket ediyor. "
                    "Üst zaman dilimleri aynı yönde olduğu için bu bir "
                    f"{label(TermKey.PULLBACK).lower()} olarak değerlendiriliyor; "
                    "ana yönün değiştiği anlamına gelmez."
                ),
                evidence=reading.evidence,
                timeframe=pullback.timeframe,
                role=pullback.role,
            )
        )
    return tuple(found)


def _contradictions(analysis: MultiTimeframeAnalysis) -> tuple[Statement, ...]:
    """Conflicts, surfaced rather than smoothed over.

    Only the most severe is turned into a sentence: §17 requires the conflict
    to be visible, and repeating one logical disagreement in several sentences
    would make it look like several problems.
    """
    conflicts = analysis.contradictions.contradictions
    if not conflicts:
        return ()

    worst = max(conflicts, key=lambda item: item.severity.rank)
    if worst.severity is ContradictionSeverity.MAJOR:
        text = (
            "Üst zaman dilimleri birbiriyle çelişiyor. Bu durumda alt zaman "
            "dilimindeki hiçbir sinyal bu çelişkiyi çözmez."
        )
    elif worst.severity is ContradictionSeverity.MODERATE:
        text = "Zaman dilimleri arasında dikkate değer bir çelişki var."
    else:
        text = "Kanıtlar arasında küçük çelişkiler var; ikisi de raporlanıyor."

    return (
        Statement(
            topic=StatementTopic.CONTRADICTION,
            tone=StatementTone.CAUTION,
            text=text,
            evidence=worst.evidence,
        ),
    )


def _quality_word(score: int) -> str:
    """A band, not a number. The exact score stays in the Pro layer."""
    if score >= 70:
        return "güçlü"
    if score >= 40:
        return "orta"
    return "zayıf"


def _setup(analysis: MultiTimeframeAnalysis) -> tuple[Statement, ...]:
    """The two directional cases, as coherence bands with the §19 caveat."""
    found: list[Statement] = []
    for scenario, side in (
        (analysis.scenarios.bull, "yükseliş"),
        (analysis.scenarios.bear, "düşüş"),
    ):
        quality: SetupQuality | None = scenario.quality
        if quality is None or scenario.state is ScenarioState.UNAVAILABLE:
            continue
        evidence = tuple(item for group in scenario.supporting for item in group.items)
        if not evidence:
            continue
        found.append(
            Statement(
                topic=StatementTopic.SETUP,
                tone=StatementTone.NEUTRAL,
                text=(
                    f"{side.capitalize()} senaryosunun kanıt tutarlılığı "
                    f"{_quality_word(quality.score)}. Bu bir başarı olasılığı değildir; "
                    "yalnızca eldeki kanıtların birbiriyle ne kadar uyumlu olduğunu gösterir."
                ),
                evidence=evidence,
            )
        )

        if scenario.state is ScenarioState.WAITING_FOR_CONFIRMATION:
            outstanding = scenario.outstanding_requirements
            found.append(
                Statement(
                    topic=StatementTopic.ENTRY,
                    tone=StatementTone.NEUTRAL,
                    text=(
                        f"{side.capitalize()} senaryosu için henüz tamamlanmamış "
                        f"{len(outstanding)} koşul var."
                    ),
                    evidence=evidence,
                )
            )
    return tuple(found)


def _gaps(analysis: MultiTimeframeAnalysis) -> tuple[Statement, ...]:
    """What could not be measured, said out loud.

    The one topic allowed to carry no evidence, because the absence is the
    fact. Silence here would let a partial analysis read as a complete one.
    """
    found: list[Statement] = []
    for role in analysis.views.missing_roles:
        found.append(
            Statement(
                topic=StatementTopic.DATA_GAP,
                tone=StatementTone.UNAVAILABLE,
                text=(
                    f"{analysis.policy.timeframe_for(role).value} verisi yok; "
                    f"{_ROLE_WORD[role]} değerlendirilemedi."
                ),
                role=role,
            )
        )

    unavailable = {
        group.category
        for group in analysis.fused.groups
        if group.direction is EvidenceDirection.UNAVAILABLE
    }
    for category in sorted(unavailable, key=lambda item: item.value):
        found.append(
            Statement(
                topic=StatementTopic.DATA_GAP,
                tone=StatementTone.UNAVAILABLE,
                text=(
                    f"{category.value} ölçümü şu an yapılamıyor. Bu, ölçümün "
                    "sonucunun nötr olduğu anlamına gelmez."
                ),
            )
        )
    return tuple(found)

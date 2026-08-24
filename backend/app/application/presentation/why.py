"""The Why Engine (master spec §92, §57).

§92 ends with the rule the whole module exists to satisfy: **never show an
unexplained score.** Every conclusion this project can currently reach is
explainable here, and the explanation is assembled from the conclusion's own
breakdown rather than written alongside it.

That distinction is what keeps it honest. `why_setup_quality` does not describe
what a setup score *usually* means; it walks the `ComponentScore` list the
scorer produced and reports the points each component actually awarded. If a
component were removed, the explanation would lose a line automatically. There
is no path by which a reason can exist for a number nobody computed.

**Nothing is recomputed.** Risk explanations read finished Phase 3 results,
evidence explanations read fused Phase 4 groups, and no arithmetic beyond
subtracting two supplied breakdowns happens anywhere in this file.

**The Phase 7 boundary is explicit.** §92 also asks *"WHY LONG / SHORT /
WAIT?"*, and those are the three the engine deliberately cannot answer: the
final synthesis that produces them belongs to Phase 7. What can be explained
today is a *supplied* typed conclusion - a scenario state, a pending
confirmation, a blocking suitability finding - and a test asserts no topic here
names a trade action.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Protocol

from app.application.presentation.terms import TermKey, label
from app.domain.analysis.contradictions import Contradiction, ContradictionSeverity
from app.domain.analysis.engine import MultiTimeframeAnalysis
from app.domain.analysis.entry import EntryQuality
from app.domain.analysis.evidence import EvidenceDirection, EvidenceItem
from app.domain.analysis.fusion import EvidenceGroup
from app.domain.analysis.quality import ComponentScore, SetupQuality
from app.domain.analysis.scenarios import Scenario, ScenarioState
from app.domain.analysis.timeframes import TimeframeRole
from app.domain.common.enums import Timeframe
from app.domain.risk.margin import MarginAssessment
from app.domain.risk.sizing import PositionSizing, SizingOutcome
from app.domain.structure.zones import Zone
from app.domain.suitability.no_trade import FindingSeverity, NoTradeAssessment


@unique
class WhyTopic(StrEnum):
    """What is being explained.

    Every member names something the project can already conclude. There is no
    `WHY_LONG`, `WHY_SHORT` or `WHY_WAIT`: §92 asks for them, and Phase 7 owns
    the synthesis that would produce them.
    """

    BULLISH_EVIDENCE = "BULLISH_EVIDENCE"
    BEARISH_EVIDENCE = "BEARISH_EVIDENCE"
    SETUP_QUALITY = "SETUP_QUALITY"
    ENTRY_QUALITY = "ENTRY_QUALITY"
    SCENARIO_STATE = "SCENARIO_STATE"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    CONTRADICTION = "CONTRADICTION"
    SUPPORT_ZONE = "SUPPORT_ZONE"
    RESISTANCE_ZONE = "RESISTANCE_ZONE"
    POSITION_SIZE = "POSITION_SIZE"
    RISK_FINDING = "RISK_FINDING"
    NO_TRADE_BLOCK = "NO_TRADE_BLOCK"
    SCORE_CHANGE = "SCORE_CHANGE"


@unique
class ReasonSource(StrEnum):
    """Which engine the reason came from, so a reader can go and check."""

    EVIDENCE = "EVIDENCE"
    QUALITY_COMPONENT = "QUALITY_COMPONENT"
    ENTRY_COMPONENT = "ENTRY_COMPONENT"
    CONTRADICTION = "CONTRADICTION"
    SCENARIO_REQUIREMENT = "SCENARIO_REQUIREMENT"
    STRUCTURE = "STRUCTURE"
    RISK_ENGINE = "RISK_ENGINE"
    SUITABILITY = "SUITABILITY"
    COMPONENT_DELTA = "COMPONENT_DELTA"


@unique
class ReasonSeverity(StrEnum):
    """How much a reason weighs. Ordinal, never a probability."""

    INFO = "INFO"
    NOTABLE = "NOTABLE"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class Reason:
    """One traceable cause, phrased for both audiences.

    `beginner` and `pro` are two renderings of **one** fact, produced together
    from the same source object. They cannot contradict each other because
    neither is derived from the other - both are derived from the breakdown.
    """

    code: str
    """A stable identifier - a component name, a reason code, an evidence
    source. Machine-readable, and the join back to the engine that produced
    it."""

    source: ReasonSource
    beginner: str
    pro: str
    severity: ReasonSeverity = ReasonSeverity.INFO
    evidence: tuple[EvidenceItem, ...] = ()
    timeframe: Timeframe | None = None
    role: TimeframeRole | None = None

    def __post_init__(self) -> None:
        if not self.beginner.strip() or not self.pro.strip():
            raise ValueError(f"reason {self.code} must be phrased for both audiences")


@dataclass(frozen=True, slots=True)
class Explanation:
    """Why one conclusion holds, or why it cannot be explained."""

    topic: WhyTopic
    subject: str
    """What is being explained, in Turkish - "Yükseliş senaryosu", "Kurulum
    kalitesi 72/100"."""

    reasons: tuple[Reason, ...] = ()
    available: bool = True
    unavailable_reason: str = ""

    def __post_init__(self) -> None:
        if self.available and not self.reasons:
            raise ValueError(
                f"{self.topic.value} claims to be explained but lists no reason; "
                "§92 forbids an unexplained conclusion"
            )
        if not self.available and not self.unavailable_reason.strip():
            raise ValueError(f"{self.topic.value} is unavailable without saying why")

    @property
    def beginner_texts(self) -> tuple[str, ...]:
        return tuple(item.beginner for item in self.reasons)

    @property
    def pro_texts(self) -> tuple[str, ...]:
        return tuple(item.pro for item in self.reasons)

    @property
    def codes(self) -> tuple[str, ...]:
        """The reason codes, identical whichever audience is reading."""
        return tuple(item.code for item in self.reasons)

    @property
    def critical(self) -> tuple[Reason, ...]:
        return tuple(item for item in self.reasons if item.severity is ReasonSeverity.CRITICAL)

    @classmethod
    def unavailable(cls, topic: WhyTopic, subject: str, reason: str) -> Explanation:
        return cls(topic=topic, subject=subject, available=False, unavailable_reason=reason)


# ----------------------------------------------------------------------
# Why bullish / why bearish
# ----------------------------------------------------------------------

_DIRECTION_WORD = {
    EvidenceDirection.BULLISH: "yükseliş",
    EvidenceDirection.BEARISH: "düşüş",
}

_STRENGTH_WORD = {"WEAK": "zayıf", "MODERATE": "orta", "STRONG": "güçlü"}


def why_direction(analysis: MultiTimeframeAnalysis, direction: EvidenceDirection) -> Explanation:
    """Which observations support this side, and how strongly.

    One reason per fused group, so a category that produced twenty records
    argues once here too - the same anti-double-counting the scorer uses.
    """
    if not direction.is_directional:
        raise ValueError(f"a directional case is explained, got {direction.value}")

    topic = (
        WhyTopic.BULLISH_EVIDENCE
        if direction is EvidenceDirection.BULLISH
        else WhyTopic.BEARISH_EVIDENCE
    )
    word = _DIRECTION_WORD[direction]
    groups = analysis.fused.supporting(direction)

    if not groups:
        return Explanation.unavailable(
            topic,
            f"{word.capitalize()} kanıtları",
            f"Hiçbir zaman diliminde {word} yönünde kanıt bulunamadı.",
        )

    reasons = tuple(_group_reason(group, word) for group in groups)
    return Explanation(topic=topic, subject=f"{word.capitalize()} kanıtları", reasons=reasons)


def _group_reason(group: EvidenceGroup, word: str) -> Reason:
    """One fused group, rendered for both audiences."""
    where = group.items[0].timeframe.value if group.items[0].timeframe else ""
    graded = _STRENGTH_WORD.get(group.strength.value, "belirsiz") if group.strength else None
    strength_note = graded if graded else "derecelendirilmedi"

    return Reason(
        code=f"{group.role.value if group.role else 'CONTRACT'}:{group.category.value}",
        source=ReasonSource.EVIDENCE,
        beginner=(
            f"{where} {group.category.value.lower()} {word} yönünü destekliyor ({strength_note})."
        ).strip(),
        pro=(
            f"{group.category.value} @ {group.role.value if group.role else '-'}: "
            f"{group.direction.value}, strength="
            f"{group.strength.value if group.strength else 'None'}, "
            f"reliability={group.reliability.value}, items={group.item_count}"
        ),
        severity=(
            ReasonSeverity.NOTABLE
            if group.strength is not None and group.strength.value == "STRONG"
            else ReasonSeverity.INFO
        ),
        evidence=group.items,
        timeframe=group.items[0].timeframe,
        role=group.role,
    )


# ----------------------------------------------------------------------
# Why this score
# ----------------------------------------------------------------------


class ScoredComponent(Protocol):
    """The shape both quality models' components share.

    Setup and entry components are separate types with separate enums, but
    they answer the same four questions - which component, was it available,
    how many points, and why. A Protocol lets one renderer serve both without
    inventing a shared base class in the domain purely for presentation's
    convenience.
    """

    @property
    def weight(self) -> int: ...

    @property
    def awarded(self) -> int | None: ...

    @property
    def reason(self) -> str: ...

    @property
    def is_available(self) -> bool: ...


def _component_reason(score: ScoredComponent, name: str, source: ReasonSource) -> Reason:
    """One scoring component, rendered for both audiences from one object."""
    if not score.is_available:
        return Reason(
            code=name,
            source=source,
            beginner=f"{_readable(name)} ölçülemedi, bu yüzden puana katılmadı.",
            pro=f"{name}: UNAVAILABLE, weight={score.weight} excluded. {score.reason}",
            severity=ReasonSeverity.NOTABLE,
        )

    awarded = score.awarded or 0
    share = "tamamı" if awarded == score.weight else ("hiç" if awarded == 0 else "bir kısmı")
    return Reason(
        code=name,
        source=source,
        beginner=f"{_readable(name)}: puanın {share} alındı.",
        pro=f"{name}: {awarded}/{score.weight}. {score.reason}",
        severity=ReasonSeverity.INFO if awarded else ReasonSeverity.NOTABLE,
    )


_READABLE: dict[str, str] = {
    "TREND_ALIGNMENT": "Trend uyumu",
    "MARKET_STRUCTURE": "Piyasa yapısı",
    "MOMENTUM": "Momentum",
    "VOLUME": "Hacim",
    "SUPPORT_RESISTANCE": "Destek/direnç",
    "TIMEFRAME_ALIGNMENT": "Zaman dilimi uyumu",
    "TIMEFRAME_COVERAGE": "Zaman dilimi kapsamı",
    "REGIME_SUITABILITY": "Rejim uygunluğu",
    "CONTRADICTION_BURDEN": "Çelişki yükü",
    "DATA_AVAILABILITY": "Veri bütünlüğü",
    "ENTRY_STRUCTURE": "Giriş yapısı",
    "LEVEL_PROXIMITY": "Seviye yakınlığı",
    "BREAKOUT_RETEST": "Kırılım/yeniden test",
    "VOLUME_CONFIRMATION": "Hacim teyidi",
    "MOMENTUM_CONTEXT": "Momentum bağlamı",
    "VWAP_RELATIONSHIP": "VWAP ilişkisi",
    "EXTENSION": "Hareketin uzaması",
}


def _readable(component: str) -> str:
    return _READABLE.get(component, component)


def why_setup_quality(quality: SetupQuality) -> Explanation:
    """The score explained by its own components (§92).

    Reads `quality.components`, so the explanation can never describe a
    component the scorer did not evaluate, and never omit one it did.
    """
    return Explanation(
        topic=WhyTopic.SETUP_QUALITY,
        subject=(
            f"{label(TermKey.SETUP_QUALITY)} {quality.score}/100 "
            f"({quality.direction.value.lower()})"
        ),
        reasons=tuple(
            _component_reason(item, item.component.value, ReasonSource.QUALITY_COMPONENT)
            for item in quality.components
        ),
    )


def why_entry_quality(entry: EntryQuality) -> Explanation:
    """The entry score explained by its own components."""
    if not entry.is_available:
        return Explanation.unavailable(
            WhyTopic.ENTRY_QUALITY,
            label(TermKey.ENTRY_QUALITY),
            entry.reason,
        )
    return Explanation(
        topic=WhyTopic.ENTRY_QUALITY,
        subject=f"{label(TermKey.ENTRY_QUALITY)} {entry.score}/100",
        reasons=tuple(
            _component_reason(item, item.component.value, ReasonSource.ENTRY_COMPONENT)
            for item in entry.components
        ),
    )


# ----------------------------------------------------------------------
# Why this scenario state / what is pending
# ----------------------------------------------------------------------

_STATE_WORD = {
    ScenarioState.UNAVAILABLE: "değerlendirilemiyor",
    ScenarioState.INACTIVE: "geçerli değil",
    ScenarioState.FORMING: "oluşuyor",
    ScenarioState.WAITING_FOR_CONFIRMATION: "teyit bekliyor",
    ScenarioState.CONFIRMED: "teyit edilmiş",
}


def why_scenario_state(scenario: Scenario) -> Explanation:
    """Why the case stands where it does - never what to do about it."""
    subject = f"{scenario.case.value} senaryosu: {_STATE_WORD[scenario.state]}"
    reasons = [
        Reason(
            code=f"STATE:{scenario.state.value}",
            source=ReasonSource.SCENARIO_REQUIREMENT,
            beginner=f"Senaryo durumu: {_STATE_WORD[scenario.state]}.",
            pro=f"state={scenario.state.value}. {scenario.reason}",
        )
    ]
    reasons.extend(
        Reason(
            code=f"REQUIREMENT:{item.code.value}",
            source=ReasonSource.SCENARIO_REQUIREMENT,
            beginner=f"{_requirement_word(item.code.value)}: {_status_word(item.status.value)}.",
            pro=f"{item.code.value}={item.status.value}. {item.reason}",
            severity=(
                ReasonSeverity.NOTABLE if item.status.value != "MET" else ReasonSeverity.INFO
            ),
        )
        for item in scenario.requirements
    )
    return Explanation(topic=WhyTopic.SCENARIO_STATE, subject=subject, reasons=tuple(reasons))


_REQUIREMENT_WORD: dict[str, str] = {
    "BIAS_TIMEFRAME_AGREES": "Ana yön zaman dilimi uyumu",
    "SETUP_TIMEFRAME_AGREES": "Kurulum zaman dilimi uyumu",
    "ENTRY_TIMEFRAME_AGREES": "Giriş zaman dilimi uyumu",
    "NO_MAJOR_CONTRADICTION": "Büyük çelişki yokluğu",
    "STRUCTURE_SUPPORTS": "Yapı desteği",
    "BREAKOUT_OR_RETEST_RESOLVED": "Kırılım/yeniden test sonucu",
}

_STATUS_WORD = {"MET": "sağlandı", "UNMET": "sağlanmadı", "UNKNOWN": "değerlendirilemedi"}


def _requirement_word(code: str) -> str:
    return _REQUIREMENT_WORD.get(code, code)


def _status_word(status: str) -> str:
    return _STATUS_WORD.get(status, status)


def why_pending_confirmation(scenario: Scenario) -> Explanation:
    """What specifically has not happened yet (§55's question).

    Reports the outstanding requirements - unmet *and* unevaluable - because a
    condition nobody could check is not a satisfied one.
    """
    outstanding = scenario.outstanding_requirements
    if not outstanding:
        return Explanation.unavailable(
            WhyTopic.PENDING_CONFIRMATION,
            f"{scenario.case.value} senaryosu",
            "Bekleyen bir teyit koşulu yok.",
        )
    return Explanation(
        topic=WhyTopic.PENDING_CONFIRMATION,
        subject=f"{scenario.case.value} senaryosu için bekleyen koşullar",
        reasons=tuple(
            Reason(
                code=item.code.value,
                source=ReasonSource.SCENARIO_REQUIREMENT,
                beginner=(
                    f"{_requirement_word(item.code.value)} henüz {_status_word(item.status.value)}."
                ),
                pro=f"{item.code.value}={item.status.value}. {item.reason}",
                severity=ReasonSeverity.NOTABLE,
            )
            for item in outstanding
        ),
    )


# ----------------------------------------------------------------------
# Why this contradiction / this zone
# ----------------------------------------------------------------------

_SEVERITY_WORD = {
    ContradictionSeverity.MINOR: "küçük",
    ContradictionSeverity.MODERATE: "dikkate değer",
    ContradictionSeverity.MAJOR: "büyük",
}


def why_contradiction(contradiction: Contradiction) -> Explanation:
    """The conflict, and the evidence on each side of it."""
    frames = ", ".join(item.value for item in contradiction.timeframes)
    reasons = [
        Reason(
            code=contradiction.contradiction_type.value,
            source=ReasonSource.CONTRADICTION,
            beginner=(
                f"{frames} arasında {_SEVERITY_WORD[contradiction.severity]} bir çelişki var."
            ),
            pro=f"{contradiction.contradiction_type.value} "
            f"[{contradiction.severity.value}] {contradiction.reason}",
            severity=(
                ReasonSeverity.CRITICAL
                if contradiction.severity is ContradictionSeverity.MAJOR
                else ReasonSeverity.NOTABLE
            ),
            evidence=contradiction.evidence,
        )
    ]
    for item in contradiction.evidence:
        reasons.append(
            Reason(
                code=f"EVIDENCE:{item.source.value}",
                source=ReasonSource.EVIDENCE,
                beginner=(
                    f"{item.timeframe.value if item.timeframe else ''} "
                    f"{item.category.value.lower()}: {item.direction.value.lower()}."
                ).strip(),
                pro=f"{item.source.value} @ {item.timeframe.value if item.timeframe else '-'}: "
                f"{item.direction.value}/{item.strength.value}. {item.reason}",
                evidence=(item,),
                timeframe=item.timeframe,
                role=item.role,
            )
        )
    return Explanation(
        topic=WhyTopic.CONTRADICTION,
        subject=f"{label(TermKey.CONTRADICTION)}: {contradiction.contradiction_type.value}",
        reasons=tuple(reasons),
    )


def why_zone(zone: Zone) -> Explanation:
    """Why this band is a level: the touches that built it (§92).

    The zone's own score breakdown is reported rather than restated - §13 is
    explicit that the strength is not a probability, and the components say
    what it actually measured.
    """
    is_support = zone.kind.value == "SUPPORT"
    topic = WhyTopic.SUPPORT_ZONE if is_support else WhyTopic.RESISTANCE_ZONE
    name = label(TermKey.SUPPORT if is_support else TermKey.RESISTANCE)

    reasons = (
        Reason(
            code="TOUCHES",
            source=ReasonSource.STRUCTURE,
            beginner=f"Fiyat bu bölgeye {len(zone.touches)} kez tepki verdi.",
            pro=f"touches={len(zone.touches)} at "
            f"{[item.pivot_index for item in zone.touches]}, band={zone.low}-{zone.high}",
            severity=ReasonSeverity.INFO,
        ),
        Reason(
            code="ZONE_SCORE",
            source=ReasonSource.STRUCTURE,
            beginner=(
                "Bölgenin gücü dokunma sayısı, tazelik, tepki büyüklüğü ve hacimden "
                "hesaplanır. Bu bir olasılık değildir."
            ),
            pro=(
                f"strength={zone.strength:.3f} "
                f"(touches={zone.breakdown.touches:.3f}, recency={zone.breakdown.recency:.3f}, "
                f"reaction={zone.breakdown.reaction:.3f}, volume={zone.breakdown.volume:.3f})"
            ),
            severity=ReasonSeverity.INFO,
        ),
    )
    return Explanation(
        topic=topic, subject=f"{name} bölgesi {zone.low}-{zone.high}", reasons=reasons
    )


# ----------------------------------------------------------------------
# Why this risk answer - Phase 3 results, read never recomputed
# ----------------------------------------------------------------------

_OUTCOME_WORD = {
    SizingOutcome.ALLOWED: "izin verildi",
    SizingOutcome.NOT_PERMITTED: "izin verilmedi",
    SizingOutcome.INVALID: "geçersiz",
    SizingOutcome.UNDETERMINED: "belirlenemedi",
}


def why_position_size(sizing: PositionSizing) -> Explanation:
    """The §42 answer, explained from the sizing result's own fields.

    Every figure below is read off `PositionSizing`. Nothing is divided,
    floored or compared here - Phase 3 already did that, and doing it again
    would create a second authority for the same number.
    """
    reasons = [
        Reason(
            code=f"OUTCOME:{sizing.outcome.value}",
            source=ReasonSource.RISK_ENGINE,
            beginner=f"Pozisyon büyüklüğü için {_OUTCOME_WORD[sizing.outcome]}.",
            pro=f"outcome={sizing.outcome.value}. {sizing.reason}",
            severity=(
                ReasonSeverity.CRITICAL
                if sizing.outcome in (SizingOutcome.NOT_PERMITTED, SizingOutcome.INVALID)
                else ReasonSeverity.INFO
            ),
        )
    ]

    if sizing.loss_per_contract is not None and sizing.risk_amount is not None:
        reasons.append(
            Reason(
                code="RISK_BUDGET",
                source=ReasonSource.RISK_ENGINE,
                beginner=(
                    "Tek sözleşmenin zarar kes seviyesine kadarki kaybı, ayrılan risk "
                    "bütçesiyle karşılaştırıldı."
                ),
                pro=(
                    f"risk_amount={sizing.risk_amount}, "
                    f"loss_per_contract={sizing.loss_per_contract}, "
                    f"stop_distance={sizing.stop_distance}, "
                    f"maximum_by_risk={sizing.maximum_by_risk}"
                ),
            )
        )

    reasons.append(
        Reason(
            code=f"MARGIN:{sizing.margin_feasibility.value}",
            source=ReasonSource.RISK_ENGINE,
            beginner=(
                "Teminat durumu biliniyor ve hesaba katıldı."
                if sizing.margin_feasibility.value == "KNOWN"
                else "Teminat durumu doğrulanamadı; bu bir kısıtın karşılandığı anlamına gelmez."
            ),
            pro=(
                f"margin_feasibility={sizing.margin_feasibility.value}, "
                f"maximum_by_margin={sizing.maximum_by_margin}"
            ),
            severity=(
                ReasonSeverity.INFO
                if sizing.margin_feasibility.value == "KNOWN"
                else ReasonSeverity.NOTABLE
            ),
        )
    )
    reasons.append(
        Reason(
            code=f"TICK:{sizing.tick_feasibility.value}",
            source=ReasonSource.RISK_ENGINE,
            beginner=(
                "Giriş ve zarar kes seviyeleri fiyat adımına uygun."
                if sizing.tick_feasibility.value == "ON_GRID"
                else "Giriş/zarar kes seviyelerinin fiyat adımına uygunluğu doğrulanamadı."
            ),
            pro=f"tick_feasibility={sizing.tick_feasibility.value}",
            severity=(
                ReasonSeverity.INFO
                if sizing.tick_feasibility.value == "ON_GRID"
                else ReasonSeverity.NOTABLE
            ),
        )
    )
    return Explanation(
        topic=WhyTopic.POSITION_SIZE,
        subject=f"Pozisyon büyüklüğü: {sizing.allowed_contracts}",
        reasons=tuple(reasons),
    )


def why_risk_findings(assessment: MarginAssessment) -> Explanation:
    """The §44 warnings, each carrying the threshold Phase 3 configured.

    The threshold travels with the warning so a reader sees it is a project
    policy rather than an exchange limit - and so this module never has to
    hold a threshold of its own.
    """
    if not assessment.warnings:
        return Explanation.unavailable(
            WhyTopic.RISK_FINDING,
            "Risk uyarıları",
            "Yapılandırılmış eşiklere göre uyarı üretilmedi.",
        )
    return Explanation(
        topic=WhyTopic.RISK_FINDING,
        subject="Risk uyarıları",
        reasons=tuple(
            Reason(
                code=item.code.value,
                source=ReasonSource.RISK_ENGINE,
                beginner=_risk_beginner_text(item.code.value),
                pro=(
                    f"{item.code.value}: {item.message} "
                    f"(observed={item.observed}, threshold={item.threshold})"
                ),
                severity=ReasonSeverity.CRITICAL,
            )
            for item in assessment.warnings
        ),
    )


_RISK_BEGINNER: dict[str, str] = {
    "HIGH_MARGIN_UTILIZATION": (
        "Hesabın teminatının büyük bölümü kullanılmış durumda; hareket alanı azalıyor."
    ),
    "EXCESSIVE_EFFECTIVE_LEVERAGE": (
        "Pozisyonun kontrol ettiği büyüklük hesaba göre yüksek; küçük fiyat hareketleri "
        "hesapta büyük değişim yaratır."
    ),
    "RISK_LIMIT_EXCEEDED": "Bu işlemin riski, ayarladığınız risk sınırının üzerinde.",
    "MARGIN_UNKNOWN": (
        "Teminat durumu doğrulanamadı. Bu, teminatın yeterli olduğu anlamına gelmez."
    ),
    "MARGIN_DEFICIT": (
        "Kullanılan teminat hesap büyüklüğünü aşmış durumda; hesap teminat açığında."
    ),
}


def _risk_beginner_text(code: str) -> str:
    return _RISK_BEGINNER.get(code, f"Risk uyarısı: {code}")


def why_no_trade_block(assessment: NoTradeAssessment) -> Explanation:
    """Why something blocks - not what to do instead.

    Reports only the BLOCKING findings; pending and caution findings are
    different questions with their own explanations, and folding them together
    is what would erase the WAIT / NO TRADE distinction Phase 7 needs.
    """
    blocking = assessment.blocking
    if not blocking:
        return Explanation.unavailable(
            WhyTopic.NO_TRADE_BLOCK,
            label(TermKey.NO_TRADE),
            "Engelleyici bir bulgu yok.",
        )
    return Explanation(
        topic=WhyTopic.NO_TRADE_BLOCK,
        subject=f"{label(TermKey.NO_TRADE)}: engelleyici bulgular",
        reasons=tuple(
            Reason(
                code=item.reason.value,
                source=ReasonSource.SUITABILITY,
                beginner=_no_trade_beginner_text(item.reason.value),
                pro=f"{item.reason.value} [{item.severity.value}]: {item.detail}",
                severity=ReasonSeverity.CRITICAL,
            )
            for item in blocking
            if item.severity is FindingSeverity.BLOCKING
        ),
    )


_NO_TRADE_BEGINNER: dict[str, str] = {
    "CONFLICTING_TIMEFRAMES": "Zaman dilimleri birbiriyle çelişiyor.",
    "CHAOTIC_REGIME": "Piyasa şu an düzensiz; okunabilir bir yapı yok.",
    "UNCLEAR_STRUCTURE": "Ana yön okunamıyor.",
    "BAD_DATA": "Veri bütünlüğü kontrolünden geçmedi.",
    "POOR_RISK_REWARD": "Risk/ödül oranı ayarlanan alt sınırın altında.",
    "RISK_NOT_PERMITTED": "Hesap bu işleme izin vermiyor.",
    "INSUFFICIENT_DATA": "Değerlendirme için yeterli veri yok.",
}


def _no_trade_beginner_text(code: str) -> str:
    return _NO_TRADE_BEGINNER.get(code, f"Engelleyici bulgu: {code}")


# ----------------------------------------------------------------------
# Why did the score change (§57)
# ----------------------------------------------------------------------


def why_score_changed(before: SetupQuality, after: SetupQuality) -> Explanation:
    """§57's question, answered **only** from the two supplied breakdowns.

    Every reason is a component whose awarded points actually differ between
    the snapshots. Nothing infers a market event: this module never sees
    candles, so it cannot claim price lost VWAP or that a breakout faded - it
    can only report that the component measuring such a thing moved, and name
    it. That restraint is the point. §57's own example lists market events,
    and inventing them from a score delta would be exactly the fabrication §2
    forbids.
    """
    if before.direction is not after.direction:
        raise ValueError(
            "score change compares one direction's case with itself, got "
            f"{before.direction.value} and {after.direction.value}"
        )

    subject = (
        f"{label(TermKey.SETUP_QUALITY)} {before.score} → {after.score} "
        f"({after.direction.value.lower()})"
    )

    reasons: list[Reason] = []

    # A changed scoring model moves the score without any component's awarded
    # points differing, because the denominator moved. Caught by probing: a
    # 72 -> 50 change once produced a single reason while a re-weighted
    # component silently accounted for the rest. An incomplete explanation of
    # a score is what §92 forbids, so the model change is stated first.
    if before.available_weight != after.available_weight:
        reasons.append(
            Reason(
                code="SCORING_MODEL",
                source=ReasonSource.COMPONENT_DELTA,
                beginner=(
                    "Puanlama modeli iki ölçüm arasında değişti; puanlar farklı bir "
                    "toplam üzerinden hesaplandı."
                ),
                pro=(
                    f"available_weight {before.available_weight} → "
                    f"{after.available_weight} "
                    f"(total {before.total_weight} → {after.total_weight}), "
                    f"method {before.method_version} → {after.method_version}"
                ),
                severity=ReasonSeverity.NOTABLE,
            )
        )

    for current in after.components:
        previous = before.component(current.component)
        if previous is None:
            reasons.append(
                Reason(
                    code=f"DELTA:{current.component.value}",
                    source=ReasonSource.COMPONENT_DELTA,
                    beginner=f"{_readable(current.component.value)} yeni bir bileşen.",
                    pro=(
                        f"{current.component.value}: absent in the earlier breakdown, "
                        f"now {current.awarded}/{current.weight}"
                    ),
                    severity=ReasonSeverity.NOTABLE,
                )
            )
            continue
        if previous.awarded == current.awarded and previous.availability is current.availability:
            continue
        reasons.append(_delta_reason(previous, current))

    if not reasons:
        return Explanation.unavailable(
            WhyTopic.SCORE_CHANGE,
            subject,
            (
                "Bileşen dökümleri aynı; verilen iki ölçüm arasında açıklanabilir bir fark yok."
                if before.score == after.score
                else "Toplam puan farklı ancak bileşen dökümlerinde fark bulunamadı."
            ),
        )
    return Explanation(topic=WhyTopic.SCORE_CHANGE, subject=subject, reasons=tuple(reasons))


def _delta_reason(previous: ComponentScore, current: ComponentScore) -> Reason:
    name = _readable(current.component.value)

    if previous.is_available and not current.is_available:
        return Reason(
            code=f"DELTA:{current.component.value}",
            source=ReasonSource.COMPONENT_DELTA,
            beginner=f"{name} artık ölçülemiyor.",
            pro=(
                f"{current.component.value}: {previous.awarded}/{previous.weight} "
                f"→ UNAVAILABLE. {current.reason}"
            ),
            severity=ReasonSeverity.NOTABLE,
        )
    if not previous.is_available and current.is_available:
        return Reason(
            code=f"DELTA:{current.component.value}",
            source=ReasonSource.COMPONENT_DELTA,
            beginner=f"{name} artık ölçülebiliyor.",
            pro=(
                f"{current.component.value}: UNAVAILABLE → "
                f"{current.awarded}/{current.weight}. {current.reason}"
            ),
            severity=ReasonSeverity.NOTABLE,
        )

    was = previous.awarded or 0
    now = current.awarded or 0
    direction = "arttı" if now > was else "azaldı"
    return Reason(
        code=f"DELTA:{current.component.value}",
        source=ReasonSource.COMPONENT_DELTA,
        beginner=f"{name} katkısı {direction}.",
        pro=(
            f"{current.component.value}: {was}/{previous.weight} → "
            f"{now}/{current.weight} ({now - was:+d}). {current.reason}"
        ),
        severity=ReasonSeverity.NOTABLE if now < was else ReasonSeverity.INFO,
    )

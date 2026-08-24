"""Level 2: the full deterministic detail (master spec §6, §8).

Where the beginner layer simplifies, this one **preserves**. Every value is
carried at the precision the engine produced it - a `Decimal` stays a
`Decimal`, a float stays a float, and nothing is rounded or formatted into a
string here. Formatting is the surface's job; losing precision on the way to a
surface is how a Pro view stops being one.

The other half of §6's job is honesty about absence. `TechnicalRow` has an
explicit unavailable state with a reason, so a warm-up period, a missing
timeframe or an unsupplied contract reads as *"ölçülemedi"* rather than as a
blank that a reader fills in themselves.

**Nothing is fabricated.** §6 lists liquidity and strategy configuration among
the things a Pro view might show; this project measures neither, so no row for
them is emitted at all - an "unavailable" row would still imply the field is
part of the analysis. `education.py` explains the concepts and marks them
`NOT_MEASURED`; that is the only place they appear.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum, unique

from app.application.presentation.terms import TermKey, label
from app.domain.analysis.contradictions import Contradiction, DirectionalReading
from app.domain.analysis.engine import MultiTimeframeAnalysis
from app.domain.analysis.entry import EntryQuality
from app.domain.analysis.evidence import EvidenceItem
from app.domain.analysis.fusion import EvidenceGroup
from app.domain.analysis.quality import SetupQuality
from app.domain.analysis.timeframes import TimeframeRole, TimeframeView
from app.domain.common.enums import Timeframe
from app.domain.futures.basis import BasisResult
from app.domain.futures.open_interest import OpenInterestReading
from app.domain.technical.types import IndicatorValues

RowValue = Decimal | float | int | str | None


@unique
class RowAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class TechnicalRow:
    """One named value, at full precision, or an explicit absence."""

    term_key: TermKey | None
    name: str
    value: RowValue
    availability: RowAvailability = RowAvailability.AVAILABLE
    detail: str = ""

    @property
    def is_available(self) -> bool:
        return self.availability is RowAvailability.AVAILABLE

    @classmethod
    def unavailable(cls, name: str, reason: str, term_key: TermKey | None = None) -> TechnicalRow:
        return cls(
            term_key=term_key,
            name=name,
            value=None,
            availability=RowAvailability.UNAVAILABLE,
            detail=reason,
        )


@dataclass(frozen=True, slots=True)
class TimeframeDetail:
    """Everything the Pro layer reports for one timeframe."""

    role: TimeframeRole
    timeframe: Timeframe
    candle_count: int
    last_close: Decimal
    indicators: tuple[TechnicalRow, ...]
    reading: DirectionalReading | None
    groups: tuple[EvidenceGroup, ...]

    def indicator(self, name: str) -> TechnicalRow | None:
        for row in self.indicators:
            if row.name == name:
                return row
        return None


@dataclass(frozen=True, slots=True)
class TechnicalDetail:
    """Level 2 of §8, referencing the same analysis the simple layer read."""

    symbol: str
    timeframes: tuple[TimeframeDetail, ...]
    evidence: tuple[EvidenceItem, ...]
    contract_evidence: tuple[EvidenceItem, ...]
    contradictions: tuple[Contradiction, ...]
    setup_quality: tuple[SetupQuality, ...]
    entry_quality: tuple[EntryQuality, ...]
    futures: tuple[TechnicalRow, ...] = field(default_factory=tuple)
    missing_roles: tuple[TimeframeRole, ...] = field(default_factory=tuple)

    def detail_for(self, role: TimeframeRole) -> TimeframeDetail | None:
        for item in self.timeframes:
            if item.role is role:
                return item
        return None

    @property
    def all_evidence(self) -> tuple[EvidenceItem, ...]:
        return self.evidence + self.contract_evidence


def build_technical_detail(
    analysis: MultiTimeframeAnalysis,
    *,
    basis: BasisResult | None = None,
    open_interest: OpenInterestReading | None = None,
) -> TechnicalDetail:
    """Assemble the full detail view for ``analysis``.

    ``basis`` and ``open_interest`` are the same optional Phase 3 readings the
    analysis itself accepts; supplying them adds rows, omitting them adds
    nothing rather than an empty placeholder.
    """
    return TechnicalDetail(
        symbol=analysis.symbol,
        timeframes=tuple(_timeframe_detail(analysis, view) for view in analysis.views.views),
        evidence=analysis.evidence,
        contract_evidence=analysis.contract_evidence,
        contradictions=analysis.contradictions.contradictions,
        setup_quality=tuple(
            scenario.quality
            for scenario in (analysis.scenarios.bull, analysis.scenarios.bear)
            if scenario.quality is not None
        ),
        entry_quality=tuple(
            scenario.entry
            for scenario in (analysis.scenarios.bull, analysis.scenarios.bear)
            if scenario.entry is not None
        ),
        futures=_futures_rows(basis, open_interest),
        missing_roles=analysis.views.missing_roles,
    )


def _timeframe_detail(analysis: MultiTimeframeAnalysis, view: TimeframeView) -> TimeframeDetail:
    return TimeframeDetail(
        role=view.role,
        timeframe=view.timeframe,
        candle_count=len(view.series),
        last_close=view.last_close,
        indicators=_indicator_rows(view),
        reading=analysis.contradictions.reading_for(view.role),
        groups=analysis.fused.groups_for(view.role),
    )


def _indicator_rows(view: TimeframeView) -> tuple[TechnicalRow, ...]:
    """The last value of every Phase 1 indicator, unrounded.

    Read straight off the finished `TechnicalSnapshot`; nothing is recomputed,
    and a value still in warm-up becomes an unavailable row naming the reason
    rather than a zero.
    """
    index = view.last_index
    technicals = view.technicals
    rows: list[TechnicalRow] = []

    for period in sorted(technicals.ema):
        rows.append(_row(f"EMA{period}", technicals.ema[period], index, TermKey.EMA))
    for period in sorted(technicals.sma):
        rows.append(_row(f"SMA{period}", technicals.sma[period], index, TermKey.SMA))

    rows.append(_row("RSI", technicals.rsi, index, TermKey.RSI))
    rows.append(_row("MACD", technicals.macd.macd, index, TermKey.MACD))
    rows.append(_row("MACD signal", technicals.macd.signal, index, TermKey.MACD))
    rows.append(_row("MACD histogram", technicals.macd.histogram, index, TermKey.MACD))
    rows.append(_row("ATR", technicals.atr, index, TermKey.ATR))
    rows.append(_row("ADX", technicals.directional.adx, index, TermKey.ADX))
    rows.append(_row("+DI", technicals.directional.plus_di, index, TermKey.ADX))
    rows.append(_row("-DI", technicals.directional.minus_di, index, TermKey.ADX))
    rows.append(_row("Bollinger üst", technicals.bollinger.upper, index, TermKey.BOLLINGER_BANDS))
    rows.append(_row("Bollinger orta", technicals.bollinger.middle, index, TermKey.BOLLINGER_BANDS))
    rows.append(_row("Bollinger alt", technicals.bollinger.lower, index, TermKey.BOLLINGER_BANDS))
    rows.append(_row("VWAP", technicals.vwap, index, TermKey.VWAP))
    rows.append(_row("Hacim ortalaması", technicals.volume_ma, index, TermKey.VOLUME))
    rows.append(_row("Göreceli hacim", technicals.relative_volume, index, TermKey.RELATIVE_VOLUME))
    return tuple(rows)


def _row(name: str, values: IndicatorValues, index: int, term_key: TermKey) -> TechnicalRow:
    if index < 0 or index >= len(values) or values[index] is None:
        return TechnicalRow.unavailable(
            name, "gösterge henüz ısınma aşamasında; değer hesaplanamadı", term_key
        )
    return TechnicalRow(term_key=term_key, name=name, value=values[index])


def _futures_rows(
    basis: BasisResult | None, open_interest: OpenInterestReading | None
) -> tuple[TechnicalRow, ...]:
    """Contract context, at the precision Phase 3 produced it.

    Both readings are context rather than direction (§32, §33); the rows carry
    the context value and the engine's own reason, and add no interpretation.
    """
    rows: list[TechnicalRow] = []

    if basis is None:
        rows.append(
            TechnicalRow.unavailable(
                label(TermKey.BASIS), "sözleşme verisi sağlanmadı", TermKey.BASIS
            )
        )
    else:
        rows.append(
            TechnicalRow(
                term_key=TermKey.BASIS,
                name=label(TermKey.BASIS),
                value=basis.basis,
                availability=(
                    RowAvailability.AVAILABLE
                    if basis.basis is not None
                    else RowAvailability.UNAVAILABLE
                ),
                detail=basis.reason,
            )
        )
        rows.append(
            TechnicalRow(
                term_key=TermKey.BASIS,
                name=f"{label(TermKey.BASIS)} oranı",
                value=basis.basis_ratio,
                availability=(
                    RowAvailability.AVAILABLE
                    if basis.basis_ratio is not None
                    else RowAvailability.UNAVAILABLE
                ),
                detail=basis.context.value,
            )
        )

    if open_interest is None:
        rows.append(
            TechnicalRow.unavailable(
                label(TermKey.OPEN_INTEREST),
                "sözleşme verisi sağlanmadı",
                TermKey.OPEN_INTEREST,
            )
        )
    else:
        rows.append(
            TechnicalRow(
                term_key=TermKey.OPEN_INTEREST,
                name=f"{label(TermKey.OPEN_INTEREST)} değişimi",
                value=open_interest.open_interest_change,
                availability=(
                    RowAvailability.AVAILABLE
                    if open_interest.open_interest_change is not None
                    else RowAvailability.UNAVAILABLE
                ),
                detail=open_interest.reason,
            )
        )
    return tuple(rows)

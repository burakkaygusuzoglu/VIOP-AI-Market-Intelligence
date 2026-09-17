"""Cross-timeframe temporal coherence (§2, §3).

Phase 1 and Phase 2 already guarantee causality *within* one dataset: a forming
candle is blocked, out-of-order and duplicate candles are reported, and every
Phase 2 fact carries the index at which it first became knowable. None of that
says anything about whether four datasets describe **the same moment**.

Measured before this module existed: a 1D series running to 2027-02-04 was
accepted alongside 5M data ending 2026-01-01, with no finding of any kind, and
it changed the evidence (11 bullish / 0 bearish became 9 / 3). A year of daily
information the entry timeframe had never seen was folded into one "current"
picture.

## What "known through" means

A candle is not an instant, it is an interval. A 1D bar opening at midnight is
not comparable to a 5M bar opening at 09:00 - the daily bar covers the whole day
that follows. So each timeframe is described by its **coverage end**:

    coverage_end = last candle's open_time + the series interval

Every candle in a validated series is closed (Phase 1 blocks a forming one), so
`coverage_end` is the instant through which that timeframe is genuinely known.

## The rule

    A COARSER TIMEFRAME MAY NEVER BE KNOWN THROUGH A LATER INSTANT THAN A
    FINER ONE.

Directional on purpose, because only one direction is a hazard:

* 5M fresher than 1D is **normal**. Today's daily bar has not closed yet, so it
  is legitimately absent, and the fine data is simply more recent.
* 1D fresher than 5M is **incoherent**. The daily bar covers hours the entry
  timeframe has never seen, and the multi-timeframe engine would combine a later
  regime reading with an earlier entry reading as though they were one moment.

No tolerance is applied. The only way a coarse timeframe outruns a fine one is
that it was given data the fine one does not have, and treating a slice of that
as acceptable would just move the question.

## What happens to an offending timeframe

It is reported and **excluded from the multi-timeframe views**, so it becomes a
missing role rather than a leaking one. Missing is a state every layer already
handles honestly; it never becomes a neutral reading. The finding travels with
the result, so nothing is silently dropped and no timestamp is silently
"fixed" - this module never rewrites a candle.

## No session metadata is invented

There is no Borsa İstanbul session table here. The rule uses only the intervals
the data itself declares, because a session close is a verified exchange fact
this project does not have (§118).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum, unique

from app.domain.analysis.timeframes import TimeframeRole
from app.domain.common.enums import Timeframe
from app.domain.market.series import ValidatedCandleSeries


@unique
class TemporalIssue(StrEnum):
    """Why a set of timeframes does not describe one moment."""

    COARSER_EXTENDS_BEYOND_FINER = "COARSER_EXTENDS_BEYOND_FINER"
    """A broader role is known through a later instant than a narrower one.
    The broader reading would contribute information the narrower one could not
    have had."""

    NO_COVERAGE = "NO_COVERAGE"
    """A timeframe produced no usable series, so it has no coverage end."""


@dataclass(frozen=True, slots=True)
class TemporalFinding:
    """One coherence problem, with both sides named."""

    issue: TemporalIssue
    timeframe: Timeframe
    role: TimeframeRole
    detail: str
    coverage_end: datetime | None = None
    conflicts_with: Timeframe | None = None


@dataclass(frozen=True, slots=True)
class Coverage:
    """How far one timeframe is known."""

    timeframe: Timeframe
    role: TimeframeRole
    coverage_end: datetime
    interval: timedelta
    """This timeframe's bar length. Carried so lag can be stated in the units
    the timeframe is actually measured in - "three bars behind" means something
    at 1H and something else at 1D, where "three hours" does not."""


@dataclass(frozen=True, slots=True)
class Staleness:
    """How far one timeframe lags the combined snapshot.

    The mirror of the exclusion rule above, and the case it deliberately does
    not catch. A *finer* timeframe running ahead of a coarser one is not
    lookahead - fresher entry data cannot inject future information into an
    older regime reading - so it is correctly allowed. But it does mean the
    coarser reading is older than the instant the response is stamped with.

    Measured before this was reported: 1H data ending 2026-03-02 was combined
    with 5M data ending 2026-03-05, the response was stamped
    `analysis_as_of = 2026-03-05`, and nothing anywhere said the 1H trend
    reading was three days old. The analysis was sound; its dating was not.
    """

    timeframe: Timeframe
    role: TimeframeRole
    coverage_end: datetime
    bars_behind: int
    """Whole bars of this timeframe that should have closed by `analysis_as_of`
    and are absent.

    Zero is the normal case and is not a finding: the current bar of a coarser
    timeframe has simply not closed yet, which is why a partial lag is expected
    and why the threshold is a whole bar rather than any lag at all.
    """


@dataclass(frozen=True, slots=True)
class TemporalAssessment:
    """The verdict for one set of timeframes."""

    coverages: tuple[Coverage, ...]
    findings: tuple[TemporalFinding, ...]
    excluded: frozenset[Timeframe]
    """Timeframes that must not contribute to the multi-timeframe analysis."""

    analysis_as_of: datetime | None
    """The instant the combined picture is known through.

    The **maximum coverage end among the timeframes that survived**, which is a
    semantic statement about the market data rather than whichever timestamp
    sorted highest. It is computed from coverage ends, not open times, because
    an open time means different things at different intervals; and it excludes
    incoherent timeframes, so a dataset from the future cannot drag the
    snapshot forward (§3).
    """

    stale: tuple[Staleness, ...] = ()
    """Surviving timeframes that lag `analysis_as_of` by at least one of their
    own bars. Reported, never excluded - the data is coherent, it is just older
    than the snapshot, and a reader must be told which part of the picture is
    which age."""

    @property
    def is_coherent(self) -> bool:
        return not self.findings


def coverage_end(series: ValidatedCandleSeries) -> datetime | None:
    """The instant this series is known through, or ``None`` when it is empty.

    Every candle is closed, so the last one's interval has elapsed.
    """
    if len(series) == 0:
        return None
    return series.open_times[-1] + series.interval


def assess(
    entries: tuple[tuple[Timeframe, TimeframeRole, ValidatedCandleSeries | None], ...],
) -> TemporalAssessment:
    """Check that the supplied timeframes describe one moment."""
    coverages: list[Coverage] = []
    findings: list[TemporalFinding] = []

    for timeframe, role, series in entries:
        if series is None:
            continue
        end = coverage_end(series)
        if end is None:
            continue
        coverages.append(
            Coverage(
                timeframe=timeframe,
                role=role,
                coverage_end=end,
                interval=series.interval,
            )
        )

    # Narrowest first, so each coarser role is compared against every finer one
    # that is actually present.
    by_rank = sorted(coverages, key=lambda item: item.role.rank)
    excluded: set[Timeframe] = set()

    for index, coarser in enumerate(by_rank):
        for finer in by_rank[index + 1 :]:
            if coarser.coverage_end > finer.coverage_end:
                findings.append(
                    TemporalFinding(
                        issue=TemporalIssue.COARSER_EXTENDS_BEYOND_FINER,
                        timeframe=coarser.timeframe,
                        role=coarser.role,
                        coverage_end=coarser.coverage_end,
                        conflicts_with=finer.timeframe,
                        detail=(
                            f"{coarser.timeframe.value} verisi "
                            f"{coarser.coverage_end.isoformat()} anına kadar biliniyor, "
                            f"ancak daha dar {finer.timeframe.value} yalnızca "
                            f"{finer.coverage_end.isoformat()} anına kadar biliniyor. "
                            "Daha geniş zaman dilimi, dar olanın göremediği bir dönemi "
                            "içerdiği için analizden çıkarıldı."
                        ),
                    )
                )
                excluded.add(coarser.timeframe)
                break

    survivors = [item for item in coverages if item.timeframe not in excluded]
    as_of = max((item.coverage_end for item in survivors), default=None)

    stale: list[Staleness] = []
    if as_of is not None:
        for item in survivors:
            bars = (as_of - item.coverage_end) // item.interval
            if bars >= 1:
                stale.append(
                    Staleness(
                        timeframe=item.timeframe,
                        role=item.role,
                        coverage_end=item.coverage_end,
                        bars_behind=int(bars),
                    )
                )

    return TemporalAssessment(
        coverages=tuple(coverages),
        findings=tuple(findings),
        excluded=frozenset(excluded),
        analysis_as_of=as_of,
        stale=tuple(stale),
    )

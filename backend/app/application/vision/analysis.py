"""The screenshot-set use case (§9, §12, §13 of the Phase 6B brief).

One place where a verified upload, a vision pass, the quality score and the
mismatch checks meet - and where the two questions the brief insists on
separating are kept apart:

    OBSERVED SCREEN VALUE   what the picture visibly shows
    CALCULATION AUTHORITY   what a deterministic engine may use

`observed_value` answers the first and is always the screenshot's own reading.
`authority` answers the second by running `precedence.resolve`, which puts
structured market data above everything a screenshot can produce. A user
confirming that the screenshot says 63.2 raises the *screenshot's* authority to
`USER_CONFIRMED`, which still loses to a validated structured 61.27 - that is
the §12 rule, and it falls out of the ranking rather than being special-cased.

**Nothing is averaged and nothing is merged.** §13: four screenshots are four
observations, each keeping its slot and its identity. Cross-screenshot symbol
disagreement is reported, not resolved.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum, unique

from app.application.ports.screenshot import (
    ScreenshotAnalysis,
    ScreenshotAnalyzer,
    ScreenshotContext,
    ScreenshotPayload,
)
from app.application.ports.system import ClockPort
from app.application.vision.intake import AcceptedScreenshot
from app.domain.common.enums import DataSourcePriority, Timeframe
from app.domain.common.identity import canonical_symbol, same_instrument
from app.domain.vision.corrections import CorrectionLog, CorrectionType, FieldCorrection
from app.domain.vision.extraction import ObservedField, VisionExtraction
from app.domain.vision.precedence import (
    ResolvedValue,
    SourcedValue,
    ValueKind,
    resolve,
)
from app.domain.vision.quality import ScreenshotQuality
from app.domain.vision.slots import ScreenshotSlot, SlotAssignment


class UnusableClaimsError(Exception):
    """Every claim about a field existed, and none of it could be used.

    Distinct from `effective_value` returning ``None``, which means nothing was
    observed about the field at all. Collapsing the two would let a screenshot
    that answered "roughly 61 or so" look identical to one that never mentioned
    a price - and a caller deciding whether it has an authoritative figure needs
    to tell those apart. Raised rather than returned because `ResolvedValue`
    has no representation for "no winner": every one of them names an
    authoritative claim.
    """

    def __init__(self, observed: ObservedField, reasons: tuple[str, ...]) -> None:
        self.observed = observed
        self.reasons = reasons
        super().__init__(f"no usable claim about {observed.value}: " + "; ".join(reasons))


@dataclass(frozen=True, slots=True)
class SymbolAgreement:
    """Whether the chart shows the instrument the user expected (§9)."""

    expected: str
    detected: str | None

    @property
    def is_mismatch(self) -> bool:
        """Compared by the project's canonical instrument-identity rule.

        Exact match after stripping whitespace - `domain.common.identity`,
        which is the Phase 3 rule from `require_matching_quote`, not a second
        policy invented here.

        An earlier version case-folded, reasoning that Vision output varies in
        casing and that warning about ASELS versus asels would be noise. That
        was wrong twice over. It put a *looser* identity rule exactly where a
        user is told whether the chart shows the instrument they meant, and it
        assumed an exchange convention - that casing is insignificant on Borsa
        İstanbul - which nobody here has verified. Surfacing a case difference
        asks the user a question; folding it answers one on their behalf.
        """
        if not canonical_symbol(self.expected) or self.detected is None:
            return False
        return not same_instrument(self.expected, self.detected)

    @property
    def is_undetected(self) -> bool:
        return self.detected is None

    @property
    def describe(self) -> str:
        if not self.is_mismatch:
            return ""
        return (
            f"the user expected {self.expected.strip()} but the chart reads "
            f"{(self.detected or '').strip()}"
        )


@dataclass(frozen=True, slots=True)
class ScreenshotReview:
    """One analysed screenshot, with every check that applies to it."""

    accepted: AcceptedScreenshot
    analysis: ScreenshotAnalysis
    slot_assignment: SlotAssignment
    symbol: SymbolAgreement
    corrections: CorrectionLog = field(default_factory=CorrectionLog)

    @property
    def slot(self) -> ScreenshotSlot:
        return self.accepted.asset.slot

    @property
    def extraction(self) -> VisionExtraction:
        return self.analysis.extraction

    @property
    def quality(self) -> ScreenshotQuality:
        return self.analysis.quality

    @property
    def has_mismatch(self) -> bool:
        return self.slot_assignment.is_mismatched or self.symbol.is_mismatch

    @property
    def mismatches(self) -> tuple[str, ...]:
        """Every disagreement, in plain terms, so none is only implicit."""
        found = []
        if self.slot_assignment.is_mismatched:
            found.append(self.slot_assignment.describe_mismatch)
        if self.symbol.is_mismatch:
            found.append(self.symbol.describe)
        return tuple(found)

    def observed_value(self, observed: ObservedField) -> str | None:
        """What the screenshot visibly shows for a field.

        Always the picture's own reading - never the structured value, and
        never a corrected one. "The screenshot shows 63.2" stays answerable
        after a correction, because it is a different question.
        """
        found = self.extraction.of_field(observed)
        return found[0].value if found else None

    def with_correction(self, log: CorrectionLog) -> ScreenshotReview:
        return ScreenshotReview(
            accepted=self.accepted,
            analysis=self.analysis,
            slot_assignment=self.slot_assignment,
            symbol=self.symbol,
            corrections=log,
        )


@dataclass(frozen=True, slots=True)
class ScreenshotSetReview:
    """Every screenshot supplied for one analysis, plus set-level checks."""

    reviews: tuple[ScreenshotReview, ...] = ()

    def for_slot(self, slot: ScreenshotSlot) -> ScreenshotReview | None:
        for review in self.reviews:
            if review.slot is slot:
                return review
        return None

    @property
    def filled_slots(self) -> tuple[ScreenshotSlot, ...]:
        return tuple(review.slot for review in self.reviews)

    @property
    def missing_slots(self) -> tuple[ScreenshotSlot, ...]:
        """Slots nobody uploaded.

        §13: a missing screenshot is allowed and explicit. Nothing here
        pretends four were supplied.
        """
        present = set(self.filled_slots)
        return tuple(slot for slot in ScreenshotSlot if slot not in present)

    @property
    def duplicate_slots(self) -> tuple[ScreenshotSlot, ...]:
        seen = self.filled_slots
        return tuple(sorted({slot for slot in seen if seen.count(slot) > 1}, key=lambda s: s.value))

    @property
    def symbol_disagreements(self) -> tuple[str, ...]:
        """Different tickers read across the set.

        Reported, never resolved: which screenshot is of the wrong instrument
        is a question only the user can answer, and picking one would discard
        the other silently.
        """
        detected = {
            canonical_symbol(review.symbol.detected)
            for review in self.reviews
            if review.symbol.detected and canonical_symbol(review.symbol.detected)
        }
        if len(detected) <= 1:
            return ()
        return tuple(sorted(detected))

    @property
    def timeframe_mismatches(self) -> tuple[ScreenshotSlot, ...]:
        return tuple(review.slot for review in self.reviews if review.slot_assignment.is_mismatched)

    @property
    def has_any_mismatch(self) -> bool:
        return bool(self.timeframe_mismatches or self.symbol_disagreements or self.duplicate_slots)


async def review_screenshot(
    analyzer: ScreenshotAnalyzer,
    accepted: AcceptedScreenshot,
    *,
    expected_symbol: str = "",
    locale: str = "tr",
) -> ScreenshotReview:
    """Analyse one verified screenshot and run every mismatch check.

    ``accepted`` can only come from `accept_and_verify`, so the bytes reaching
    the analyzer have been through a real decode. That is the §A boundary made
    structural rather than remembered.
    """
    payload = ScreenshotPayload.from_accepted(accepted)
    analysis = await analyzer.analyse(
        payload, ScreenshotContext(expected_symbol=expected_symbol, locale=locale)
    )

    detected_timeframe = _detected_timeframe(analysis.extraction)
    assignment = SlotAssignment(slot=accepted.asset.slot)
    if detected_timeframe is not None:
        assignment = assignment.with_detection(detected_timeframe)

    return ScreenshotReview(
        accepted=accepted,
        analysis=analysis,
        slot_assignment=assignment,
        symbol=SymbolAgreement(
            expected=expected_symbol,
            detected=_detected_symbol(analysis.extraction),
        ),
    )


def _detected_timeframe(extraction: VisionExtraction) -> Timeframe | None:
    """The timeframe the model read, if it read one it recognises.

    A value the model reported that is not a supported timeframe is treated as
    *no detection* rather than coerced - guessing that "1 hour" means H1 would
    be exactly the silent rewriting §9 forbids.
    """
    for observation in extraction.of_field(ObservedField.TIMEFRAME):
        if observation.timeframe is not None:
            return observation.timeframe
        try:
            return Timeframe(observation.value.strip().upper())
        except ValueError:
            continue
    return None


def _detected_symbol(extraction: VisionExtraction) -> str | None:
    found = extraction.of_field(ObservedField.SYMBOL)
    return found[0].value.strip() if found else None


def effective_value(
    review: ScreenshotReview,
    observed: ObservedField,
    *,
    structured_value: str | None = None,
    kind: ValueKind = ValueKind.CATEGORICAL,
) -> ResolvedValue | None:
    """Resolve one field across every source that has a claim on it (§12).

    The candidate list is built in weakest-first order and `resolve` ranks it,
    so the outcome follows §1's hierarchy without this function encoding it:

        screenshot reading / visual inference   (weakest)
        user confirmation or correction
        validated structured market data        (strongest)

    ``structured_value`` is the deterministic engine's own figure when one
    exists. Supplying it is what makes a screenshot reading lose - and *not*
    supplying it does not promote the screenshot to market truth; it only
    means no structured claim was offered for this field.
    """
    candidates: list[SourcedValue] = []
    unusable: list[str] = []

    for observation in review.extraction.of_field(observed):
        # A claim from a screenshot or a model is untrusted text. Asked for a
        # price it may answer "roughly 61 or so", which cannot be a numeric
        # candidate - but that is a fact about the reading, not a defect in
        # this code, so it is recorded rather than raised. `SourcedValue`
        # stays strict on purpose: the same text arriving as *structured
        # market data* is a real bug and must still be loud.
        try:
            candidates.append(SourcedValue.from_observation(observation, kind))
        except ValueError as error:
            unusable.append(f"{observation.source_priority.name}: {error}")

    latest = review.corrections.latest_for(observed)
    if latest is not None:
        try:
            claim = latest.as_sourced_value(kind)
        except ValueError as error:
            claim = None
            unusable.append(f"{DataSourcePriority.USER_CONFIRMED.name}: {error}")
        if claim is not None:
            candidates.append(claim)

    if structured_value is not None and structured_value.strip():
        candidates.append(
            SourcedValue(
                field=observed.value,
                value=structured_value,
                source=DataSourcePriority.STRUCTURED_MARKET_DATA,
                kind=kind,
                detail="validated structured market data",
            )
        )

    if not candidates:
        if unusable:
            # Something was said about this field, and none of it was usable.
            # That is a different answer from "nothing was observed", so it
            # must not collapse into the same `None`.
            raise UnusableClaimsError(observed, tuple(unusable))
        return None

    resolved = resolve(tuple(candidates))
    return replace(resolved, unusable=tuple(unusable))


@unique
class Staleness(StrEnum):
    """How old a screenshot is, or an admission that it cannot be told."""

    UNDETERMINED = "STALENESS_UNDETERMINED"
    FRESH = "FRESH"
    STALE = "STALE"


def stale_check(
    observed_at: datetime | None,
    now: datetime | None,
    *,
    max_age: timedelta | None = None,
) -> Staleness:
    """Staleness, or an honest refusal to judge it (§8, §10).

    `UNDETERMINED` unless *all* of a capture instant, a current instant and an
    explicit age policy are available. A screenshot timestamp is rendered in
    the chart's own timezone, which this project does not know, so reading one
    without that context would mean guessing a timezone, a session and an
    exchange clock - three things §8 forbids guessing.

    ``max_age`` has **no default**. There is no specified answer to "how old is
    too old", it varies per timeframe, and inventing a constant here would
    make a policy decision look like a market fact. A caller that has not
    stated one gets `UNDETERMINED` rather than a number this module made up.

    A capture instant *ahead* of ``now`` is `UNDETERMINED`, not `FRESH`. That
    is the whole point: an unread timezone or a skewed clock puts the reading
    in the future, and the failure must not land on the reassuring answer.

    ``now`` comes from a `ClockPort` - see `staleness_of`, which is the only
    intended way to supply it.
    """
    if observed_at is None or now is None:
        return Staleness.UNDETERMINED
    if observed_at.tzinfo is None or now.tzinfo is None:
        return Staleness.UNDETERMINED
    if max_age is None:
        return Staleness.UNDETERMINED

    age = now - observed_at
    if age < timedelta(0):
        return Staleness.UNDETERMINED
    return Staleness.STALE if age > max_age else Staleness.FRESH


def staleness_of(
    review: ScreenshotReview,
    clock: ClockPort,
    *,
    max_age: timedelta | None = None,
) -> Staleness:
    """Staleness of one screenshot, with the current instant from a port (§10).

    The `ClockPort` seam exists so replay and backtest supply their own time.
    An ambient `datetime.now()` would read the host wall clock during a replay
    of a historical session and call every screenshot in it stale.
    """
    return stale_check(review.extraction.observed_at, clock.now(), max_age=max_age)


def record_correction(
    review: ScreenshotReview,
    observed: ObservedField,
    correction_type: CorrectionType,
    clock: ClockPort,
    *,
    corrected_value: str | None = None,
    note: str = "",
) -> ScreenshotReview:
    """Record one user judgement about one observation (§12).

    The timestamp comes from the `ClockPort` rather than from the caller or
    from ambient time, so an audit trail replayed through a historical clock
    carries the instants that replay actually had.

    Returns a new review: `CorrectionLog` is append-only, and the original
    observation stays reachable through the correction that supersedes it.
    """
    found = review.extraction.of_field(observed)
    if not found:
        raise ValueError(
            f"cannot correct {observed.value}: the screenshot carries no such observation"
        )

    correction = FieldCorrection(
        original=found[0],
        correction_type=correction_type,
        corrected_value=corrected_value,
        corrected_at=clock.now(),
        note=note,
    )
    return review.with_correction(review.corrections.with_correction(correction))

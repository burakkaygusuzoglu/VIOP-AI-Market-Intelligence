"""Screenshot Quality 0-100 (master spec §39).

**Claude does not compute this number.** §39 asks for a 0-100 quality score,
and CLAUDE.md is explicit that the LLM is never the source of truth for
arithmetic. The score is deterministic Python over dimensions that are either
measured from the file or reported by a later visual pass - and the scorer
never asks the model for a total.

## Three states, and why the third is not optional

§39 lists nine dimensions. Some are decidable from the upload itself: whether
the chart is large enough is a dimension check. Most are not - whether the
symbol is visible needs someone to look at the picture, which in Phase 6A has
not happened yet.

So every dimension is `PRESENT`, `ABSENT` or `NOT_EVALUATED`, and the third is
the one that keeps the score honest:

* `NOT_EVALUATED` never becomes `PRESENT`. Crediting an unlooked-at dimension
  would inflate the score of every screenshot before anything examined it.
* `NOT_EVALUATED` never becomes `ABSENT` either, unless a policy says so.
  Penalising the same dimension would make every upload look poor for the same
  reason, which is equally uninformative.

Instead it is **excluded from both sides of the fraction**, and the denominator
actually used is published on the result. `evaluated_weight` and `coverage`
say how much of the model was answerable, so a 90 measured on two dimensions
is visibly not the same as a 90 measured on nine.

## Quality is not confidence

`ScreenshotQuality` says how usable the *image* is. `VisionConfidence` (in
`extraction.py`) says how sure the model is about *one field it read*. They
are different measurements of different things, and neither is a probability
of anything happening in the market. A test enforces that they cannot be
assigned to each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, unique

QUALITY_LABEL = "SCREENSHOT QUALITY"
QUALITY_METHOD_VERSION = "screenshot-quality/1"


@unique
class QualityDimension(StrEnum):
    """The nine §39 checks, in the order that section lists them."""

    READABLE = "READABLE"
    SYMBOL_VISIBLE = "SYMBOL_VISIBLE"
    TIMEFRAME_VISIBLE = "TIMEFRAME_VISIBLE"
    PRICE_SCALE_VISIBLE = "PRICE_SCALE_VISIBLE"
    CHART_LARGE_ENOUGH = "CHART_LARGE_ENOUGH"
    TIMESTAMP_VISIBLE = "TIMESTAMP_VISIBLE"
    INDICATORS_READABLE = "INDICATORS_READABLE"
    NOT_CROPPED = "NOT_CROPPED"
    """§39 asks "screenshot cropped?"; stated positively so that `PRESENT` is
    consistently the good outcome for every dimension."""

    NOT_STALE = "NOT_STALE"
    """Likewise for "screenshot stale?"."""


@unique
class DimensionState(StrEnum):
    """Whether a dimension holds, fails, or was never examined."""

    PRESENT = "PRESENT"
    ABSENT = "ABSENT"

    NOT_EVALUATED = "NOT_EVALUATED"
    """Nothing has looked. Excluded from the score rather than counted as
    either outcome - see the module docstring."""


@unique
class UnevaluatedPolicy(StrEnum):
    """What to do with a dimension nobody examined."""

    EXCLUDE = "EXCLUDE"
    """The default. Leaves numerator and denominator alone, and reports the
    reduced coverage."""

    TREAT_AS_ABSENT = "TREAT_AS_ABSENT"
    """Counts it against the score. A legitimate strictness choice, and
    available only because §6 of the brief allows it *with an explicit
    policy* - never as a silent default."""


@dataclass(frozen=True, slots=True)
class QualityWeights:
    """Per-dimension maxima. **Application policy, not a market fact.**

    Configurable, validated, and documented here rather than buried:

    * `READABLE` carries the most because an unreadable chart makes every
      other dimension moot.
    * `SYMBOL_VISIBLE` and `TIMEFRAME_VISIBLE` are next: without them an
      extraction cannot be tied to an instrument or a slot, which is what
      §38's whole precedence model depends on.
    * `NOT_STALE` matters because a screenshot of yesterday's chart can look
      perfect and describe a market that no longer exists.
    """

    readable: int = 22
    symbol_visible: int = 16
    timeframe_visible: int = 16
    price_scale_visible: int = 10
    chart_large_enough: int = 10
    timestamp_visible: int = 8
    indicators_readable: int = 8
    not_cropped: int = 5
    not_stale: int = 5

    def __post_init__(self) -> None:
        for dimension in QualityDimension:
            weight = self.weight_for(dimension)
            if weight < 0:
                raise ValueError(f"{dimension.value} weight must not be negative, got {weight}")
        if self.total == 0:
            raise ValueError("at least one quality dimension must carry weight")

    def weight_for(self, dimension: QualityDimension) -> int:
        return {
            QualityDimension.READABLE: self.readable,
            QualityDimension.SYMBOL_VISIBLE: self.symbol_visible,
            QualityDimension.TIMEFRAME_VISIBLE: self.timeframe_visible,
            QualityDimension.PRICE_SCALE_VISIBLE: self.price_scale_visible,
            QualityDimension.CHART_LARGE_ENOUGH: self.chart_large_enough,
            QualityDimension.TIMESTAMP_VISIBLE: self.timestamp_visible,
            QualityDimension.INDICATORS_READABLE: self.indicators_readable,
            QualityDimension.NOT_CROPPED: self.not_cropped,
            QualityDimension.NOT_STALE: self.not_stale,
        }[dimension]

    @property
    def total(self) -> int:
        return sum(self.weight_for(dimension) for dimension in QualityDimension)


@dataclass(frozen=True, slots=True)
class QualityPolicy:
    """Every scoring setting in one frozen object."""

    weights: QualityWeights = field(default_factory=QualityWeights)
    unevaluated: UnevaluatedPolicy = UnevaluatedPolicy.EXCLUDE

    minimum_coverage: float = 0.25
    """Below this share of evaluated weight no total is produced at all.

    Two dimensions out of nine cannot support a 0-100 number that a reader
    will inevitably compare against a fully-evaluated one. §39 says a poor
    screenshot must not produce false precision; so must a barely-examined
    one, and refusing the number is the only way to say so."""


@dataclass(frozen=True, slots=True)
class DimensionResult:
    """One dimension's state, the points it earned, and why."""

    dimension: QualityDimension
    state: DimensionState
    weight: int
    awarded: int | None
    """``None`` when the dimension was excluded - not zero, which would read
    as measured and failing."""

    reason: str = ""

    @property
    def is_evaluated(self) -> bool:
        return self.state is not DimensionState.NOT_EVALUATED


@dataclass(frozen=True, slots=True)
class ScreenshotQuality:
    """The §39 score, or an explicit refusal to produce one."""

    score: int | None
    """0-100 when calculable. ``None`` when too little was evaluated to make
    a total meaningful - see `QualityPolicy.minimum_coverage`."""

    dimensions: tuple[DimensionResult, ...]
    evaluated_weight: int
    """The denominator actually used. Published so normalisation is never
    something a reader has to reconstruct."""

    total_weight: int
    blocking_issues: tuple[str, ...] = field(default_factory=tuple)
    label: str = QUALITY_LABEL
    method_version: str = QUALITY_METHOD_VERSION

    @property
    def coverage(self) -> float:
        """Share of the model that could be evaluated, 0.0-1.0."""
        return self.evaluated_weight / self.total_weight if self.total_weight else 0.0

    @property
    def is_calculable(self) -> bool:
        return self.score is not None

    @property
    def not_evaluated(self) -> tuple[QualityDimension, ...]:
        return tuple(item.dimension for item in self.dimensions if not item.is_evaluated)

    @property
    def absent(self) -> tuple[QualityDimension, ...]:
        return tuple(
            item.dimension for item in self.dimensions if item.state is DimensionState.ABSENT
        )

    def dimension(self, dimension: QualityDimension) -> DimensionResult | None:
        for item in self.dimensions:
            if item.dimension is dimension:
                return item
        return None


def score_quality(
    states: dict[QualityDimension, DimensionState],
    *,
    policy: QualityPolicy | None = None,
    reasons: dict[QualityDimension, str] | None = None,
    blocking_issues: tuple[str, ...] = (),
) -> ScreenshotQuality:
    """Score the nine §39 dimensions deterministically.

    ``states`` may name any subset; anything omitted is `NOT_EVALUATED`, which
    is the honest default for a dimension nobody was asked about.
    """
    settings = policy if policy is not None else QualityPolicy()
    notes = reasons or {}
    results: list[DimensionResult] = []

    evaluated_weight = 0
    awarded_total = 0

    for dimension in QualityDimension:
        weight = settings.weights.weight_for(dimension)
        state = states.get(dimension, DimensionState.NOT_EVALUATED)

        if state is DimensionState.NOT_EVALUATED:
            if settings.unevaluated is UnevaluatedPolicy.TREAT_AS_ABSENT:
                evaluated_weight += weight
                results.append(
                    DimensionResult(
                        dimension=dimension,
                        state=state,
                        weight=weight,
                        awarded=0,
                        reason=notes.get(
                            dimension,
                            "not evaluated; counted as absent by the configured policy",
                        ),
                    )
                )
                continue
            results.append(
                DimensionResult(
                    dimension=dimension,
                    state=state,
                    weight=weight,
                    awarded=None,
                    reason=notes.get(dimension, "not evaluated; excluded from the score"),
                )
            )
            continue

        earned = weight if state is DimensionState.PRESENT else 0
        evaluated_weight += weight
        awarded_total += earned
        results.append(
            DimensionResult(
                dimension=dimension,
                state=state,
                weight=weight,
                awarded=earned,
                reason=notes.get(dimension, ""),
            )
        )

    total_weight = settings.weights.total
    coverage = evaluated_weight / total_weight if total_weight else 0.0
    score = (
        round(100 * awarded_total / evaluated_weight)
        if evaluated_weight > 0 and coverage >= settings.minimum_coverage
        else None
    )

    return ScreenshotQuality(
        score=score,
        dimensions=tuple(results),
        evaluated_weight=evaluated_weight,
        total_weight=total_weight,
        blocking_issues=tuple(blocking_issues),
    )

"""Screenshot slots (§38) and the quality score (§39).

Every value here is TEST_FIXTURE data.
"""

from __future__ import annotations

import pytest

from app.domain.common.enums import Timeframe
from app.domain.vision.assets import ScreenshotAsset, ScreenshotId, ScreenshotSet
from app.domain.vision.quality import (
    QUALITY_LABEL,
    DimensionState,
    QualityDimension,
    QualityPolicy,
    QualityWeights,
    ScreenshotQuality,
    UnevaluatedPolicy,
    score_quality,
)
from app.domain.vision.slots import (
    SLOTS_BROADEST_FIRST,
    ScreenshotSlot,
    SlotAssignment,
    TimeframeAgreement,
)

ALL_PRESENT = dict.fromkeys(QualityDimension, DimensionState.PRESENT)
ALL_ABSENT = dict.fromkeys(QualityDimension, DimensionState.ABSENT)


def asset(slot: ScreenshotSlot, digest: str = "abc") -> ScreenshotAsset:
    return ScreenshotAsset(
        screenshot_id=ScreenshotId.generate(),
        slot=slot,
        image_format="PNG",
        width=1280,
        height=720,
        byte_size=2048,
        digest=digest,
    )


# ----------------------------------------------------------------------
# The four §38 slots
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("slot", "timeframe"),
    (
        (ScreenshotSlot.D1, Timeframe.D1),
        (ScreenshotSlot.H1, Timeframe.H1),
        (ScreenshotSlot.M15, Timeframe.M15),
        (ScreenshotSlot.M5, Timeframe.M5),
    ),
)
def test_each_slot_maps_to_its_timeframe(slot: ScreenshotSlot, timeframe: Timeframe) -> None:
    assert slot.timeframe is timeframe
    assert ScreenshotSlot.for_timeframe(timeframe) is slot


@pytest.mark.unit
def test_the_four_slots_are_ordered_broadest_first() -> None:
    assert SLOTS_BROADEST_FIRST == (
        ScreenshotSlot.D1,
        ScreenshotSlot.H1,
        ScreenshotSlot.M15,
        ScreenshotSlot.M5,
    )


@pytest.mark.unit
def test_a_timeframe_with_no_slot_has_none() -> None:
    """4H is a supported timeframe with no §38 screenshot slot."""
    assert ScreenshotSlot.for_timeframe(Timeframe.H4) is None


# ----------------------------------------------------------------------
# Expected, detected and confirmed stay separate
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_nothing_detected_is_not_agreement() -> None:
    """Nobody has looked, which is a different answer from "it matches"."""
    assignment = SlotAssignment(slot=ScreenshotSlot.H1)
    assert assignment.agreement is TimeframeAgreement.UNDETECTED
    assert assignment.effective_timeframe is None


@pytest.mark.unit
def test_a_matching_detection_agrees() -> None:
    assignment = SlotAssignment(slot=ScreenshotSlot.H1).with_detection(Timeframe.H1)
    assert assignment.agreement is TimeframeAgreement.AGREES
    assert assignment.effective_timeframe is Timeframe.H1


@pytest.mark.unit
def test_a_1h_chart_in_the_15m_slot_stays_a_mismatch() -> None:
    """The §2 rule: never silently accepted, never silently corrected."""
    assignment = SlotAssignment(slot=ScreenshotSlot.M15).with_detection(Timeframe.H1)
    assert assignment.agreement is TimeframeAgreement.MISMATCH
    assert assignment.is_mismatched
    assert assignment.slot is ScreenshotSlot.M15
    assert assignment.expected is Timeframe.M15
    assert assignment.detected is Timeframe.H1


@pytest.mark.unit
def test_a_mismatch_yields_no_usable_timeframe() -> None:
    """There is deliberately no best guess: a disputed chart has no
    timeframe until the dispute is settled."""
    assignment = SlotAssignment(slot=ScreenshotSlot.M15).with_detection(Timeframe.H1)
    assert assignment.effective_timeframe is None


@pytest.mark.unit
def test_a_mismatch_describes_itself() -> None:
    assignment = SlotAssignment(slot=ScreenshotSlot.M15).with_detection(Timeframe.H1)
    assert "15M" in assignment.describe_mismatch
    assert "1H" in assignment.describe_mismatch
    assert "vision-detected" in assignment.describe_mismatch


@pytest.mark.unit
def test_a_detected_timeframe_with_no_slot_is_its_own_state() -> None:
    """A 4H chart in a four-slot form is real information, and not the same
    kind of problem as a 1H/15M mix-up."""
    assignment = SlotAssignment(slot=ScreenshotSlot.H1).with_detection(Timeframe.H4)
    assert assignment.agreement is TimeframeAgreement.UNSUPPORTED_TIMEFRAME
    assert assignment.is_mismatched


@pytest.mark.unit
def test_a_user_confirmation_outranks_a_detection() -> None:
    """When a human states what the chart is, the model does not overrule it."""
    assignment = (
        SlotAssignment(slot=ScreenshotSlot.H1)
        .with_detection(Timeframe.M15)
        .with_confirmation(Timeframe.H1)
    )
    assert assignment.agreement is TimeframeAgreement.AGREES
    assert assignment.effective_timeframe is Timeframe.H1


@pytest.mark.unit
def test_a_confirmation_never_erases_the_detection() -> None:
    """The disagreement stays inspectable even after it is resolved."""
    assignment = (
        SlotAssignment(slot=ScreenshotSlot.H1)
        .with_detection(Timeframe.M15)
        .with_confirmation(Timeframe.H1)
    )
    assert assignment.detected is Timeframe.M15


@pytest.mark.unit
def test_a_confirmation_can_itself_disagree_with_the_slot() -> None:
    assignment = SlotAssignment(slot=ScreenshotSlot.H1).with_confirmation(Timeframe.M5)
    assert assignment.agreement is TimeframeAgreement.MISMATCH
    assert "user-confirmed" in assignment.describe_mismatch


@pytest.mark.unit
def test_recording_a_detection_never_changes_the_slot() -> None:
    original = SlotAssignment(slot=ScreenshotSlot.D1)
    assert original.with_detection(Timeframe.M5).slot is ScreenshotSlot.D1


# ----------------------------------------------------------------------
# Screenshot sets
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_all_four_slots_can_be_filled() -> None:
    screenshots = ScreenshotSet(assets=tuple(asset(slot) for slot in SLOTS_BROADEST_FIRST))
    assert screenshots.filled_slots == SLOTS_BROADEST_FIRST
    assert screenshots.for_slot(ScreenshotSlot.M5) is not None


@pytest.mark.unit
def test_two_screenshots_for_one_slot_are_refused() -> None:
    """Which one to keep is a question only the user can answer."""
    with pytest.raises(ValueError, match="more than one screenshot"):
        ScreenshotSet(assets=(asset(ScreenshotSlot.H1), asset(ScreenshotSlot.H1)))


@pytest.mark.unit
def test_the_same_image_in_two_slots_is_surfaced_not_blocked() -> None:
    screenshots = ScreenshotSet(
        assets=(
            asset(ScreenshotSlot.H1, digest="same"),
            asset(ScreenshotSlot.M15, digest="same"),
        )
    )
    assert screenshots.duplicate_digests == ("same",)


# ----------------------------------------------------------------------
# Quality scoring
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_an_entirely_good_screenshot_scores_one_hundred() -> None:
    quality = score_quality(ALL_PRESENT)
    assert quality.score == 100
    assert quality.coverage == 1.0
    assert quality.label == QUALITY_LABEL


@pytest.mark.unit
def test_an_entirely_poor_screenshot_scores_zero() -> None:
    quality = score_quality(ALL_ABSENT)
    assert quality.score == 0
    assert quality.coverage == 1.0
    assert len(quality.absent) == len(QualityDimension)


@pytest.mark.unit
def test_an_unevaluated_dimension_is_never_credited() -> None:
    """The rule that keeps the score honest before anything has looked."""
    partial = {QualityDimension.READABLE: DimensionState.PRESENT}
    quality = score_quality(partial)
    unevaluated = quality.dimension(QualityDimension.SYMBOL_VISIBLE)
    assert unevaluated is not None
    assert unevaluated.state is DimensionState.NOT_EVALUATED
    assert unevaluated.awarded is None


@pytest.mark.unit
def test_an_unevaluated_dimension_is_excluded_from_both_sides() -> None:
    """Not credited, and not charged either - just absent from the fraction."""
    partial = {
        QualityDimension.READABLE: DimensionState.PRESENT,
        QualityDimension.SYMBOL_VISIBLE: DimensionState.PRESENT,
        QualityDimension.TIMEFRAME_VISIBLE: DimensionState.ABSENT,
    }
    quality = score_quality(partial)
    weights = QualityWeights()
    expected_denominator = weights.readable + weights.symbol_visible + weights.timeframe_visible
    assert quality.evaluated_weight == expected_denominator
    assert quality.total_weight == weights.total
    assert quality.coverage < 1.0


@pytest.mark.unit
def test_the_denominator_actually_used_is_published() -> None:
    quality = score_quality(ALL_PRESENT)
    assert quality.evaluated_weight == QualityWeights().total
    assert quality.score == 100


@pytest.mark.unit
def test_too_little_evaluated_produces_no_score_at_all() -> None:
    """§39 forbids false precision; a barely-examined screenshot cannot
    support a 0-100 number a reader will compare against a full one."""
    quality = score_quality({QualityDimension.NOT_STALE: DimensionState.PRESENT})
    assert quality.score is None
    assert not quality.is_calculable
    assert quality.coverage < QualityPolicy().minimum_coverage


@pytest.mark.unit
def test_a_strict_policy_can_charge_for_unevaluated_dimensions() -> None:
    """Available only as an explicit policy, never as a silent default."""
    partial = {QualityDimension.READABLE: DimensionState.PRESENT}
    lenient = score_quality(partial)
    strict = score_quality(
        partial, policy=QualityPolicy(unevaluated=UnevaluatedPolicy.TREAT_AS_ABSENT)
    )
    assert lenient.score is None or strict.score is not None
    assert strict.coverage == 1.0
    assert strict.score is not None
    assert strict.score < 100


@pytest.mark.unit
def test_weights_are_configurable() -> None:
    custom = QualityPolicy(weights=QualityWeights(readable=90, symbol_visible=10))
    states = {
        QualityDimension.READABLE: DimensionState.PRESENT,
        QualityDimension.SYMBOL_VISIBLE: DimensionState.ABSENT,
    }
    quality = score_quality(states, policy=custom)
    assert quality.score == 90


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    ({"readable": -1}, dict.fromkeys(QualityWeights.__dataclass_fields__, 0)),
)
def test_invalid_weights_are_rejected(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="must not be negative|at least one"):
        QualityWeights(**kwargs)


@pytest.mark.unit
def test_scoring_is_deterministic() -> None:
    states = {
        QualityDimension.READABLE: DimensionState.PRESENT,
        QualityDimension.SYMBOL_VISIBLE: DimensionState.ABSENT,
        QualityDimension.TIMEFRAME_VISIBLE: DimensionState.PRESENT,
        QualityDimension.PRICE_SCALE_VISIBLE: DimensionState.PRESENT,
    }
    assert score_quality(states) == score_quality(states)


@pytest.mark.unit
def test_every_dimension_is_reported_even_when_unevaluated() -> None:
    quality = score_quality({QualityDimension.READABLE: DimensionState.PRESENT})
    assert {item.dimension for item in quality.dimensions} == set(QualityDimension)
    assert len(quality.not_evaluated) == len(QualityDimension) - 1


@pytest.mark.unit
def test_blocking_issues_are_carried_through() -> None:
    quality = score_quality(ALL_PRESENT, blocking_issues=("the chart is from 2019",))
    assert quality.blocking_issues == ("the chart is from 2019",)


@pytest.mark.unit
def test_the_nine_section_39_dimensions_are_all_present() -> None:
    assert len(QualityDimension) == 9
    for name in ("READABLE", "SYMBOL_VISIBLE", "TIMEFRAME_VISIBLE", "NOT_CROPPED", "NOT_STALE"):
        assert name in {item.value for item in QualityDimension}


@pytest.mark.unit
def test_quality_carries_no_probability_vocabulary() -> None:
    forbidden = {"probability", "confidence", "likelihood", "win_rate", "odds"}
    assert forbidden.isdisjoint(ScreenshotQuality.__dataclass_fields__)


# ----------------------------------------------------------------------
# Polarity: a negative condition must never improve the score
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("dimension", list(QualityDimension))
def test_worsening_any_single_dimension_never_raises_the_score(
    dimension: QualityDimension,
) -> None:
    """The audit the Phase 6 review asked for, over all nine dimensions.

    Two of them - NOT_CROPPED and NOT_STALE - describe conditions that §39
    phrases negatively ("screenshot cropped?", "screenshot stale?"). They are
    named positively here precisely so that PRESENT is the good outcome
    everywhere and no reader has to remember which way round one of them runs.
    This test proves the naming matches the arithmetic.
    """
    good = score_quality(dict(ALL_PRESENT))
    worse = score_quality({**ALL_PRESENT, dimension: DimensionState.ABSENT})

    assert good.score is not None and worse.score is not None
    assert worse.score < good.score, f"{dimension.value}=ABSENT did not lower the score"


@pytest.mark.unit
def test_a_cropped_screenshot_scores_worse_than_an_uncropped_one() -> None:
    """Same input except cropped. Stated explicitly, not only parametrised."""
    uncropped = score_quality({**ALL_PRESENT, QualityDimension.NOT_CROPPED: DimensionState.PRESENT})
    cropped = score_quality({**ALL_PRESENT, QualityDimension.NOT_CROPPED: DimensionState.ABSENT})
    assert cropped.score is not None and uncropped.score is not None
    assert cropped.score < uncropped.score


@pytest.mark.unit
def test_a_stale_screenshot_scores_worse_than_a_fresh_one() -> None:
    fresh = score_quality({**ALL_PRESENT, QualityDimension.NOT_STALE: DimensionState.PRESENT})
    stale = score_quality({**ALL_PRESENT, QualityDimension.NOT_STALE: DimensionState.ABSENT})
    assert stale.score is not None and fresh.score is not None
    assert stale.score < fresh.score


@pytest.mark.unit
@pytest.mark.parametrize(
    "policy",
    (QualityPolicy(), QualityPolicy(unevaluated=UnevaluatedPolicy.TREAT_AS_ABSENT)),
)
@pytest.mark.parametrize("dimension", list(QualityDimension))
def test_present_never_scores_below_absent_under_any_policy(
    policy: QualityPolicy, dimension: QualityDimension
) -> None:
    """Exhaustive polarity check across both unevaluated policies."""
    for other in (DimensionState.PRESENT, DimensionState.ABSENT, DimensionState.NOT_EVALUATED):
        base = {**ALL_PRESENT, QualityDimension.TIMESTAMP_VISIBLE: other}
        present = score_quality({**base, dimension: DimensionState.PRESENT}, policy=policy)
        absent = score_quality({**base, dimension: DimensionState.ABSENT}, policy=policy)
        if present.score is None or absent.score is None:
            continue
        assert absent.score <= present.score, (
            f"{dimension.value}=ABSENT outscored PRESENT under {policy.unevaluated.value}"
        )


@pytest.mark.unit
def test_unevaluated_staleness_is_reported_as_unknown_not_as_fresh() -> None:
    """Staleness needs a clock and a session calendar this project lacks.

    It must therefore stay NOT_EVALUATED - and that must be *visible* as
    reduced coverage rather than quietly scoring like a fresh screenshot.
    """
    unknown = score_quality(
        {**ALL_PRESENT, QualityDimension.NOT_STALE: DimensionState.NOT_EVALUATED}
    )

    assert QualityDimension.NOT_STALE in unknown.not_evaluated
    assert QualityDimension.NOT_STALE not in unknown.absent, (
        "an unknown staleness was recorded as a known fault"
    )
    assert unknown.coverage < 1.0, "an unexamined dimension was counted as examined"
    assert unknown.evaluated_weight < unknown.total_weight


@pytest.mark.unit
def test_an_unexamined_dimension_is_never_counted_as_good() -> None:
    """EXCLUDE leaves it out of both sides; it cannot be a free pass."""
    unknown = score_quality(
        {**ALL_PRESENT, QualityDimension.NOT_CROPPED: DimensionState.NOT_EVALUATED}
    )
    known_bad = score_quality({**ALL_PRESENT, QualityDimension.NOT_CROPPED: DimensionState.ABSENT})

    assert QualityDimension.NOT_CROPPED in unknown.not_evaluated
    assert QualityDimension.NOT_CROPPED not in unknown.absent
    assert known_bad.score is not None and unknown.score is not None
    assert known_bad.score < unknown.score, "a known fault must cost more than an unknown one"
    assert unknown.coverage < 1.0

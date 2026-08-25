"""Extraction schemas, confidence, and source precedence (§38, §65, §68).

The §38 worked example is the test this file exists for: structured RSI 61.27
beats a screenshot reading of 63.2, and the screenshot reading survives as a
recorded conflict.

Every value here is TEST_FIXTURE data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.application.vision.schemas import (
    ExtractedValueSchema,
    VisionExtractionSchema,
)
from app.domain.common.enums import DataSourcePriority, Timeframe
from app.domain.vision.assets import ScreenshotId
from app.domain.vision.extraction import (
    ConfidenceError,
    ExtractedValue,
    ObservationKind,
    ObservedField,
    VisionConfidence,
    VisionExtraction,
)
from app.domain.vision.precedence import (
    ResolvedValue,
    SourcedValue,
    conflicts_of,
    resolve,
    resolve_all,
)
from app.domain.vision.quality import ScreenshotQuality
from app.domain.vision.slots import ScreenshotSlot

SHOT = ScreenshotId(value="fixture-screenshot")
SLOT = ScreenshotSlot.H1


def observation(
    field: ObservedField = ObservedField.INDICATOR_READING,
    value: str = "63.2",
    kind: ObservationKind = ObservationKind.DIRECTLY_VISIBLE,
    confidence: str | None = "0.74",
) -> ExtractedValue:
    return ExtractedValue(
        field=field,
        value=value,
        kind=kind,
        screenshot_id=SHOT,
        slot=SLOT,
        confidence=VisionConfidence.of(confidence) if confidence is not None else None,
    )


# ----------------------------------------------------------------------
# Confidence
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_confidence_is_bounded_to_zero_and_one() -> None:
    assert VisionConfidence.of("0.74").value == Decimal("0.74")
    assert VisionConfidence.of(0).value == Decimal(0)
    assert VisionConfidence.of(1).value == Decimal(1)


@pytest.mark.unit
@pytest.mark.parametrize("bad", ("-0.01", "1.01", "5", "NaN"))
def test_an_out_of_range_confidence_is_refused(bad: str) -> None:
    with pytest.raises(ConfidenceError):
        VisionConfidence.of(bad)


@pytest.mark.unit
def test_a_float_confidence_does_not_acquire_binary_noise() -> None:
    """JSON has no decimal type, so 0.74 arrives as a float; it must stay
    0.74 rather than becoming 0.7400000000000000133."""
    assert VisionConfidence.of(0.74).value == Decimal("0.74")


@pytest.mark.unit
def test_a_missing_confidence_stays_missing() -> None:
    """Neither 0 nor 1: the model did not tell us, and both would be
    inventions."""
    item = observation(confidence=None)
    assert item.confidence is None
    assert not item.has_confidence


@pytest.mark.unit
def test_observations_without_confidence_are_surfaced() -> None:
    extraction = VisionExtraction(
        screenshot_id=SHOT,
        slot=SLOT,
        values=(observation(), observation(value="61.0", confidence=None)),
    )
    assert len(extraction.without_confidence) == 1


@pytest.mark.unit
def test_confidence_is_not_named_as_a_probability() -> None:
    """Model-reported confidence is not a calibrated statistic."""
    forbidden = {"probability", "win_rate", "likelihood", "odds", "chance"}
    assert forbidden.isdisjoint(VisionConfidence.__dataclass_fields__)
    assert forbidden.isdisjoint(ExtractedValue.__dataclass_fields__)


@pytest.mark.unit
def test_quality_and_confidence_are_different_types() -> None:
    """§8: they measure different things and must not collapse.

    Quality describes the image; confidence describes one field read from it.
    Neither is a field of the other.
    """
    assert "confidence" not in ScreenshotQuality.__dataclass_fields__
    assert "quality" not in ExtractedValue.__dataclass_fields__
    assert "score" not in VisionConfidence.__dataclass_fields__


# ----------------------------------------------------------------------
# Read vs inferred
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_reading_and_a_judgement_rank_differently() -> None:
    """ "RSI 63.2 is printed here" is stronger evidence than "the trend looks
    bullish", and §1's precedence already says so."""
    read = observation(kind=ObservationKind.DIRECTLY_VISIBLE)
    judged = observation(
        field=ObservedField.TREND_CONTEXT,
        value="yükseliş görünüyor",
        kind=ObservationKind.VISUALLY_INFERRED,
    )
    assert read.source_priority is DataSourcePriority.SCREENSHOT_EXTRACTED
    assert judged.source_priority is DataSourcePriority.AI_VISUAL_INFERENCE
    assert read.source_priority.wins_over(judged.source_priority)


@pytest.mark.unit
def test_an_extraction_separates_readings_from_inferences() -> None:
    extraction = VisionExtraction(
        screenshot_id=SHOT,
        slot=SLOT,
        values=(
            observation(),
            observation(
                field=ObservedField.TREND_CONTEXT,
                value="yükseliş",
                kind=ObservationKind.VISUALLY_INFERRED,
            ),
        ),
    )
    assert len(extraction.directly_visible) == 1
    assert len(extraction.inferred) == 1


@pytest.mark.unit
def test_an_observation_cannot_cite_a_different_screenshot() -> None:
    """Identity binding: an observation belongs to the image it came from."""
    with pytest.raises(ValueError, match="cites screenshot"):
        VisionExtraction(
            screenshot_id=ScreenshotId(value="other"),
            slot=SLOT,
            values=(observation(),),
        )


@pytest.mark.unit
def test_an_empty_observation_is_impossible() -> None:
    with pytest.raises(ValueError, match="carries no value"):
        observation(value="   ")


@pytest.mark.unit
def test_a_naive_timestamp_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        VisionExtraction(
            screenshot_id=SHOT,
            slot=SLOT,
            observed_at=datetime(2026, 3, 2, 12, 0),  # noqa: DTZ001
        )


# ----------------------------------------------------------------------
# The strict schema boundary
# ----------------------------------------------------------------------


def valid_payload() -> dict[str, object]:
    return {
        "slot": "1H",
        "detected_timeframe": "1H",
        "values": [
            {
                "field": "INDICATOR_READING",
                "value": "RSI 63.2",
                "kind": "DIRECTLY_VISIBLE",
                "confidence": "0.74",
            }
        ],
        "unreadable": [{"field": "SYMBOL", "reason": "the ticker is cropped off"}],
        "observed_at": "2026-03-02T12:00:00+00:00",
        "model": "fixture-model",
    }


@pytest.mark.unit
def test_a_valid_payload_parses_and_converts_to_domain() -> None:
    schema = VisionExtractionSchema.model_validate(valid_payload())
    extraction = schema.to_domain(SHOT)
    assert extraction.screenshot_id == SHOT
    assert extraction.slot is ScreenshotSlot.H1
    assert extraction.values[0].confidence is not None
    assert extraction.values[0].confidence.value == Decimal("0.74")
    assert extraction.observed_at == datetime(2026, 3, 2, 12, 0, tzinfo=UTC)


@pytest.mark.unit
def test_an_unknown_field_is_rejected() -> None:
    """extra="forbid": a model that invented a key has not answered the
    question asked."""
    payload = valid_payload()
    payload["hallucinated_field"] = "surprise"
    with pytest.raises(ValidationError, match="Extra inputs"):
        VisionExtractionSchema.model_validate(payload)


@pytest.mark.unit
@pytest.mark.parametrize("bad", ("-0.1", "1.5", "not-a-number"))
def test_an_invalid_confidence_is_rejected_at_the_boundary(bad: str) -> None:
    with pytest.raises(ValidationError):
        ExtractedValueSchema.model_validate(
            {
                "field": "INDICATOR_READING",
                "value": "63.2",
                "kind": "DIRECTLY_VISIBLE",
                "confidence": bad,
            }
        )


@pytest.mark.unit
def test_an_omitted_confidence_is_allowed_and_stays_none() -> None:
    schema = ExtractedValueSchema.model_validate(
        {"field": "INDICATOR_READING", "value": "63.2", "kind": "DIRECTLY_VISIBLE"}
    )
    assert schema.confidence is None


@pytest.mark.unit
def test_a_blank_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ExtractedValueSchema.model_validate(
            {"field": "SYMBOL", "value": "   ", "kind": "DIRECTLY_VISIBLE"}
        )


@pytest.mark.unit
def test_a_naive_timestamp_is_rejected_at_the_boundary() -> None:
    payload = valid_payload()
    payload["observed_at"] = "2026-03-02T12:00:00"
    with pytest.raises(ValidationError, match="timezone-aware"):
        VisionExtractionSchema.model_validate(payload)


@pytest.mark.unit
def test_the_model_cannot_choose_which_screenshot_it_saw() -> None:
    """A hallucinated id would attach observations to the wrong chart, so the
    identity is supplied by the caller and is not a schema field."""
    assert "screenshot_id" not in VisionExtractionSchema.model_fields
    extraction = VisionExtractionSchema.model_validate(valid_payload()).to_domain(SHOT)
    assert all(item.screenshot_id == SHOT for item in extraction.values)


@pytest.mark.unit
def test_a_detected_timeframe_does_not_reassign_the_slot() -> None:
    payload = valid_payload()
    payload["detected_timeframe"] = "15M"
    schema = VisionExtractionSchema.model_validate(payload)
    assert schema.slot is ScreenshotSlot.H1
    assert schema.detected_timeframe is Timeframe.M15
    assert schema.to_domain(SHOT).slot is ScreenshotSlot.H1


# ----------------------------------------------------------------------
# Precedence: §38's worked example
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_structured_data_beats_a_screenshot_reading() -> None:
    """§38 verbatim: structured RSI 61.27 against a screenshot's 63.2."""
    structured = SourcedValue(
        field="RSI", value="61.27", source=DataSourcePriority.STRUCTURED_MARKET_DATA
    )
    screenshot = SourcedValue(
        field="RSI", value="63.2", source=DataSourcePriority.SCREENSHOT_EXTRACTED
    )
    resolved = resolve((screenshot, structured))

    assert resolved.value == "61.27"
    assert resolved.source is DataSourcePriority.STRUCTURED_MARKET_DATA


@pytest.mark.unit
def test_the_losing_claim_is_preserved_not_discarded() -> None:
    """§13: never discard a disagreement silently."""
    structured = SourcedValue(
        field="RSI", value="61.27", source=DataSourcePriority.STRUCTURED_MARKET_DATA
    )
    screenshot = SourcedValue(
        field="RSI", value="63.2", source=DataSourcePriority.SCREENSHOT_EXTRACTED
    )
    resolved = resolve((structured, screenshot))

    assert resolved.has_conflict
    conflict = resolved.conflicts[0]
    assert conflict.rejected.value == "63.2"
    assert "61.27" in conflict.describe
    assert "63.2" in conflict.describe


@pytest.mark.unit
@pytest.mark.parametrize(
    "stronger",
    (
        DataSourcePriority.STRUCTURED_MARKET_DATA,
        DataSourcePriority.USER_CONFIRMED,
        DataSourcePriority.VALIDATED_CONTRACT_METADATA,
    ),
)
def test_every_stronger_source_beats_a_screenshot(stronger: DataSourcePriority) -> None:
    winner = SourcedValue(field="RSI", value="61.27", source=stronger)
    screenshot = SourcedValue(
        field="RSI", value="63.2", source=DataSourcePriority.SCREENSHOT_EXTRACTED
    )
    assert resolve((screenshot, winner)).source is stronger


@pytest.mark.unit
def test_a_reading_beats_an_inference() -> None:
    read = SourcedValue(
        field="TREND_CONTEXT", value="up", source=DataSourcePriority.SCREENSHOT_EXTRACTED
    )
    inferred = SourcedValue(
        field="TREND_CONTEXT", value="down", source=DataSourcePriority.AI_VISUAL_INFERENCE
    )
    assert resolve((inferred, read)).value == "up"


@pytest.mark.unit
def test_high_confidence_never_promotes_a_weaker_source() -> None:
    """A model reporting 0.99 does not outrank a computed number."""
    structured = SourcedValue(
        field="RSI", value="61.27", source=DataSourcePriority.STRUCTURED_MARKET_DATA
    )
    confident_screenshot = SourcedValue(
        field="RSI",
        value="63.2",
        source=DataSourcePriority.SCREENSHOT_EXTRACTED,
        confidence=VisionConfidence.of("0.99"),
    )
    assert resolve((confident_screenshot, structured)).value == "61.27"


@pytest.mark.unit
def test_agreement_is_recorded_as_corroboration_not_conflict() -> None:
    structured = SourcedValue(
        field="RSI", value="61.27", source=DataSourcePriority.STRUCTURED_MARKET_DATA
    )
    screenshot = SourcedValue(
        field="RSI", value="61.27", source=DataSourcePriority.SCREENSHOT_EXTRACTED
    )
    resolved = resolve((structured, screenshot))
    assert not resolved.has_conflict
    assert resolved.agreeing == (screenshot,)


@pytest.mark.unit
def test_an_observation_enters_precedence_at_its_own_rank() -> None:
    read = SourcedValue.from_observation(observation())
    judged = SourcedValue.from_observation(
        observation(
            field=ObservedField.TREND_CONTEXT,
            value="up",
            kind=ObservationKind.VISUALLY_INFERRED,
        )
    )
    assert read.source is DataSourcePriority.SCREENSHOT_EXTRACTED
    assert judged.source is DataSourcePriority.AI_VISUAL_INFERENCE


@pytest.mark.unit
def test_resolving_many_fields_keeps_each_separate() -> None:
    candidates = (
        SourcedValue(field="RSI", value="63.2", source=DataSourcePriority.SCREENSHOT_EXTRACTED),
        SourcedValue(field="RSI", value="61.27", source=DataSourcePriority.STRUCTURED_MARKET_DATA),
        SourcedValue(field="SYMBOL", value="X", source=DataSourcePriority.SCREENSHOT_EXTRACTED),
    )
    resolved = resolve_all(candidates)
    assert [item.field for item in resolved] == ["RSI", "SYMBOL"]
    assert len(conflicts_of(resolved)) == 1


@pytest.mark.unit
def test_mixing_fields_in_one_resolution_is_refused() -> None:
    with pytest.raises(ValueError, match="different fields"):
        resolve(
            (
                SourcedValue(
                    field="RSI", value="1", source=DataSourcePriority.SCREENSHOT_EXTRACTED
                ),
                SourcedValue(
                    field="ATR", value="2", source=DataSourcePriority.SCREENSHOT_EXTRACTED
                ),
            )
        )


@pytest.mark.unit
def test_resolving_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="no candidates"):
        resolve(())


@pytest.mark.unit
def test_resolution_is_deterministic() -> None:
    candidates = (
        SourcedValue(field="RSI", value="63.2", source=DataSourcePriority.SCREENSHOT_EXTRACTED),
        SourcedValue(field="RSI", value="61.27", source=DataSourcePriority.STRUCTURED_MARKET_DATA),
    )
    assert resolve(candidates) == resolve(candidates)


@pytest.mark.unit
def test_a_resolved_value_carries_no_probability_field() -> None:
    forbidden = {"probability", "likelihood", "win_rate"}
    assert forbidden.isdisjoint(ResolvedValue.__dataclass_fields__)

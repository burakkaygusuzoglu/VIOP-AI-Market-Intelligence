"""The Claude Vision adapter, corrections and precedence (§1-§14 of the brief).

**No test here needs an API key, a network connection or a paid call.** The
adapter takes a `VisionTransport`, so every path - success, malformed JSON,
schema violation, timeout, rate limit - is exercised with a fake that returns
whatever the test needs.

Every value is TEST_FIXTURE data.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from PIL import Image

from app.adapters.vision.claude_analyzer import AnalyzerConfig, ClaudeScreenshotAnalyzer
from app.adapters.vision.transport import VisionRequest, VisionResponse, VisionUsage
from app.application.ports.screenshot import ScreenshotContext, ScreenshotPayload
from app.application.ports.system import ClockPort
from app.application.vision.analysis import (
    ScreenshotSetReview,
    Staleness,
    SymbolAgreement,
    UnusableClaimsError,
    effective_value,
    record_correction,
    review_screenshot,
    stale_check,
    staleness_of,
)
from app.application.vision.errors import VisionFailure, VisionProviderError
from app.application.vision.intake import accept_and_verify
from app.application.vision.prompt import PROMPT_VERSION, VISION_SYSTEM_PROMPT, VisionPrompt
from app.domain.common.enums import DataSourcePriority, Timeframe
from app.domain.vision.corrections import CorrectionLog, CorrectionType, FieldCorrection
from app.domain.vision.extraction import ObservationKind, ObservedField
from app.domain.vision.precedence import SourcedValue, ValueKind, resolve
from app.domain.vision.quality import QualityDimension
from app.domain.vision.slots import ScreenshotSlot

NOW = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)
MODEL = "fixture-vision-model"


def chart_png(width: int = 640, height: int = 480) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def accepted(slot: ScreenshotSlot = ScreenshotSlot.H1):  # type: ignore[no-untyped-def]
    return accept_and_verify(chart_png(), slot, filename="chart.png")


def payload_of(slot: ScreenshotSlot = ScreenshotSlot.H1) -> ScreenshotPayload:
    return ScreenshotPayload.from_accepted(accepted(slot))


def response_payload(
    *,
    slot: str = "1H",
    detected_timeframe: str | None = "1H",
    symbol: str | None = "FIXTURE_SYM",
    indicator: str | None = "RSI 63.2",
    confidence: str | None = "0.74",
) -> dict[str, object]:
    values: list[dict[str, object]] = []
    if symbol is not None:
        values.append(
            {
                "field": "SYMBOL",
                "value": symbol,
                "kind": "DIRECTLY_VISIBLE",
                "confidence": confidence,
            }
        )
    if detected_timeframe is not None:
        values.append(
            {
                "field": "TIMEFRAME",
                "value": detected_timeframe,
                "kind": "DIRECTLY_VISIBLE",
                "timeframe": detected_timeframe,
                "confidence": confidence,
            }
        )
    if indicator is not None:
        values.append(
            {
                "field": "INDICATOR_READING",
                "value": indicator,
                "kind": "DIRECTLY_VISIBLE",
                "confidence": confidence,
            }
        )
    values.append(
        {
            "field": "TREND_CONTEXT",
            "value": "yükseliş görünüyor",
            "kind": "VISUALLY_INFERRED",
            "confidence": "0.55",
        }
    )
    return {
        "slot": slot,
        "detected_timeframe": detected_timeframe,
        "values": values,
        "unreadable": [],
        "model": MODEL,
    }


@dataclass
class FakeTransport:
    """A `VisionTransport` that returns whatever the test needs.

    Records the request so tests can assert what was actually sent - the
    prompt, the media type, the model - without a network call.
    """

    text: str = ""
    error: VisionProviderError | None = None
    sent: list[VisionRequest] = dataclass_field(default_factory=list)

    async def send(self, request: VisionRequest) -> VisionResponse:
        self.sent.append(request)
        if self.error is not None:
            raise self.error
        return VisionResponse(
            text=self.text, usage=VisionUsage(input_tokens=100, output_tokens=50, model=MODEL)
        )


def analyzer(text: str = "", error: VisionProviderError | None = None):  # type: ignore[no-untyped-def]
    transport = FakeTransport(text=text, error=error)
    return ClaudeScreenshotAnalyzer(transport, AnalyzerConfig(model=MODEL)), transport


def ok_analyzer(**kwargs: object):  # type: ignore[no-untyped-def]
    return analyzer(json.dumps(response_payload(**kwargs)))  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# The happy path
# ----------------------------------------------------------------------


@pytest.mark.unit
async def test_a_valid_response_becomes_typed_observations() -> None:
    engine, transport = ok_analyzer()
    result = await engine.analyse(payload_of(), ScreenshotContext())

    assert result.extraction.slot is ScreenshotSlot.H1
    assert result.extraction.model == MODEL
    assert len(result.extraction.directly_visible) == 3
    assert len(result.extraction.inferred) == 1
    assert transport.sent[0].model == MODEL


@pytest.mark.unit
async def test_every_observation_keeps_its_screenshot_identity() -> None:
    engine, _ = ok_analyzer()
    payload = payload_of()
    result = await engine.analyse(payload, ScreenshotContext())
    for observation in result.extraction.values:
        assert observation.screenshot_id == payload.asset.screenshot_id
        assert observation.slot is payload.asset.slot


@pytest.mark.unit
async def test_the_normalised_payload_is_what_gets_sent() -> None:
    """Review C: the provider receives the re-encoded image, not the upload."""
    engine, transport = ok_analyzer()
    payload = payload_of()
    await engine.analyse(payload, ScreenshotContext())
    assert transport.sent[0].image_media_type == "image/png"
    assert transport.sent[0].image_bytes == payload.data


@pytest.mark.unit
async def test_the_versioned_prompt_is_sent() -> None:
    engine, transport = ok_analyzer()
    await engine.analyse(payload_of(), ScreenshotContext(expected_symbol="FIXTURE_SYM"))
    assert transport.sent[0].system == VISION_SYSTEM_PROMPT
    assert "FIXTURE_SYM" in transport.sent[0].user_message
    assert "Do not assume they are right" in transport.sent[0].user_message


@pytest.mark.unit
def test_the_prompt_forbids_everything_it_must() -> None:
    """§3: the instructions are part of the safety boundary.

    Whitespace is collapsed before matching so re-wrapping a paragraph cannot
    break the test without changing what the prompt actually says.
    """
    prompt = " ".join(VISION_SYSTEM_PROMPT.split())
    for phrase in (
        "Do NOT calculate anything",
        "Do NOT recommend a direction",
        "No LONG, no SHORT, no BUY, no SELL, no WAIT",
        "Do NOT propose an entry price, a stop-loss or a take-profit level",
        "Do NOT guess a value you cannot read",
        "This is a legibility signal, not a probability about the market",
        "Do NOT calculate or estimate profit, loss, risk, margin, leverage, position size",
    ):
        assert phrase in prompt, phrase
    assert VisionPrompt().prompt_version == PROMPT_VERSION


@pytest.mark.unit
async def test_usage_is_captured_but_nothing_depends_on_it() -> None:
    engine, _ = ok_analyzer()
    result = await engine.analyse(payload_of(), ScreenshotContext())
    assert "usage" not in type(result).__dataclass_fields__


# ----------------------------------------------------------------------
# Invalid output is never accepted (§4)
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("label", "text"),
    (
        ("empty", ""),
        ("not json", "I looked at the chart and it seems bullish."),
        ("json array", "[1, 2, 3]"),
        ("json fenced", "```json\n{}\n```"),
    ),
)
async def test_malformed_output_is_refused(label: str, text: str) -> None:
    engine, _ = analyzer(text)
    with pytest.raises(VisionProviderError) as excinfo:
        await engine.analyse(payload_of(), ScreenshotContext())
    assert excinfo.value.failure is VisionFailure.INVALID_OUTPUT, label


@pytest.mark.unit
async def test_an_unexpected_field_is_refused_not_stripped() -> None:
    """§4: dangerous malformed output must not be silently repaired."""
    payload = response_payload()
    payload["recommended_action"] = "LONG"
    engine, _ = analyzer(json.dumps(payload))
    with pytest.raises(VisionProviderError) as excinfo:
        await engine.analyse(payload_of(), ScreenshotContext())
    assert excinfo.value.failure is VisionFailure.INVALID_OUTPUT


@pytest.mark.unit
async def test_an_invalid_enum_is_refused() -> None:
    payload = response_payload()
    payload["values"] = [{"field": "NOT_A_REAL_FIELD", "value": "x", "kind": "DIRECTLY_VISIBLE"}]
    engine, _ = analyzer(json.dumps(payload))
    with pytest.raises(VisionProviderError) as excinfo:
        await engine.analyse(payload_of(), ScreenshotContext())
    assert excinfo.value.failure is VisionFailure.INVALID_OUTPUT


@pytest.mark.unit
@pytest.mark.parametrize("bad", ("1.4", "-0.2"))
async def test_an_out_of_range_confidence_is_refused(bad: str) -> None:
    engine, _ = ok_analyzer(confidence=bad)
    with pytest.raises(VisionProviderError) as excinfo:
        await engine.analyse(payload_of(), ScreenshotContext())
    assert excinfo.value.failure is VisionFailure.INVALID_OUTPUT


@pytest.mark.unit
async def test_a_rejection_message_never_echoes_model_output() -> None:
    """A validation message can quote the offending value, and that value came
    from a model reading a user's screenshot."""
    payload = response_payload()
    payload["values"] = [
        {"field": "SYMBOL", "value": "SENSITIVE-CHART-CONTENT", "kind": "NOT_A_KIND"}
    ]
    engine, _ = analyzer(json.dumps(payload))
    with pytest.raises(VisionProviderError) as excinfo:
        await engine.analyse(payload_of(), ScreenshotContext())
    assert "SENSITIVE-CHART-CONTENT" not in str(excinfo.value)


@pytest.mark.unit
async def test_invalid_output_is_not_retryable() -> None:
    """A model that returned the wrong shape will likely return it again."""
    engine, _ = analyzer("not json")
    with pytest.raises(VisionProviderError) as excinfo:
        await engine.analyse(payload_of(), ScreenshotContext())
    assert not excinfo.value.retryable


# ----------------------------------------------------------------------
# Provider errors (§14)
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failure", "retryable"),
    (
        (VisionFailure.TIMEOUT, True),
        (VisionFailure.NETWORK, True),
        (VisionFailure.RATE_LIMITED, True),
        (VisionFailure.UNAVAILABLE, True),
        (VisionFailure.AUTHENTICATION, False),
        (VisionFailure.CONFIGURATION, False),
        (VisionFailure.PROVIDER_REJECTED, False),
        (VisionFailure.INVALID_OUTPUT, False),
    ),
)
async def test_each_provider_failure_propagates_with_its_retry_semantics(
    failure: VisionFailure, retryable: bool
) -> None:
    engine, _ = analyzer(error=VisionProviderError(failure, "fixture"))
    with pytest.raises(VisionProviderError) as excinfo:
        await engine.analyse(payload_of(), ScreenshotContext())
    assert excinfo.value.failure is failure
    assert excinfo.value.retryable is retryable


@pytest.mark.unit
def test_a_public_error_message_never_leaks_provider_detail() -> None:
    """§17: typed public errors, no internals."""
    error = VisionProviderError(
        VisionFailure.AUTHENTICATION, "x-api-key sk-ant-SECRET was rejected by api.anthropic.com"
    )
    assert "sk-ant" not in error.public_detail
    assert "anthropic" not in error.public_detail.lower()


@pytest.mark.unit
def test_an_unconfigured_model_refuses_to_build() -> None:
    """§2: no safe default exists, so it must be set explicitly."""
    with pytest.raises(VisionProviderError) as excinfo:
        AnalyzerConfig(model="   ")
    assert excinfo.value.failure is VisionFailure.CONFIGURATION


@pytest.mark.unit
def test_the_transport_refuses_an_unsupported_media_type() -> None:
    from app.adapters.vision.transport import _media_type_param  # noqa: PLC0415

    with pytest.raises(VisionProviderError) as excinfo:
        _media_type_param("image/gif")
    assert excinfo.value.failure is VisionFailure.CONFIGURATION


# ----------------------------------------------------------------------
# Quality stays deterministic (§7)
# ----------------------------------------------------------------------


@pytest.mark.unit
async def test_the_quality_score_comes_from_what_was_read_not_from_a_claim() -> None:
    engine, _ = ok_analyzer()
    result = await engine.analyse(payload_of(), ScreenshotContext())

    symbol = result.quality.dimension(QualityDimension.SYMBOL_VISIBLE)
    assert symbol is not None
    assert symbol.state.value == "PRESENT"
    assert result.quality.method_version.startswith("screenshot-quality/")


@pytest.mark.unit
async def test_a_model_cannot_supply_the_score_itself() -> None:
    """The schema has no field for a quality total, so a model claiming one
    is rejected as an unexpected field."""
    payload = response_payload()
    payload["screenshot_quality"] = 95
    engine, _ = analyzer(json.dumps(payload))
    with pytest.raises(VisionProviderError):
        await engine.analyse(payload_of(), ScreenshotContext())


@pytest.mark.unit
async def test_a_poor_screenshot_leaves_dimensions_unknown_rather_than_guessed() -> None:
    engine, _ = ok_analyzer(symbol=None, detected_timeframe=None, indicator=None)
    result = await engine.analyse(payload_of(), ScreenshotContext())
    assert QualityDimension.SYMBOL_VISIBLE in result.quality.not_evaluated
    assert QualityDimension.TIMEFRAME_VISIBLE in result.quality.not_evaluated


@pytest.mark.unit
async def test_staleness_is_undetermined_without_a_clock_and_a_capture_time() -> None:
    """§8: never guess a timezone, a session or an exchange clock."""
    assert stale_check(None, NOW) == "STALENESS_UNDETERMINED"
    assert stale_check(NOW, None) == "STALENESS_UNDETERMINED"
    engine, _ = ok_analyzer()
    result = await engine.analyse(payload_of(), ScreenshotContext())
    assert stale_check(result.extraction.observed_at, NOW) == "STALENESS_UNDETERMINED"


@pytest.mark.unit
def test_staleness_needs_an_explicit_age_policy_before_it_will_judge() -> None:
    """§10: no invented constant for "how old is too old"."""
    captured = NOW - timedelta(minutes=5)
    assert stale_check(captured, NOW) is Staleness.UNDETERMINED
    assert stale_check(captured, NOW, max_age=timedelta(minutes=10)) is Staleness.FRESH
    assert stale_check(captured, NOW, max_age=timedelta(minutes=1)) is Staleness.STALE


@pytest.mark.unit
def test_a_recent_screenshot_is_not_called_stale() -> None:
    """Any positive age used to mean STALE, which made every screenshot stale."""
    assert (
        stale_check(NOW - timedelta(seconds=1), NOW, max_age=timedelta(minutes=15))
        is Staleness.FRESH
    )


@pytest.mark.unit
def test_a_capture_time_in_the_future_is_undetermined_not_fresh() -> None:
    """The unsafe direction must not land on the reassuring answer.

    A chart timestamp read in an unknown timezone, or a skewed device clock,
    puts the capture instant ahead of now. Calling that FRESH would present a
    reading this project cannot date as one it had just verified.
    """
    assert (
        stale_check(NOW + timedelta(hours=3), NOW, max_age=timedelta(minutes=15))
        is Staleness.UNDETERMINED
    )


@pytest.mark.unit
async def test_the_current_instant_comes_from_the_clock_port() -> None:
    """§10: no ambient datetime.now() anywhere in the decision."""

    @dataclass
    class FixedClock:
        instant: datetime

        def now(self) -> datetime:
            return self.instant

    engine, _ = ok_analyzer()
    review = await review_screenshot(engine, accepted())
    clock = FixedClock(NOW)

    assert isinstance(clock, ClockPort)
    assert staleness_of(review, clock) is Staleness.UNDETERMINED
    assert staleness_of(review, clock, max_age=timedelta(minutes=15)) is Staleness.UNDETERMINED


# ----------------------------------------------------------------------
# Expected vs detected (§9)
# ----------------------------------------------------------------------


@pytest.mark.unit
async def test_a_matching_timeframe_agrees() -> None:
    engine, _ = ok_analyzer(slot="1H", detected_timeframe="1H")
    review = await review_screenshot(engine, accepted(ScreenshotSlot.H1))
    assert review.slot_assignment.detected is Timeframe.H1
    assert not review.has_mismatch


@pytest.mark.unit
async def test_a_1h_slot_holding_a_15m_chart_stays_an_explicit_mismatch() -> None:
    """§9: never silently rewrite the expected context."""
    engine, _ = ok_analyzer(slot="1H", detected_timeframe="15M")
    review = await review_screenshot(engine, accepted(ScreenshotSlot.H1))

    assert review.slot_assignment.is_mismatched
    assert review.slot is ScreenshotSlot.H1
    assert review.slot_assignment.detected is Timeframe.M15
    assert review.slot_assignment.effective_timeframe is None
    assert any("15M" in item for item in review.mismatches)


@pytest.mark.unit
async def test_a_symbol_mismatch_is_explicit() -> None:
    engine, _ = ok_analyzer(symbol="ASELS")
    review = await review_screenshot(engine, accepted(), expected_symbol="OYAKC")
    assert review.symbol.is_mismatch
    assert "OYAKC" in review.symbol.describe
    assert "ASELS" in review.symbol.describe


@pytest.mark.unit
async def test_a_symbol_case_difference_is_reported_as_a_mismatch() -> None:
    """Phase 6 must use the project's identity rule, not a looser one.

    This test previously asserted the opposite. Phase 3 fixed the rule in
    `require_matching_quote`: exact match after stripping, explicitly no case
    folding, because whether the exchange treats casing as significant is a
    convention nobody here has verified. Case-folding in the screenshot check
    put the looser of two rules exactly where a user is told whether the chart
    shows the instrument they meant.
    """
    engine, _ = ok_analyzer(symbol="fixtureSYM")
    review = await review_screenshot(engine, accepted(), expected_symbol="FIXTURESYM")
    assert review.symbol.is_mismatch
    assert "FIXTURESYM" in review.symbol.describe
    assert "fixtureSYM" in review.symbol.describe


@pytest.mark.unit
async def test_an_unreadable_timeframe_is_not_a_mismatch() -> None:
    """Nobody read it, which is a different answer from a disagreement."""
    engine, _ = ok_analyzer(detected_timeframe=None)
    review = await review_screenshot(engine, accepted())
    assert review.slot_assignment.detected is None
    assert not review.slot_assignment.is_mismatched


# ----------------------------------------------------------------------
# Corrections (§10, §11)
# ----------------------------------------------------------------------


def observation_of(review, field: ObservedField):  # type: ignore[no-untyped-def]
    return review.extraction.of_field(field)[0]


@dataclass
class FixedClock:
    """A `ClockPort` that returns a fixed instant."""

    instant: datetime = NOW

    def now(self) -> datetime:
        return self.instant


@pytest.mark.unit
async def test_a_correction_is_stamped_by_the_clock_port_not_by_the_caller() -> None:
    """§12: the timestamp comes from the port, so replay keeps its own time."""
    engine, _ = ok_analyzer()
    review = await review_screenshot(engine, accepted())
    replay_instant = datetime(2021, 6, 4, 9, 30, tzinfo=UTC)

    corrected = record_correction(
        review,
        ObservedField.SYMBOL,
        CorrectionType.CORRECTED,
        FixedClock(replay_instant),
        corrected_value="OYAKC",
    )

    latest = corrected.corrections.latest_for(ObservedField.SYMBOL)
    assert latest is not None
    assert latest.corrected_at == replay_instant, "an ambient clock was used"
    assert latest.corrected_at.tzinfo is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("correction_type", "value"),
    (
        (CorrectionType.CONFIRMED, None),
        (CorrectionType.CORRECTED, "OYAKC"),
        (CorrectionType.REJECTED, None),
    ),
)
async def test_every_correction_kind_preserves_the_original_observation(
    correction_type: CorrectionType, value: str | None
) -> None:
    engine, _ = ok_analyzer(symbol="ASELS")
    review = await review_screenshot(engine, accepted())

    corrected = record_correction(
        review, ObservedField.SYMBOL, correction_type, FixedClock(), corrected_value=value
    )

    latest = corrected.corrections.latest_for(ObservedField.SYMBOL)
    assert latest is not None
    assert latest.original_value == "ASELS", "the model's reading was erased"
    assert corrected.observed_value(ObservedField.SYMBOL) == "ASELS", (
        "what the picture shows must survive the correction"
    )


@pytest.mark.unit
async def test_the_log_is_append_only_across_repeated_corrections() -> None:
    engine, _ = ok_analyzer()
    review = await review_screenshot(engine, accepted())

    once = record_correction(
        review, ObservedField.SYMBOL, CorrectionType.CORRECTED, FixedClock(), corrected_value="AAA"
    )
    twice = record_correction(
        once, ObservedField.SYMBOL, CorrectionType.CORRECTED, FixedClock(), corrected_value="BBB"
    )

    assert len(twice.corrections.for_field(ObservedField.SYMBOL)) == 2
    assert review.corrections.corrections == (), "the original review was mutated"
    latest = twice.corrections.latest_for(ObservedField.SYMBOL)
    assert latest is not None and latest.effective_value == "BBB"


@pytest.mark.unit
async def test_correcting_a_field_the_screenshot_never_mentioned_is_refused() -> None:
    engine, _ = ok_analyzer()
    review = await review_screenshot(engine, accepted())
    with pytest.raises(ValueError, match="no such observation"):
        record_correction(review, ObservedField.LAST_PRICE, CorrectionType.CONFIRMED, FixedClock())


@pytest.mark.unit
async def test_confirming_a_field_raises_its_authority_without_changing_it() -> None:
    engine, _ = ok_analyzer()
    review = await review_screenshot(engine, accepted())
    original = observation_of(review, ObservedField.SYMBOL)

    correction = FieldCorrection(
        original=original,
        correction_type=CorrectionType.CONFIRMED,
        corrected_value=None,
        corrected_at=NOW,
    )
    assert correction.effective_value == original.value
    assert not correction.changed_the_value

    claim = correction.as_sourced_value()
    assert claim is not None
    assert claim.source is DataSourcePriority.USER_CONFIRMED


@pytest.mark.unit
async def test_correcting_a_field_preserves_the_original_observation() -> None:
    """§10: the AI value is never erased."""
    engine, _ = ok_analyzer(symbol="ASELS")
    review = await review_screenshot(engine, accepted())
    original = observation_of(review, ObservedField.SYMBOL)

    correction = FieldCorrection(
        original=original,
        correction_type=CorrectionType.CORRECTED,
        corrected_value="OYAKC",
        corrected_at=NOW,
        note="ticker was cropped",
    )
    assert correction.original_value == "ASELS"
    assert correction.effective_value == "OYAKC"
    assert correction.corrected_at == NOW


@pytest.mark.unit
async def test_rejecting_a_field_leaves_it_unknown_not_reverted() -> None:
    engine, _ = ok_analyzer()
    review = await review_screenshot(engine, accepted())
    correction = FieldCorrection(
        original=observation_of(review, ObservedField.SYMBOL),
        correction_type=CorrectionType.REJECTED,
        corrected_value=None,
        corrected_at=NOW,
    )
    assert correction.effective_value is None
    assert correction.as_sourced_value() is None


@pytest.mark.unit
async def test_a_correction_without_a_replacement_is_refused() -> None:
    engine, _ = ok_analyzer()
    review = await review_screenshot(engine, accepted())
    with pytest.raises(ValueError, match="requires a replacement"):
        FieldCorrection(
            original=observation_of(review, ObservedField.SYMBOL),
            correction_type=CorrectionType.CORRECTED,
            corrected_value="  ",
            corrected_at=NOW,
        )


@pytest.mark.unit
async def test_a_correction_timestamp_must_be_timezone_aware() -> None:
    """§10: time comes from a ClockPort, never ambient wall-clock."""
    engine, _ = ok_analyzer()
    review = await review_screenshot(engine, accepted())
    with pytest.raises(ValueError, match="timezone-aware"):
        FieldCorrection(
            original=observation_of(review, ObservedField.SYMBOL),
            correction_type=CorrectionType.CONFIRMED,
            corrected_value=None,
            corrected_at=datetime(2026, 3, 2, 12, 0),  # noqa: DTZ001
        )


@pytest.mark.unit
async def test_the_correction_log_is_append_only_and_keeps_superseded_entries() -> None:
    engine, _ = ok_analyzer()
    review = await review_screenshot(engine, accepted())
    original = observation_of(review, ObservedField.SYMBOL)

    first = FieldCorrection(
        original=original,
        correction_type=CorrectionType.CORRECTED,
        corrected_value="AAAAA",
        corrected_at=NOW,
    )
    second = replace(first, corrected_value="BBBBB")
    log = CorrectionLog().with_correction(first).with_correction(second)

    assert len(log.corrections) == 2
    latest = log.latest_for(ObservedField.SYMBOL)
    assert latest is not None
    assert latest.corrected_value == "BBBBB"
    assert log.corrections[0].corrected_value == "AAAAA"


# ----------------------------------------------------------------------
# Precedence after correction (§11, §12)
# ----------------------------------------------------------------------


@pytest.mark.unit
async def test_a_user_confirmation_beats_raw_visual_inference() -> None:
    engine, _ = ok_analyzer()
    review = await review_screenshot(engine, accepted())
    inference = review.extraction.of_field(ObservedField.TREND_CONTEXT)[0]
    assert inference.kind is ObservationKind.VISUALLY_INFERRED

    correction = FieldCorrection(
        original=inference,
        correction_type=CorrectionType.CORRECTED,
        corrected_value="düşüş",
        corrected_at=NOW,
    )
    log = CorrectionLog().with_correction(correction)
    resolved = effective_value(review.with_correction(log), ObservedField.TREND_CONTEXT)
    assert resolved is not None
    assert resolved.value == "düşüş"
    assert resolved.source is DataSourcePriority.USER_CONFIRMED


@pytest.mark.unit
async def test_structured_data_still_wins_after_a_user_confirms_the_screenshot() -> None:
    """§12's mandatory case, end to end.

    The user confirms the screenshot visibly reads 63.2. The screenshot's
    authority rises to USER_CONFIRMED - and structured 61.27 still wins for
    calculation, with the confirmed reading preserved as a conflict.
    """
    engine, _ = ok_analyzer(indicator="63.2")
    review = await review_screenshot(engine, accepted())
    reading = review.extraction.of_field(ObservedField.INDICATOR_READING)[0]

    log = CorrectionLog().with_correction(
        FieldCorrection(
            original=reading,
            correction_type=CorrectionType.CONFIRMED,
            corrected_value=None,
            corrected_at=NOW,
        )
    )
    resolved = effective_value(
        review.with_correction(log),
        ObservedField.INDICATOR_READING,
        structured_value="61.27",
        kind=ValueKind.NUMERIC,
    )

    assert resolved is not None
    assert resolved.value == "61.27"
    assert resolved.source is DataSourcePriority.STRUCTURED_MARKET_DATA
    assert resolved.has_conflict

    # And the screenshot's own reading is still answerable.
    assert review.observed_value(ObservedField.INDICATOR_READING) == "63.2"


@pytest.mark.unit
async def test_the_observed_value_is_never_replaced_by_the_authority() -> None:
    """ "The screenshot visibly shows 63.2" stays true regardless."""
    engine, _ = ok_analyzer(indicator="63.2")
    review = await review_screenshot(engine, accepted())
    effective_value(
        review,
        ObservedField.INDICATOR_READING,
        structured_value="61.27",
        kind=ValueKind.NUMERIC,
    )
    assert review.observed_value(ObservedField.INDICATOR_READING) == "63.2"


# ----------------------------------------------------------------------
# Review D: typed value equality
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("written", ("61.27", "61.270", "61.2700", " 61.27 "))
def test_equivalent_decimal_representations_do_not_conflict(written: str) -> None:
    """Review D: formatting alone must not manufacture a disagreement."""
    structured = SourcedValue(
        field="RSI",
        value="61.27",
        source=DataSourcePriority.STRUCTURED_MARKET_DATA,
        kind=ValueKind.NUMERIC,
    )
    screenshot = SourcedValue(
        field="RSI",
        value=written,
        source=DataSourcePriority.SCREENSHOT_EXTRACTED,
        kind=ValueKind.NUMERIC,
    )
    resolved = resolve((structured, screenshot))
    assert not resolved.has_conflict
    assert resolved.agreeing == (screenshot,)


@pytest.mark.unit
def test_the_original_representation_is_preserved_for_audit() -> None:
    claim = SourcedValue(
        field="RSI",
        value="61.270",
        source=DataSourcePriority.SCREENSHOT_EXTRACTED,
        kind=ValueKind.NUMERIC,
    )
    assert claim.value == "61.270"
    assert claim.comparable == "61.27"
    assert claim.numeric == Decimal("61.270")


@pytest.mark.unit
def test_a_genuinely_different_number_still_conflicts() -> None:
    structured = SourcedValue(
        field="RSI",
        value="61.27",
        source=DataSourcePriority.STRUCTURED_MARKET_DATA,
        kind=ValueKind.NUMERIC,
    )
    screenshot = SourcedValue(
        field="RSI",
        value="63.2",
        source=DataSourcePriority.SCREENSHOT_EXTRACTED,
        kind=ValueKind.NUMERIC,
    )
    assert resolve((structured, screenshot)).has_conflict


@pytest.mark.unit
def test_numeric_comparison_never_uses_float() -> None:
    """Two values a float would wrongly equate stay distinct."""
    left = SourcedValue(
        field="PRICE",
        value="0.1",
        source=DataSourcePriority.STRUCTURED_MARKET_DATA,
        kind=ValueKind.NUMERIC,
    )
    right = SourcedValue(
        field="PRICE",
        value="0.10000000000000001",
        source=DataSourcePriority.SCREENSHOT_EXTRACTED,
        kind=ValueKind.NUMERIC,
    )
    assert not left.agrees_with(right)
    assert resolve((left, right)).has_conflict


@pytest.mark.unit
def test_a_categorical_field_keeps_text_semantics() -> None:
    """Instrument codes are not merged case-insensitively - that would assume
    an exchange convention this project has not verified."""
    left = SourcedValue(
        field="SYMBOL", value="ASELS", source=DataSourcePriority.STRUCTURED_MARKET_DATA
    )
    right = SourcedValue(
        field="SYMBOL", value="asels", source=DataSourcePriority.SCREENSHOT_EXTRACTED
    )
    assert not left.agrees_with(right)
    assert resolve((left, right)).has_conflict


@pytest.mark.unit
def test_identical_categorical_values_corroborate() -> None:
    left = SourcedValue(
        field="SYMBOL", value="ASELS", source=DataSourcePriority.STRUCTURED_MARKET_DATA
    )
    right = SourcedValue(
        field="SYMBOL", value=" ASELS ", source=DataSourcePriority.SCREENSHOT_EXTRACTED
    )
    assert not resolve((left, right)).has_conflict


@pytest.mark.unit
def test_a_non_numeric_value_cannot_be_declared_numeric() -> None:
    with pytest.raises(ValueError, match="not a number"):
        SourcedValue(
            field="RSI",
            value="about sixty",
            source=DataSourcePriority.SCREENSHOT_EXTRACTED,
            kind=ValueKind.NUMERIC,
        )


@pytest.mark.unit
def test_typed_equality_did_not_change_precedence() -> None:
    """Review D is explicit: authority ordering must be untouched."""
    structured = SourcedValue(
        field="RSI",
        value="61.27",
        source=DataSourcePriority.STRUCTURED_MARKET_DATA,
        kind=ValueKind.NUMERIC,
    )
    for weaker in (
        DataSourcePriority.USER_CONFIRMED,
        DataSourcePriority.SCREENSHOT_EXTRACTED,
        DataSourcePriority.AI_VISUAL_INFERENCE,
    ):
        other = SourcedValue(field="RSI", value="63.2", source=weaker, kind=ValueKind.NUMERIC)
        assert resolve((other, structured)).source is (DataSourcePriority.STRUCTURED_MARKET_DATA)


# ----------------------------------------------------------------------
# Multi-screenshot workflow (§13)
# ----------------------------------------------------------------------


async def review_for(slot: ScreenshotSlot, **kwargs: object):  # type: ignore[no-untyped-def]
    engine, _ = ok_analyzer(slot=slot.value, **kwargs)
    return await review_screenshot(engine, accepted(slot), expected_symbol="FIXTURE_SYM")


@pytest.mark.unit
async def test_all_four_slots_are_reviewed_separately() -> None:
    reviews = [await review_for(slot, detected_timeframe=slot.value) for slot in ScreenshotSlot]
    review_set = ScreenshotSetReview(reviews=tuple(reviews))
    assert len(review_set.filled_slots) == 4
    assert review_set.missing_slots == ()
    assert not review_set.has_any_mismatch


@pytest.mark.unit
async def test_a_missing_slot_is_explicit_and_allowed() -> None:
    """§13: nothing pretends four were supplied."""
    reviews = (await review_for(ScreenshotSlot.D1, detected_timeframe="1D"),)
    review_set = ScreenshotSetReview(reviews=reviews)
    assert review_set.missing_slots == (
        ScreenshotSlot.H1,
        ScreenshotSlot.M15,
        ScreenshotSlot.M5,
    )


@pytest.mark.unit
async def test_a_duplicate_slot_is_detected() -> None:
    first = await review_for(ScreenshotSlot.H1, detected_timeframe="1H")
    review_set = ScreenshotSetReview(reviews=(first, first))
    assert review_set.duplicate_slots == (ScreenshotSlot.H1,)
    assert review_set.has_any_mismatch


@pytest.mark.unit
async def test_conflicting_symbols_across_screenshots_are_reported_not_resolved() -> None:
    one = await review_for(ScreenshotSlot.H1, detected_timeframe="1H", symbol="FIXTURE_A")
    two = await review_for(ScreenshotSlot.M15, detected_timeframe="15M", symbol="FIXTURE_B")
    review_set = ScreenshotSetReview(reviews=(one, two))
    assert set(review_set.symbol_disagreements) == {"FIXTURE_A", "FIXTURE_B"}
    assert review_set.has_any_mismatch


@pytest.mark.unit
async def test_a_case_difference_across_screenshots_is_a_disagreement() -> None:
    """Reported for the user to settle, never folded together silently."""
    one = await review_for(ScreenshotSlot.H1, detected_timeframe="1H", symbol="FIXTURE_A")
    two = await review_for(ScreenshotSlot.M15, detected_timeframe="15M", symbol="fixture_a")
    review_set = ScreenshotSetReview(reviews=(one, two))
    assert set(review_set.symbol_disagreements) == {"FIXTURE_A", "fixture_a"}


@pytest.mark.unit
async def test_a_timeframe_mismatch_survives_into_the_set_review() -> None:
    mismatched = await review_for(ScreenshotSlot.H1, detected_timeframe="15M")
    review_set = ScreenshotSetReview(reviews=(mismatched,))
    assert review_set.timeframe_mismatches == (ScreenshotSlot.H1,)


@pytest.mark.unit
async def test_nothing_averages_or_merges_the_screenshots() -> None:
    """Each keeps its own identity, slot and observations."""
    reviews = [await review_for(slot, detected_timeframe=slot.value) for slot in ScreenshotSlot]
    identities = {review.accepted.asset.screenshot_id for review in reviews}
    assert len(identities) == 4
    for review in reviews:
        assert review.extraction.slot is review.slot


@pytest.mark.unit
def test_a_symbol_agreement_with_nothing_detected_is_not_a_mismatch() -> None:
    agreement = SymbolAgreement(expected="ASELS", detected=None)
    assert agreement.is_undetected
    assert not agreement.is_mismatch


# ----------------------------------------------------------------------
# A model's prose must not be able to raise out of a resolution
# ----------------------------------------------------------------------


def with_price(value: str, **kwargs: object) -> dict[str, object]:
    payload = response_payload(**kwargs)  # type: ignore[arg-type]
    existing = payload["values"]
    assert isinstance(existing, list)
    payload["values"] = [
        *existing,
        {
            "field": "LAST_PRICE",
            "value": value,
            "kind": "VISUALLY_INFERRED",
            "confidence": "0.40",
        },
    ]
    return payload


@pytest.mark.unit
async def test_prose_in_a_numeric_field_does_not_escape_as_a_crash() -> None:
    """Untrusted output must never reach a caller as an unhandled ValueError.

    A model asked for a price can answer "roughly 61 or so". That is a fact
    about the reading, not a defect in this code, so it is reported as an
    unusable claim - the strictness of `SourcedValue` stays intact for our own
    structured data, where the same text really would be a bug.
    """
    engine, _ = analyzer(json.dumps(with_price("roughly 61 or so")))
    review = await review_screenshot(engine, accepted())

    with pytest.raises(UnusableClaimsError) as excinfo:
        effective_value(review, ObservedField.LAST_PRICE, kind=ValueKind.NUMERIC)

    assert excinfo.value.observed is ObservedField.LAST_PRICE
    assert any("roughly 61 or so" in reason for reason in excinfo.value.reasons)


@pytest.mark.unit
async def test_nothing_observed_is_a_different_answer_from_nothing_usable() -> None:
    engine, _ = ok_analyzer()
    review = await review_screenshot(engine, accepted())
    assert effective_value(review, ObservedField.LAST_PRICE, kind=ValueKind.NUMERIC) is None


@pytest.mark.unit
async def test_an_unusable_claim_never_displaces_a_usable_one() -> None:
    engine, _ = analyzer(json.dumps(with_price("roughly 61")))
    review = await review_screenshot(engine, accepted())

    resolved = effective_value(
        review,
        ObservedField.LAST_PRICE,
        kind=ValueKind.NUMERIC,
        structured_value="61.27",
    )
    assert resolved is not None
    assert resolved.value == "61.27"
    assert resolved.source is DataSourcePriority.STRUCTURED_MARKET_DATA
    assert not resolved.has_conflict, "prose is not a competing value"
    assert any("roughly 61" in reason for reason in resolved.unusable), (
        "the unusable reading was dropped silently"
    )


@pytest.mark.unit
async def test_a_numerically_equal_screenshot_reading_is_not_a_conflict() -> None:
    """Review D, end to end: 61.270 read off a chart agrees with 61.27."""
    engine, _ = analyzer(json.dumps(with_price("61.270")))
    review = await review_screenshot(engine, accepted())

    resolved = effective_value(
        review,
        ObservedField.LAST_PRICE,
        kind=ValueKind.NUMERIC,
        structured_value="61.27",
    )
    assert resolved is not None
    assert not resolved.has_conflict
    assert resolved.source is DataSourcePriority.STRUCTURED_MARKET_DATA
    assert any(claim.value == "61.270" for claim in resolved.agreeing), (
        "the screenshot's own representation was not preserved"
    )

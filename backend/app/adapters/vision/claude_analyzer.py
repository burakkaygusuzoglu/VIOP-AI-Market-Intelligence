"""`ScreenshotAnalyzer` backed by Claude Vision (§1, §4, §7 of the brief).

This is where an untrusted model response becomes typed application state, or
does not become anything at all. The order matters:

    transport returns text
    → strict JSON parse           (malformed JSON is rejected)
    → strict schema validation    (unknown field, bad enum, bad confidence)
    → convert to domain values
    → deterministic quality scoring in Python

**Nothing is repaired.** §4 is explicit: dangerous malformed output must not be
silently fixed. There is no code path here that strips an unexpected key,
clamps an out-of-range confidence, or retries with a "please try again in the
right format" nudge. A response that does not satisfy the schema produces
`VisionFailure.INVALID_OUTPUT` and no state changes.

**The quality score stays ours.** §7 lets the model *observe* dimensions - is
the symbol legible, is the price scale visible - but the 0-100 arithmetic is
`score_quality` in `app.domain.vision.quality`, unchanged from Phase 6A. The
model contributes observations; it never contributes a total, and a test proves
the score is unaffected by anything it claims about quality.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError

from app.adapters.vision.transport import VisionRequest, VisionTransport
from app.application.ports.screenshot import (
    ScreenshotAnalysis,
    ScreenshotContext,
    ScreenshotPayload,
)
from app.application.vision.errors import VisionFailure, VisionProviderError
from app.application.vision.prompt import VisionPrompt
from app.application.vision.schemas import VisionExtractionSchema
from app.domain.vision.extraction import ObservedField, VisionExtraction
from app.domain.vision.quality import (
    DimensionState,
    QualityDimension,
    QualityPolicy,
    ScreenshotQuality,
    score_quality,
)

_FIELD_TO_DIMENSION: dict[ObservedField, QualityDimension] = {
    ObservedField.SYMBOL: QualityDimension.SYMBOL_VISIBLE,
    ObservedField.TIMEFRAME: QualityDimension.TIMEFRAME_VISIBLE,
    ObservedField.LAST_PRICE: QualityDimension.PRICE_SCALE_VISIBLE,
    ObservedField.INDICATOR_READING: QualityDimension.INDICATORS_READABLE,
}
"""Which observed fields evidence which §39 dimension.

Only four, and only these four: a dimension is marked PRESENT when the model
actually *read* the corresponding field, which is evidence that it was legible.
Nothing here asks the model "is the symbol visible?" and believes the answer -
the evidence is that it produced a symbol.
"""


@dataclass(frozen=True, slots=True)
class AnalyzerConfig:
    """Adapter settings. Application policy, not market facts."""

    model: str
    max_tokens: int = 2048
    prompt: VisionPrompt = VisionPrompt()
    quality_policy: QualityPolicy = QualityPolicy()

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise VisionProviderError(
                VisionFailure.CONFIGURATION,
                "no vision model is configured; refusing to guess one",
            )
        if self.max_tokens <= 0:
            raise VisionProviderError(VisionFailure.CONFIGURATION, "max_tokens must be positive")


class ClaudeScreenshotAnalyzer:
    """Reads a chart screenshot through Claude and returns typed observations.

    Satisfies the `ScreenshotAnalyzer` protocol. Holds no SDK type: the
    transport is injected, so the whole class is exercised in tests by a fake.
    """

    def __init__(self, transport: VisionTransport, config: AnalyzerConfig) -> None:
        self._transport = transport
        self._config = config

    async def analyse(
        self,
        payload: ScreenshotPayload,
        context: ScreenshotContext,
    ) -> ScreenshotAnalysis:
        """Analyse one screenshot. Raises `VisionProviderError` on any failure."""
        request = VisionRequest(
            system=self._config.prompt.system,
            user_message=self._config.prompt.user_message(
                slot=payload.asset.slot.value,
                expected_symbol=context.expected_symbol,
            ),
            image_bytes=payload.data,
            image_media_type=payload.media_type,
            max_tokens=self._config.max_tokens,
            model=self._config.model,
        )

        response = await self._transport.send(request)
        schema = _validate(response.text)
        extraction = schema.to_domain(payload.asset.screenshot_id)
        extraction = _stamp_provenance(
            extraction,
            model=response.usage.model or self._config.model,
        )

        return ScreenshotAnalysis(
            extraction=extraction,
            quality=self._score(extraction),
            warnings=_warnings(schema, payload),
        )

    def _score(self, extraction: VisionExtraction) -> ScreenshotQuality:
        """Deterministic Python, from what the model actually read (§7).

        A dimension is PRESENT only when the corresponding field was read.
        Everything else stays NOT_EVALUATED - including the dimensions no
        vision pass can judge, such as staleness, which needs a clock and a
        session calendar this project does not have.
        """
        read_fields = {item.field for item in extraction.directly_visible}
        unreadable_fields = {item.field for item in extraction.unreadable}

        states: dict[QualityDimension, DimensionState] = {}
        for field, dimension in _FIELD_TO_DIMENSION.items():
            if field in read_fields:
                states[dimension] = DimensionState.PRESENT
            elif field in unreadable_fields:
                states[dimension] = DimensionState.ABSENT

        if extraction.values or extraction.unreadable:
            # The model produced something, so the image was legible enough to
            # look at. An empty response says nothing either way.
            states[QualityDimension.READABLE] = (
                DimensionState.PRESENT if extraction.values else DimensionState.ABSENT
            )

        return score_quality(states, policy=self._config.quality_policy)


def _validate(text: str) -> VisionExtractionSchema:
    """Parse and validate, refusing anything that does not fit exactly."""
    stripped = text.strip()
    if not stripped:
        raise VisionProviderError(
            VisionFailure.INVALID_OUTPUT, "the vision provider returned an empty response"
        )

    try:
        # `parse_float=Decimal` hands the JSON module's own **lexical** token to
        # Decimal, so `{"value": 61.27}` becomes Decimal("61.27") exactly. The
        # default path would build a binary float first, and
        # Decimal(61.27) is 61.27000000000000312638803734444081783294677734375
        # - which would compare unequal to a structured Decimal("61.27") and
        # manufacture a conflict out of nothing but representation.
        #
        # `parse_int=Decimal` for the same reason and for consistency: a price
        # written `61` should travel the same road as `61.0`.
        parsed = json.loads(stripped, parse_float=Decimal, parse_int=Decimal)
    except json.JSONDecodeError as error:
        raise VisionProviderError(
            VisionFailure.INVALID_OUTPUT,
            "the vision response was not valid JSON and was not repaired",
        ) from error

    if not isinstance(parsed, dict):
        raise VisionProviderError(
            VisionFailure.INVALID_OUTPUT,
            f"the vision response was a {type(parsed).__name__}, not an object",
        )

    try:
        return VisionExtractionSchema.model_validate(parsed)
    except ValidationError as error:
        # The count is included; the content is not. A validation message can
        # echo the offending value, and that value came from a model reading a
        # user's screenshot.
        raise VisionProviderError(
            VisionFailure.INVALID_OUTPUT,
            f"the vision response failed schema validation with {error.error_count()} error(s)",
        ) from error


def _stamp_provenance(extraction: VisionExtraction, *, model: str) -> VisionExtraction:
    """Record which model produced this, for auditability (§5)."""
    from dataclasses import replace  # noqa: PLC0415

    return replace(extraction, model=model)


def _warnings(schema: VisionExtractionSchema, payload: ScreenshotPayload) -> tuple[str, ...]:
    """Non-fatal observations worth surfacing with the result.

    A detected timeframe that differs from the slot is *not* handled here -
    that is a typed mismatch owned by `SlotAssignment`, and reducing it to a
    warning string would lose the structure a user needs to correct it.
    """
    found: list[str] = []
    if not schema.values and not schema.unreadable:
        found.append("the vision pass reported no observations at all")
    if schema.detected_timeframe is None:
        found.append("no timeframe could be read from the chart")
    if payload.asset.media_type_was_misleading:
        found.append("the uploaded content type did not match the detected image format")
    return tuple(found)

"""Screenshot analysis endpoint (§16, §17).

The smallest useful Phase 6 surface:

    validated upload → ScreenshotAnalyzer → strict result
    → quality → mismatch checks → typed response

No persistence, no database table, no analysis lifecycle. §16 is explicit that
Phase 6 does not need them, and adding a table to host a transient screenshot
would be inventing a schema to justify itself.

## Errors

Every failure becomes a `ScreenshotErrorResponse` with a stable code and a
fixed public phrase. Nothing from a provider, an exception or an image reaches
the client - the mapping below never reads an exception's message, only its
*type*, which is what makes that guarantee hold rather than depend on
remembering to sanitise.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.dependencies import get_clock
from app.api.schemas.screenshots import (
    CorrectionRequestBody,
    CorrectionResponse,
    ObservationResponse,
    QualityDimensionResponse,
    ScreenshotAnalysisResponse,
    ScreenshotQualityResponse,
)
from app.application.ports.screenshot import ScreenshotAnalyzer
from app.application.ports.system import ClockPort
from app.application.vision.analysis import ScreenshotReview, review_screenshot
from app.application.vision.correction_workflow import CorrectionRequest, apply_correction
from app.application.vision.errors import VisionProviderError
from app.application.vision.intake import ImagePolicy, ScreenshotRejectedError, accept_and_verify
from app.application.vision.prompt import PROMPT_VERSION
from app.domain.vision.assets import ScreenshotId
from app.domain.vision.corrections import CorrectionType
from app.domain.vision.extraction import ObservedField
from app.domain.vision.precedence import ValueKind
from app.domain.vision.slots import ScreenshotSlot

router = APIRouter(prefix="/screenshots", tags=["screenshots"])

_MAX_READ_BYTES = ImagePolicy().max_bytes + 1
"""Read one byte past the limit so an oversized upload is detected without
holding an unbounded body in memory."""


def get_analyzer() -> ScreenshotAnalyzer:
    """Dependency seam for the analyser.

    Overridden in tests with a fake, and wired to the real adapter at the
    composition root. Left unimplemented here so no route can accidentally
    construct a provider client of its own.
    """
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "VISION_NOT_CONFIGURED",
            "detail": "Görüntü analizi servisi yapılandırılmamış.",
        },
    )


@router.post(
    "/analyse",
    response_model=ScreenshotAnalysisResponse,
    status_code=status.HTTP_200_OK,
)
async def analyse_screenshot(
    analyzer: Annotated[ScreenshotAnalyzer, Depends(get_analyzer)],
    slot: Annotated[ScreenshotSlot, Form()],
    file: Annotated[UploadFile, File()],
    expected_symbol: Annotated[str, Form()] = "",
) -> ScreenshotAnalysisResponse:
    """Analyse one chart screenshot.

    The upload goes through every Phase 6A/6B layer - size bound, header
    preflight, dimension policy and a real bounded decode - before the analyser
    is reached. A failure at any layer returns a typed error and the provider
    is never called.
    """
    data = await file.read(_MAX_READ_BYTES)

    try:
        accepted = accept_and_verify(
            data,
            slot,
            filename=file.filename,
            declared_media_type=file.content_type,
        )
    except ScreenshotRejectedError as error:
        # The code and message here are ours: `ScreenshotRejectedError` is
        # constructed with a fixed reason that never contains image content.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": error.code, "detail": error.message},
        ) from error

    try:
        review = await review_screenshot(analyzer, accepted, expected_symbol=expected_symbol)
    except VisionProviderError as error:
        raise HTTPException(
            status_code=_status_for(error),
            detail={"code": error.failure.value, "detail": error.public_detail},
        ) from error

    return _to_response(review)


@router.post(
    "/corrections",
    response_model=CorrectionResponse,
    status_code=status.HTTP_200_OK,
)
async def correct_observation(
    body: CorrectionRequestBody,
    clock: Annotated[ClockPort, Depends(get_clock)],
) -> CorrectionResponse:
    """Confirm, correct or reject one thing the vision pass reported (§12).

    Stateless: no screenshot is stored, so the caller submits the observation
    it is correcting. The original reading is preserved on the response and
    never overwritten, and the correction is timestamped from the clock port.

    **Nothing a client sends can choose its own trust rank.** Every field below
    comes from the request body and every one of them is user-correction data;
    `server_context` - the only route to `STRUCTURED_MARKET_DATA` - is left
    unset, because a request is not a trusted source and Phase 6 has no
    deterministic engine wired in here to be one.
    """
    try:
        request = CorrectionRequest(
            screenshot_id=ScreenshotId(body.screenshot_id),
            slot=ScreenshotSlot(body.slot),
            field=ObservedField(body.field),
            replayed_observation=body.replayed_observation,
            action=CorrectionType(body.action),
            corrected_value=body.corrected_value,
            note=body.note,
            value_kind=ValueKind.NUMERIC if body.numeric else ValueKind.CATEGORICAL,
            # Deliberately absent: server_context and origin keep their
            # untrusted defaults. A future phase that loads the observation
            # from a store sets them here, and only there.
        )
        outcome = apply_correction(request, clock)
    except ValueError as error:
        # Covers an unknown enum member and every `FieldCorrection` rule - a
        # CORRECTED with no replacement, a CONFIRMED that carries one, a
        # numeric field given prose. The message is ours, not a provider's.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_CORRECTION", "detail": str(error)},
        ) from error

    return CorrectionResponse(
        field=outcome.field.value,
        action=outcome.action.value,
        observed_screen_value=outcome.observed_screen_value,
        observed_value_origin=outcome.observed_value_origin.value,
        user_value=outcome.user_value,
        authoritative_value=outcome.authoritative_value,
        authoritative_source=(
            outcome.authoritative_source.name if outcome.authoritative_source else None
        ),
        user_input_was_overridden=outcome.user_input_was_overridden,
        conflicts=(
            tuple(item.describe for item in outcome.authority.conflicts)
            if outcome.authority is not None
            else ()
        ),
        agreeing=(
            tuple(f"{claim.source.name} said {claim.value}" for claim in outcome.authority.agreeing)
            if outcome.authority is not None
            else ()
        ),
        corrected_at=outcome.corrected_at.isoformat(),
    )


def _status_for(error: VisionProviderError) -> int:
    """HTTP status by failure *kind*, never by parsing a message."""
    if error.failure.value in {"AUTHENTICATION", "CONFIGURATION"}:
        return status.HTTP_503_SERVICE_UNAVAILABLE
    if error.failure.value == "RATE_LIMITED":
        return status.HTTP_429_TOO_MANY_REQUESTS
    if error.failure.value == "TIMEOUT":
        return status.HTTP_504_GATEWAY_TIMEOUT
    return status.HTTP_502_BAD_GATEWAY


def _to_response(review: ScreenshotReview) -> ScreenshotAnalysisResponse:
    """Project the internal review onto the public shape.

    An explicit projection rather than a dump: a field reaches a client only
    because it is named here.
    """
    asset = review.accepted.asset
    quality = review.quality
    detected = review.slot_assignment.detected

    return ScreenshotAnalysisResponse(
        screenshot_id=asset.identity,
        slot=asset.slot.value,
        image_format=asset.image_format,
        width=asset.width,
        height=asset.height,
        detected_timeframe=detected.value if detected is not None else None,
        timeframe_agreement=review.slot_assignment.agreement.value,
        mismatches=review.mismatches,
        observations=tuple(
            ObservationResponse(
                field=item.field.value,
                value=item.value,
                kind=item.kind.value,
                confidence=(str(item.confidence.value) if item.confidence is not None else None),
            )
            for item in review.extraction.values
        ),
        unreadable=tuple(
            f"{item.field.value}: {item.reason}" for item in review.extraction.unreadable
        ),
        quality=ScreenshotQualityResponse(
            score=quality.score,
            coverage=quality.coverage,
            evaluated_weight=quality.evaluated_weight,
            total_weight=quality.total_weight,
            method_version=quality.method_version,
            dimensions=tuple(
                QualityDimensionResponse(
                    dimension=item.dimension.value,
                    state=item.state.value,
                    awarded=item.awarded,
                    weight=item.weight,
                )
                for item in quality.dimensions
            ),
        ),
        warnings=review.analysis.warnings,
        model=review.extraction.model,
        prompt_version=PROMPT_VERSION,
    )

"""Public response shapes for screenshot analysis (§16, §17).

Everything here is what a client is allowed to see. That is a deliberate
subset: no provider payload, no stack trace, no internal reason string, no
image bytes, and no API credential.

The `ScreenshotAnalysisResponse` deliberately has **no field for a trade
action**. §20 puts LONG, SHORT, WAIT, NO TRADE and Bull/Bear synthesis in
Phase 7, and the way to keep them out of Phase 6 is to leave the API no place
to put them.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_PUBLIC = ConfigDict(extra="forbid", frozen=True)


class ObservationResponse(BaseModel):
    """One thing the vision pass reported seeing."""

    model_config = _PUBLIC

    field: str
    value: str
    kind: str
    """DIRECTLY_VISIBLE or VISUALLY_INFERRED - the epistemic category is part
    of the public answer, not an internal detail."""

    confidence: str | None = None
    """Rendered as a string so a client cannot mistake it for a float that has
    been rounded. ``null`` when the model reported none - never 0 or 1."""


class QualityDimensionResponse(BaseModel):
    model_config = _PUBLIC

    dimension: str
    state: str
    awarded: int | None
    weight: int


class ScreenshotQualityResponse(BaseModel):
    """The §39 score, or an explicit refusal to produce one."""

    model_config = _PUBLIC

    score: int | None
    """``null`` when too little was evaluated to make a total meaningful."""

    coverage: float
    evaluated_weight: int
    total_weight: int
    method_version: str
    dimensions: tuple[QualityDimensionResponse, ...] = ()


class ScreenshotAnalysisResponse(BaseModel):
    """What one analysed screenshot looks like to a client."""

    model_config = _PUBLIC

    screenshot_id: str
    slot: str
    image_format: str
    width: int
    height: int

    detected_timeframe: str | None
    timeframe_agreement: str
    mismatches: tuple[str, ...] = ()
    """Plain statements of every disagreement, so none is only implicit."""

    observations: tuple[ObservationResponse, ...] = ()
    unreadable: tuple[str, ...] = ()
    quality: ScreenshotQualityResponse
    warnings: tuple[str, ...] = ()
    model: str = ""
    prompt_version: str = ""


class CorrectionRequestBody(BaseModel):
    """A user's CONFIRM / CORRECT / REJECT of one observation (§12).

    **This schema carries user-correction fields and nothing else.** There is
    deliberately no way to state a source, a priority, a verification status or
    a structured market figure - not even indirectly.

    An earlier version had a `structured_value` field, and the workflow stamped
    whatever arrived in it as `STRUCTURED_MARKET_DATA`. Sending
    ``{"structured_value": "999"}`` returned 999 as authoritative validated
    market data. `extra="forbid"` had blocked the *label* while the *value that
    receives the label* walked straight through, which is the same escalation
    wearing a different hat.

    Trusted context now travels in `ServerAnalysisContext`, a type this schema
    cannot produce. The escalation is not rejected by a validator; it has no
    representation.
    """

    model_config = ConfigDict(extra="forbid")

    screenshot_id: str = Field(min_length=1, max_length=128)
    slot: str = Field(min_length=1, max_length=8)
    field: str = Field(min_length=1, max_length=64)
    replayed_observation: str = Field(min_length=1, max_length=512)
    """What the caller says the vision pass reported, echoed back from the
    analyse response. Named for what it is: Phase 6 stores no screenshot, so
    nothing server-side can confirm it, and it is treated as untrusted."""

    action: str = Field(min_length=1, max_length=16)
    corrected_value: str | None = Field(default=None, max_length=512)
    note: str = Field(default="", max_length=512)
    numeric: bool = False
    """Whether this field's semantics are numeric, so equal values written
    differently are compared as exact decimals rather than as text."""


class CorrectionResponse(BaseModel):
    """Both answers, kept apart on purpose.

    ``observed_screen_value`` is what the picture shows. ``authoritative_value``
    is what a deterministic engine may use. A user may confirm the first while
    the second stays a structured figure - that is §13 working, not a bug.
    """

    model_config = _PUBLIC

    field: str
    action: str
    observed_screen_value: str
    observed_value_origin: str
    """``CLIENT_REPLAYED_UNVERIFIED`` in Phase 6: the caller supplied this and
    nothing server-side confirmed it. Returned explicitly so no consumer reads
    it as a verified reading."""

    user_value: str | None
    authoritative_value: str | None
    authoritative_source: str | None
    user_input_was_overridden: bool
    conflicts: tuple[str, ...] = ()
    agreeing: tuple[str, ...] = ()
    corrected_at: str


class ScreenshotErrorResponse(BaseModel):
    """A typed public failure.

    ``code`` is stable and machine-readable; ``detail`` is a fixed phrase per
    code. Neither is derived from a provider message or an exception string,
    which is what stops internals leaking through the error path.
    """

    model_config = _PUBLIC

    code: str = Field(min_length=1)
    detail: str = Field(min_length=1)

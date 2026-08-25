"""The strict contract a vision response must satisfy (§14, §68).

This is the boundary where **untrusted model output** becomes a typed domain
value. Everything about it is deliberately unforgiving:

* ``extra="forbid"`` - an unexpected field is a contract violation, not
  something to ignore. A model that invented a key has not answered the
  question asked.
* every field is explicitly typed and bounded; there is no `dict[str, Any]`
  anywhere, because §14 rules it out and because a loosely typed AI response
  is how unvalidated output reaches application state.
* confidence is bounded to 0-1 at parse time, so an out-of-range value is
  rejected here rather than discovered downstream.
* timestamps must be timezone-aware.

Pydantic lives **only** at this boundary. The domain has no idea it exists -
contract 1 forbids importing it there - so `to_domain` converts once, at the
edge, and everything past this point is plain frozen dataclasses.

Phase 6A defines the contract and the conversion. It does **not** call a model:
there is no adapter, no prompt and no network client in this phase.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.common.enums import Timeframe
from app.domain.vision.assets import ScreenshotId
from app.domain.vision.extraction import (
    ExtractedValue,
    ObservationKind,
    ObservedField,
    UnreadableField,
    VisionConfidence,
    VisionExtraction,
)
from app.domain.vision.slots import ScreenshotSlot

_STRICT = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ExtractedValueSchema(BaseModel):
    """One observation, as the model must report it.

    Mirrors §38's example - field, value, source, confidence - with ``source``
    expressed as `ObservationKind` so the model must state whether it *read*
    the value or *judged* it. That is the distinction §10 of this phase
    requires, and asking for it explicitly is the only way to get it.
    """

    model_config = _STRICT

    field: ObservedField
    value: str = Field(min_length=1, max_length=512)
    """Carried as text, because most observations are text - "yükseliş", a
    timeframe label, an indicator caption.

    A model asked for a price may nonetheless answer with a JSON *number*:
    ``{"value": 61.27}`` rather than ``{"value": "61.27"}``. Both are valid
    JSON and both are the same observation, so refusing the first would throw
    away an entire screenshot analysis over a quoting style. It is accepted -
    but only through the exact path: the response is parsed with
    ``parse_float=Decimal`` (see `claude_analyzer`), so what arrives here is
    the **lexical** value the model wrote, and `_value_as_exact_text` renders
    it without ever constructing a float.

    A bare `float` is refused rather than converted. By the time a float
    exists the damage is done - `Decimal(61.27)` is
    ``61.27000000000000312638803734444081783294677734375`` - and silently
    stringifying it would hide that the exact path was bypassed.
    """

    kind: ObservationKind
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    """Optional on purpose. A model that omits it has not claimed certainty,
    and `None` is preserved all the way into the domain rather than defaulted
    to 0 or 1."""

    timeframe: Timeframe | None = None
    note: str = Field(default="", max_length=512)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence_never_via_float(cls, value: object) -> object:
        """A confidence is a `Decimal`, and must not arrive through a float.

        Same reasoning as `value`: the response is parsed with
        ``parse_float=Decimal``, so a JSON number reaches this validator as an
        exact lexical Decimal. Refusing `float` keeps that the only way in,
        rather than trusting every future caller to remember.
        """
        if isinstance(value, float):
            raise ValueError(
                "a floating-point confidence is refused: parse the response "
                "with parse_float=Decimal"
            )
        return value

    @field_validator("value", mode="before")
    @classmethod
    def _value_as_exact_text(cls, value: object) -> object:
        """Render a JSON number as its exact decimal text.

        ``format(d, "f")`` is used rather than `str` so an exponent form never
        appears: `Decimal("1E+2")` would otherwise become ``"1E+2"`` and stop
        comparing equal to ``"100"`` as text. Trailing zeros are **kept** -
        61.270 stays "61.270" - because §D requires the source's original
        representation to survive for audit. Canonicalisation for *comparison*
        happens later, in `SourcedValue.comparable`.
        """
        if isinstance(value, bool):
            # bool is an int subclass; a boolean is not an observation.
            raise ValueError("an observation value must not be a boolean")
        if isinstance(value, float):
            raise ValueError(
                "a floating-point observation value is refused: parse the "
                "response with parse_float=Decimal so the exact lexical value "
                "is preserved"
            )
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError("an observation value must be a finite number")
            return format(value, "f")
        if isinstance(value, int):
            return str(value)
        return value

    @field_validator("value")
    @classmethod
    def _value_must_say_something(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("an observation must carry a value")
        return value


class UnreadableFieldSchema(BaseModel):
    """Something the model looked for and could not read."""

    model_config = _STRICT

    field: ObservedField
    reason: str = Field(min_length=1, max_length=512)


class VisionExtractionSchema(BaseModel):
    """The whole response for one screenshot.

    The screenshot identity is **not** taken from the model: it is supplied by
    the caller in `to_domain`. A model cannot be allowed to state which image
    it was looking at - that would let a hallucinated id attach observations to
    the wrong chart.
    """

    model_config = _STRICT

    slot: ScreenshotSlot
    detected_timeframe: Timeframe | None = None
    """What the model read off the chart. Compared against the slot by
    `SlotAssignment`; never used to reassign the slot."""

    values: tuple[ExtractedValueSchema, ...] = ()
    unreadable: tuple[UnreadableFieldSchema, ...] = ()
    observed_at: datetime | None = None
    model: str = Field(default="", max_length=128)

    @field_validator("observed_at")
    @classmethod
    def _must_be_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        return value

    def to_domain(self, screenshot_id: ScreenshotId) -> VisionExtraction:
        """Convert into domain values, binding to a caller-supplied identity.

        The single crossing point from Pydantic to the domain. Past here
        nothing knows what a `BaseModel` is.
        """
        return VisionExtraction(
            screenshot_id=screenshot_id,
            slot=self.slot,
            values=tuple(
                ExtractedValue(
                    field=item.field,
                    value=item.value,
                    kind=item.kind,
                    screenshot_id=screenshot_id,
                    slot=self.slot,
                    confidence=(
                        VisionConfidence(value=item.confidence)
                        if item.confidence is not None
                        else None
                    ),
                    timeframe=item.timeframe,
                    note=item.note,
                )
                for item in self.values
            ),
            unreadable=tuple(
                UnreadableField(field=item.field, reason=item.reason) for item in self.unreadable
            ),
            observed_at=self.observed_at,
            model=self.model,
        )

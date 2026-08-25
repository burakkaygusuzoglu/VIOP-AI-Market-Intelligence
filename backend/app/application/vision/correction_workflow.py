"""The correction workflow as a use case a consumer can actually reach (§12).

Phase 6 requires CONFIRM / CORRECT / REJECT. The pieces existed - `FieldCorrection`
in the domain, `record_correction` in `analysis` - but nothing in `app/` called
them, so the workflow was reachable only from a test. This module is the entry
point that closes that gap, and `POST /screenshots/corrections` is its edge.

## The trust boundary

This is the module's most important property, so it is stated before anything
else: **external input may supply a value, but it may never choose that value's
trust rank.**

An earlier version of this endpoint broke that. The public schema had a
`structured_value` field, and this module stamped whatever arrived in it with
`DataSourcePriority.STRUCTURED_MARKET_DATA`. The client never sent the *label* -
`extra="forbid"` refused that - but it did not need to: sending

    {"structured_value": "999"}

came back as `authoritative_value: "999"`, `authoritative_source:
"STRUCTURED_MARKET_DATA"`. A number a hostile caller typed was presented as
validated market data. Self-assigning the label and self-assigning the value
that gets the label are the same escalation.

The boundary is now structural rather than remembered:

* `CorrectionRequest` splits its inputs into `replayed_observation` (whatever
  the client says it is correcting) and `server_context` (what trusted
  server-side code knows).
* `ServerAnalysisContext` can only be constructed by server code. The public
  request schema has no field that produces one, so no request can create one -
  it is not validated away, it is unreachable.
* Everything from the client enters at the **weakest** rank the project has.

## Why it is stateless, and what that costs

§16 is explicit that Phase 6 adds no persistence, and a screenshot analysis is
transient. There is nothing to look a correction up *against*: the caller
submits the observation it is correcting, having received it from
`POST /screenshots/analyse` moments earlier.

**That replayed observation is untrusted context, and is treated as such.** This
module cannot prove it came from a server Vision result - a client may echo back
a value no model ever produced - so it does not manufacture that provenance. It
does not enter precedence as "the screenshot said this"; it enters as
`AI_VISUAL_INFERENCE`, the weakest rank, and the outcome reports its origin as
`CLIENT_REPLAYED_UNVERIFIED` so no reader mistakes it for a server-verified
reading.

When a phase introduces a screenshot store, this use case should load the
observation by `screenshot_id`, `replayed_observation` should disappear from the
request schema, and the origin becomes `SERVER_VISION_RESULT`. The trust split
below is already shaped for that; nothing else needs to change.

What it guarantees today:

* the original reading is preserved on the result, never overwritten;
* the user's judgement enters as `USER_CONFIRMED` - above anything a screenshot
  or a model produced, still below validated structured market data;
* nothing a client sends can reach `STRUCTURED_MARKET_DATA` or
  `VALIDATED_CONTRACT_METADATA`;
* the timestamp comes from a `ClockPort`;
* OBSERVED SCREEN VALUE and CALCULATION AUTHORITY are returned as two separate
  answers, because they are two separate questions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique

from app.application.ports.system import ClockPort
from app.domain.common.enums import DataSourcePriority
from app.domain.vision.assets import ScreenshotId
from app.domain.vision.corrections import CorrectionLog, CorrectionType, FieldCorrection
from app.domain.vision.extraction import ExtractedValue, ObservationKind, ObservedField
from app.domain.vision.precedence import ResolvedValue, SourcedValue, ValueKind, resolve
from app.domain.vision.slots import ScreenshotSlot


@unique
class ObservationOrigin(StrEnum):
    """Where the observation being corrected actually came from.

    A typed distinction rather than a comment, because the difference decides
    whether the value may be described as verified.
    """

    CLIENT_REPLAYED_UNVERIFIED = "CLIENT_REPLAYED_UNVERIFIED"
    """The caller says this is what the vision pass reported. Phase 6 stores no
    screenshot, so nothing can confirm that. Treated as untrusted context."""

    SERVER_VISION_RESULT = "SERVER_VISION_RESULT"
    """Loaded server-side from a real analysis. No Phase 6 path produces this;
    it exists so a persistence phase has somewhere honest to put it, and so
    this distinction is visible now rather than retrofitted later."""


@dataclass(frozen=True, slots=True)
class ServerAnalysisContext:
    """What trusted server-side code knows about this field.

    **Only server code constructs this.** No field of the public request schema
    produces one, which is what stops a client reaching `STRUCTURED_MARKET_DATA`
    - the escalation is not blocked by validation, it is unrepresentable.

    Phase 6 has no deterministic engine wired into the corrections endpoint, so
    the route passes ``None``. The parameter exists because the alternative -
    accepting the structured figure from the request - is exactly the defect
    this type was introduced to remove.
    """

    structured_value: str | None = None
    """The deterministic engine's own figure, when the server has one.

    Supplying it is what makes a user-confirmed screen reading lose. Not
    supplying it does **not** promote anything to market truth; it means no
    structured claim was offered.
    """


@dataclass(frozen=True, slots=True)
class CorrectionRequest:
    """One user judgement about one observation.

    The fields divide by trust, and the division is the point: everything above
    `server_context` came from outside and is treated accordingly.
    """

    screenshot_id: ScreenshotId
    """The wrapper type, not a bare string: `ScreenshotId` exists so a
    filename, a digest or a user input cannot arrive where an identity is
    expected, and this use case is reached straight from user input."""

    slot: ScreenshotSlot
    field: ObservedField
    replayed_observation: str
    """What the caller says the vision pass reported.

    Named for what it is. Nothing here can verify it, so it enters precedence
    at the weakest rank and is reported as `CLIENT_REPLAYED_UNVERIFIED`.
    """

    action: CorrectionType
    corrected_value: str | None = None
    note: str = ""
    value_kind: ValueKind = ValueKind.CATEGORICAL
    """Whether this field's semantics are numeric. Categorical by default: a
    screenshot reading is text until a caller says otherwise."""

    server_context: ServerAnalysisContext | None = None
    """Trusted server-side knowledge. Never populated from a request body."""

    origin: ObservationOrigin = ObservationOrigin.CLIENT_REPLAYED_UNVERIFIED
    """Defaults to untrusted. A caller that genuinely loaded the observation
    server-side sets it; a route handling a request cannot."""


@dataclass(frozen=True, slots=True)
class CorrectionOutcome:
    """The two questions, answered separately and both returned."""

    field: ObservedField
    action: CorrectionType
    observed_screen_value: str
    """Always what the picture showed. A correction never edits this."""

    user_value: str | None
    """What the user says the field is. ``None`` for a rejection - the reading
    is wrong and nothing replaces it, so the field is unknown rather than
    reverted to the model's answer."""

    corrected_at: datetime
    authority: ResolvedValue | None
    """The precedence outcome: what a deterministic engine may use. ``None``
    when a rejection leaves no claim at all to rank."""

    log: CorrectionLog
    observed_value_origin: ObservationOrigin = ObservationOrigin.CLIENT_REPLAYED_UNVERIFIED
    """Whether `observed_screen_value` is a server-verified reading or a value
    the caller replayed. Returned so nothing downstream has to assume."""

    @property
    def authoritative_value(self) -> str | None:
        return self.authority.value if self.authority is not None else None

    @property
    def authoritative_source(self) -> DataSourcePriority | None:
        return self.authority.source if self.authority is not None else None

    @property
    def user_input_was_overridden(self) -> bool:
        """True when the user's claim did not win the ranking.

        Worth surfacing plainly: a user who corrects a reading and then sees a
        different number used deserves to be told that structured market data
        outranked their correction, rather than left to infer it.
        """
        if self.authority is None:
            return False
        return self.authority.source is not DataSourcePriority.USER_CONFIRMED


def apply_correction(request: CorrectionRequest, clock: ClockPort) -> CorrectionOutcome:
    """Record a CONFIRM / CORRECT / REJECT and resolve the field afterwards.

    Raises `ValueError` for an incoherent request - a CORRECTED with no
    replacement value, or a CONFIRMED that carries one - via `FieldCorrection`,
    which owns those rules.
    """
    original = ExtractedValue(
        field=request.field,
        value=request.replayed_observation,
        # Always the weakest kind. The caller does not get to say whether the
        # value it replayed was "directly visible" - that would let it pick its
        # own precedence rank, one notch at a time, for data nothing verified.
        kind=ObservationKind.VISUALLY_INFERRED,
        screenshot_id=request.screenshot_id,
        slot=request.slot,
        confidence=None,
    )

    correction = FieldCorrection(
        original=original,
        correction_type=request.action,
        corrected_value=request.corrected_value,
        corrected_at=clock.now(),
        note=request.note,
    )
    log = CorrectionLog().with_correction(correction)

    return CorrectionOutcome(
        field=request.field,
        action=request.action,
        observed_screen_value=original.value,
        user_value=correction.effective_value,
        corrected_at=correction.corrected_at,
        authority=_authority(request, original, correction),
        log=log,
        observed_value_origin=request.origin,
    )


def _authority(
    request: CorrectionRequest,
    original: ExtractedValue,
    correction: FieldCorrection,
) -> ResolvedValue | None:
    """Rank every claim about the field and let precedence decide.

    The candidate list is built weakest-first and `resolve` orders it, so §13's
    hierarchy is applied rather than restated here.
    """
    candidates: list[SourcedValue] = []

    if correction.correction_type is not CorrectionType.REJECTED:
        # The screenshot's own reading still competes, at its own rank. A
        # rejected observation does not: the user has said it is wrong.
        candidates.append(SourcedValue.from_observation(original, request.value_kind))

    claim = correction.as_sourced_value(request.value_kind)
    if claim is not None:
        candidates.append(claim)

    # The ONLY path to STRUCTURED_MARKET_DATA in this module, and it reads from
    # `server_context` - a type no request body can produce.
    context = request.server_context
    structured = (context.structured_value if context is not None else "") or ""
    if structured.strip():
        candidates.append(
            SourcedValue(
                field=request.field.value,
                value=structured.strip(),
                source=DataSourcePriority.STRUCTURED_MARKET_DATA,
                kind=request.value_kind,
                detail="validated structured market data",
            )
        )

    if not candidates:
        return None
    return resolve(tuple(candidates))


__all__ = [
    "CorrectionOutcome",
    "CorrectionRequest",
    "ObservationOrigin",
    "ServerAnalysisContext",
    "apply_correction",
]

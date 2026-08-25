"""The correction workflow (§39, §10-§12 of the Phase 6B brief).

A vision model misreads charts. The product answer is not a better model, it is
letting the user say so - and recording both what the model said and what the
user said, so the disagreement is auditable afterwards.

Three rules shape the whole module.

**The original is never erased.** A `FieldCorrection` holds the observation it
corrects. "The screenshot visibly shows 63.2" stays true and inspectable even
after a user corrects the reading to 61.3, because those are answers to
different questions.

**A correction changes authority, not arithmetic.** Confirming or correcting a
field lifts it to `USER_CONFIRMED`, which outranks both screenshot extraction
and visual inference. It does **not** outrank validated structured market data:
§1's hierarchy puts structured data first, and a user confirming what a picture
shows has not measured the market. `corrected_sourced_value` therefore produces
a `USER_CONFIRMED` claim and lets `precedence.resolve` do the ranking - there is
no path here that promotes anything above structured data.

**Time comes from a clock port.** §8 of the brief and CLAUDE.md both forbid
ambient `datetime.now()` in deterministic logic, so a correction is stamped
with a caller-supplied instant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique

from app.domain.common.enums import DataSourcePriority
from app.domain.vision.extraction import ExtractedValue, ObservedField
from app.domain.vision.precedence import SourcedValue, ValueKind


@unique
class CorrectionType(StrEnum):
    """What the user did about an observation."""

    CONFIRMED = "CONFIRMED"
    """The model read it correctly. The value is unchanged but its authority
    rises: a human has now vouched for it."""

    CORRECTED = "CORRECTED"
    """The model read it wrongly and the user supplied the right value."""

    REJECTED = "REJECTED"
    """The observation is wrong and the user has no replacement. The field
    becomes unavailable rather than reverting to the model's reading - a
    rejected value is not a usable value."""


@dataclass(frozen=True, slots=True)
class FieldCorrection:
    """One user judgement about one observation, with both values kept."""

    original: ExtractedValue
    correction_type: CorrectionType
    corrected_value: str | None
    corrected_at: datetime
    note: str = ""

    def __post_init__(self) -> None:
        if self.corrected_at.tzinfo is None:
            raise ValueError("corrected_at must be timezone-aware")

        if self.correction_type is CorrectionType.CORRECTED:
            if not (self.corrected_value or "").strip():
                raise ValueError(
                    f"correcting {self.original.field.value} requires a replacement value"
                )
        elif self.corrected_value is not None:
            raise ValueError(
                f"a {self.correction_type.value} correction of "
                f"{self.original.field.value} must not carry a replacement value"
            )

    @property
    def field(self) -> ObservedField:
        return self.original.field

    @property
    def effective_value(self) -> str | None:
        """What the user says the field is.

        ``None`` for a rejection: the observation is wrong and nothing
        replaces it, so the field is unknown rather than reverted.
        """
        if self.correction_type is CorrectionType.REJECTED:
            return None
        if self.correction_type is CorrectionType.CORRECTED:
            return self.corrected_value
        return self.original.value

    @property
    def changed_the_value(self) -> bool:
        return self.correction_type is CorrectionType.CORRECTED

    @property
    def original_value(self) -> str:
        """What the model said, always available whatever the user did."""
        return self.original.value

    def as_sourced_value(self, kind: ValueKind = ValueKind.CATEGORICAL) -> SourcedValue | None:
        """The user's judgement as a precedence candidate.

        `USER_CONFIRMED` outranks both screenshot ranks and is outranked by
        structured market data - exactly as §1 orders them. This method does
        not decide anything; it hands a ranked claim to `precedence.resolve`.

        ``None`` for a rejection, because there is no claim to rank.
        """
        value = self.effective_value
        if value is None:
            return None
        return SourcedValue(
            field=self.field.value,
            value=value,
            source=DataSourcePriority.USER_CONFIRMED,
            kind=kind,
            detail=self.note or f"user {self.correction_type.value.lower()}",
        )


@dataclass(frozen=True, slots=True)
class CorrectionLog:
    """Every correction made against one extraction, in order.

    Append-only by construction: `with_correction` returns a new log rather
    than mutating, so an audit trail cannot be quietly rewritten.
    """

    corrections: tuple[FieldCorrection, ...] = ()

    def with_correction(self, correction: FieldCorrection) -> CorrectionLog:
        return CorrectionLog(corrections=(*self.corrections, correction))

    def for_field(self, field: ObservedField) -> tuple[FieldCorrection, ...]:
        return tuple(item for item in self.corrections if item.field is field)

    def latest_for(self, field: ObservedField) -> FieldCorrection | None:
        """The most recent judgement about a field.

        Later corrections supersede earlier ones for *authority*; the earlier
        ones remain in the log, because how a user changed their mind is part
        of the audit trail.
        """
        found = self.for_field(field)
        return found[-1] if found else None

    @property
    def corrected_fields(self) -> tuple[ObservedField, ...]:
        seen: list[ObservedField] = []
        for item in self.corrections:
            if item.field not in seen:
                seen.append(item.field)
        return tuple(seen)

    @property
    def rejected_fields(self) -> tuple[ObservedField, ...]:
        return tuple(
            field
            for field in self.corrected_fields
            if (latest := self.latest_for(field)) is not None
            and latest.correction_type is CorrectionType.REJECTED
        )

    def candidates(
        self, kinds: dict[ObservedField, ValueKind] | None = None
    ) -> tuple[SourcedValue, ...]:
        """User-confirmed claims, one per corrected field.

        Only the latest judgement per field becomes a candidate; superseded
        ones stay in the log but do not compete.
        """
        semantics = kinds or {}
        found: list[SourcedValue] = []
        for field in self.corrected_fields:
            latest = self.latest_for(field)
            if latest is None:  # pragma: no cover - corrected_fields guarantees one
                continue
            claim = latest.as_sourced_value(semantics.get(field, ValueKind.CATEGORICAL))
            if claim is not None:
                found.append(claim)
        return tuple(found)

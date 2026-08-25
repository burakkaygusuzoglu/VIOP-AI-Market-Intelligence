"""Screenshot slots and the three timeframes that must not be conflated (§38).

§38 names four preferred slots - 1D, 1H, 15M, 5M - matching the Phase 4 role
hierarchy. A slot says *which chart the user meant to upload*.

The module exists because there are **three** different timeframes in play and
collapsing any two of them loses information a user needs:

    EXPECTED    the slot the upload was filed under
    DETECTED    what a vision model read off the chart
    CONFIRMED   what the user said it actually is

A 1H screenshot filed as 15M is a real and common mistake. Accepting it
silently would make every downstream reading wrong while looking perfectly
normal, and *auto-correcting* it silently would be worse - it would decide, on
a model's say-so, that the user meant something other than what they said.

So a mismatch is neither accepted nor repaired. It becomes a typed
`TimeframeAgreement` that stays visible until a human resolves it, which is the
§13 conflict-preservation rule applied to the very first field.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from app.domain.common.enums import Timeframe


@unique
class ScreenshotSlot(StrEnum):
    """The four §38 slots.

    Deliberately a separate type from `Timeframe`: a slot is a *place in the
    upload form*, and the whole point of this module is that the place and the
    chart can disagree.
    """

    D1 = "1D"
    H1 = "1H"
    M15 = "15M"
    M5 = "5M"

    @property
    def timeframe(self) -> Timeframe:
        """The timeframe this slot is asking for."""
        return _SLOT_TIMEFRAME[self]

    @classmethod
    def for_timeframe(cls, timeframe: Timeframe) -> ScreenshotSlot | None:
        """The slot that expects ``timeframe``, if any.

        ``None`` for a supported timeframe with no slot - 4H and 30M exist in
        `Timeframe` but §38 lists no screenshot slot for them, and inventing
        one would accept an upload the product never asked for.
        """
        for slot in cls:
            if _SLOT_TIMEFRAME[slot] is timeframe:
                return slot
        return None


_SLOT_TIMEFRAME: dict[ScreenshotSlot, Timeframe] = {
    ScreenshotSlot.D1: Timeframe.D1,
    ScreenshotSlot.H1: Timeframe.H1,
    ScreenshotSlot.M15: Timeframe.M15,
    ScreenshotSlot.M5: Timeframe.M5,
}

SLOTS_BROADEST_FIRST: tuple[ScreenshotSlot, ...] = (
    ScreenshotSlot.D1,
    ScreenshotSlot.H1,
    ScreenshotSlot.M15,
    ScreenshotSlot.M5,
)


@unique
class TimeframeAgreement(StrEnum):
    """Whether the slot, the detection and the user agree."""

    AGREES = "AGREES"
    """Everything available points at the same timeframe."""

    MISMATCH = "MISMATCH"
    """Two available sources disagree. Never auto-corrected, never ignored -
    it stays until a human resolves it."""

    UNDETECTED = "UNDETECTED"
    """No detection has been made yet. Not agreement: nobody has looked."""

    UNSUPPORTED_TIMEFRAME = "UNSUPPORTED_TIMEFRAME"
    """A timeframe was detected that no §38 slot covers - a 4H chart uploaded
    into a four-slot form. Real information, and not a mismatch of the same
    kind."""


@dataclass(frozen=True, slots=True)
class SlotAssignment:
    """One slot, and every timeframe claim made about it.

    ``detected`` and ``confirmed`` are both optional and both meaningful when
    absent: nothing has read the chart yet, and nobody has confirmed it yet.
    Neither absence is filled in with the expected value, because that would
    manufacture the agreement this type exists to test for.
    """

    slot: ScreenshotSlot
    detected: Timeframe | None = None
    confirmed: Timeframe | None = None

    @property
    def expected(self) -> Timeframe:
        """What the slot asked for."""
        return self.slot.timeframe

    @property
    def agreement(self) -> TimeframeAgreement:
        """How the available claims relate.

        The user's confirmation is checked first: when a human has stated what
        the chart is, that statement is what the expectation is measured
        against. A model's reading never overrules it.
        """
        if self.confirmed is not None:
            return (
                TimeframeAgreement.AGREES
                if self.confirmed is self.expected
                else TimeframeAgreement.MISMATCH
            )
        if self.detected is None:
            return TimeframeAgreement.UNDETECTED
        if ScreenshotSlot.for_timeframe(self.detected) is None:
            return TimeframeAgreement.UNSUPPORTED_TIMEFRAME
        return (
            TimeframeAgreement.AGREES
            if self.detected is self.expected
            else TimeframeAgreement.MISMATCH
        )

    @property
    def is_mismatched(self) -> bool:
        return self.agreement in (
            TimeframeAgreement.MISMATCH,
            TimeframeAgreement.UNSUPPORTED_TIMEFRAME,
        )

    @property
    def effective_timeframe(self) -> Timeframe | None:
        """The timeframe a consumer may rely on, if any.

        ``None`` whenever anything disagrees. There is deliberately no
        "best guess" here: a disputed chart has no usable timeframe until the
        dispute is settled, and returning the expected value anyway is exactly
        the silent auto-correction §2 of this phase forbids.
        """
        if self.is_mismatched:
            return None
        if self.confirmed is not None:
            return self.confirmed
        if self.detected is not None:
            return self.detected
        return None

    def with_detection(self, timeframe: Timeframe) -> SlotAssignment:
        """Record what a vision model read. Never changes ``slot``."""
        return SlotAssignment(slot=self.slot, detected=timeframe, confirmed=self.confirmed)

    def with_confirmation(self, timeframe: Timeframe) -> SlotAssignment:
        """Record what the user says the chart is. Never changes ``slot``.

        A confirmation resolves the *authority* question without erasing the
        detection, so a disagreement between the two remains inspectable.
        """
        return SlotAssignment(slot=self.slot, detected=self.detected, confirmed=timeframe)

    @property
    def describe_mismatch(self) -> str:
        """A plain statement of the disagreement, or an empty string."""
        if not self.is_mismatched:
            return ""
        claimed = self.confirmed or self.detected
        source = "user-confirmed" if self.confirmed is not None else "vision-detected"
        return (
            f"slot {self.slot.value} expects {self.expected.value} but the "
            f"{source} timeframe is {claimed.value if claimed else 'unknown'}"
        )

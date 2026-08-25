"""The screenshot analysis port (§15, §69, §73).

## Why a second port rather than reusing `AIProvider`

`AIProvider` was audited first, as §0 requires. It is a good general port -
structured output, schema-validated, vendor-free - and a vision adapter will
almost certainly *use* one internally. But it is the wrong shape for a caller:

* it takes a `system_prompt`, a `system_prompt_version` and `max_tokens`,
  which are prompt-engineering concerns a use case should not have to hold;
* it takes `ImageInput` with a raw media type, leaving the caller to know
  which formats are acceptable and to have validated them;
* its return type is parameterised by whatever schema the caller passes, so
  nothing stops two call sites asking for two different shapes of vision
  result.

`ScreenshotAnalyzer` narrows all three away. A caller supplies a validated
asset, the bytes, and the context that matters analytically; it gets domain
values back. Prompts, models, retries and token budgets belong to the adapter.

The port stays vendor-free by construction: **no Anthropic type, no SDK class
and no dictionary appears in its signature**, and a test enforces that.

## Phase 6A defines it and stops

There is no implementation here. Writing one would mean writing prompts and
making network calls, which §1 places in 6B.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.application.vision.intake import AcceptedScreenshot
from app.domain.vision.assets import ScreenshotAsset
from app.domain.vision.extraction import VisionExtraction
from app.domain.vision.quality import ScreenshotQuality


@dataclass(frozen=True, slots=True)
class ScreenshotPayload:
    """A verified screenshot together with the bytes safe to send.

    The bytes travel here rather than on `ScreenshotAsset` so they stay inside
    the call that needs them: a long-lived domain value carrying megabytes
    would be copied into every structure that referenced it.

    ``data`` is the **post-verification payload**, which is deliberately not
    required to equal `asset.byte_size`. Phase 6B normalises a verified image
    by re-encoding it to a static PNG before it leaves the process, so the
    payload is usually a different size - and often a different format - than
    what the user uploaded. `asset.byte_size` records the upload; ``data`` and
    ``media_type`` record what is actually being sent.

    Build one with `from_accepted` rather than by hand: that path is the one
    that has been through a real decode.
    """

    asset: ScreenshotAsset
    data: bytes
    media_type: str

    def __post_init__(self) -> None:
        if not self.data:
            raise ValueError("a screenshot payload cannot be empty")
        if not self.media_type.strip():
            raise ValueError("a screenshot payload must state what it is")

    @classmethod
    def from_accepted(cls, accepted: AcceptedScreenshot) -> ScreenshotPayload:
        """The only construction path that guarantees a real decode ran.

        `AcceptedScreenshot` is produced solely by `accept_and_verify`, so a
        payload built this way cannot hold bytes that were never decoded.
        """
        return cls(
            asset=accepted.asset,
            data=accepted.payload,
            media_type=accepted.payload_media_type,
        )


@dataclass(frozen=True, slots=True)
class ScreenshotContext:
    """What the analyser may be told about the chart.

    Deliberately thin. An expected symbol helps a model confirm what it is
    looking at, and the caller already knows the slot from the asset. Nothing
    here carries an opinion about the market, because supplying one would
    invite the model to agree with it.
    """

    expected_symbol: str = ""
    locale: str = "tr"
    """Which language the model should answer free-text notes in. Turkish by
    default, matching §4."""


@dataclass(frozen=True, slots=True)
class ScreenshotAnalysis:
    """What one analysis pass produced.

    Quality and extraction travel together because §39 requires the quality
    judgement to be available *before* the extraction is relied on - and
    because a poor screenshot is exactly the case where an extraction needs
    its caveat attached.
    """

    extraction: VisionExtraction
    quality: ScreenshotQuality
    warnings: tuple[str, ...] = field(default_factory=tuple)


class ScreenshotAnalyzer(Protocol):
    """Reads a chart screenshot and reports what it observed.

    Implementations must:

    * return **only** schema-validated output - invalid model responses raise
      rather than returning partial data (§68);
    * never compute an indicator, a price or a size. Vision observes what is
      drawn; §16 of this phase forbids it becoming a numeric authority;
    * never decide a trade action. There is no path from this port to LONG,
      SHORT or WAIT.
    """

    async def analyse(
        self,
        payload: ScreenshotPayload,
        context: ScreenshotContext,
    ) -> ScreenshotAnalysis:
        """Analyse one screenshot and return typed observations.

        Raises rather than returning a partially valid result: unvalidated AI
        output must never reach application state.
        """
        ...

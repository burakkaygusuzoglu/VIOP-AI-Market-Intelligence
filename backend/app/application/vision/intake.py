"""Accepting an upload safely (master spec §38, §98).

Image handling is the most security-sensitive surface this project has: the
bytes are attacker-controlled, the format is attacker-declared, and the
filename is attacker-written. Every one of those is treated as untrusted here.

What this module refuses, and why each matters:

* **empty uploads** - a zero-byte file is not an image, and letting one through
  produces a confusing failure much later;
* **oversized uploads** - bounded before anything else, so a hostile payload
  cannot be parsed at all;
* **declared type and extension** - both are read for auditing and **neither is
  believed**; the format comes from the content;
* **malformed or truncated files** - rejected by the header parser, which
  raises rather than guessing;
* **decompression bombs** - caught by comparing *declared* dimensions against a
  policy limit, without ever allocating a pixel buffer;
* **unsafe filenames** - reduced to display-only text, never used as a path or
  as identity.

Everything here is **bounded in-memory**. No temporary file is written, so
there is nothing to clean up, nothing to leak and no path to traverse.

## The full pipeline (Phase 6B, review A)

Phase 6A stopped at the header. The human review required a real decode before
any bytes leave the process, so `accept_screenshot` now runs all four layers,
cheapest first, and `decode.py` owns the last one:

    size bound → header preflight → dimension policy → bounded real decode

`accept_screenshot` performs the first three and is still available on its own
for a cheap pre-check. `accept_and_verify` runs all four and is what the
analysis path must use - a caller that skips it cannot reach the provider,
because the provider takes a `VerifiedImage` payload rather than raw bytes.

Every limit below is **application policy, not a market fact** - explicit,
configurable, documented and tested.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.application.vision.decode import (
    DecodePolicy,
    ImageDecodeError,
    VerifiedImage,
    verify_and_normalise,
)
from app.application.vision.images import (
    ImageFacts,
    ImageFormat,
    UnreadableImageError,
    read_image_facts,
)
from app.domain.vision.assets import (
    ScreenshotAsset,
    ScreenshotId,
    content_digest,
    sanitise_filename,
)
from app.domain.vision.slots import ScreenshotSlot


class ScreenshotRejectedError(ValueError):
    """An upload that must not proceed, with a stable reason code.

    The message never contains image content - only a code and the measured
    figures - so it is safe to log and safe to show.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ImagePolicy:
    """Upload limits. **Application policy, not market facts.**

    The defaults are sized for a chart screenshot from a normal display:
    generous enough for a 4K capture, small enough that a hostile upload is
    rejected before any parsing happens.
    """

    max_bytes: int = 8 * 1024 * 1024
    """8 MiB. Checked first, before the content is examined at all."""

    min_bytes: int = 64
    """Below this nothing can be a valid image of any supported format; a
    smaller file is truncated or empty by definition."""

    max_width: int = 12_000
    max_height: int = 12_000
    """Per-axis ceilings. A chart wider than this is not a screenshot."""

    max_pixels: int = 40_000_000
    """The decompression-bomb guard. Compared against *declared* dimensions,
    so the check costs two integer comparisons and never expands anything.
    40 megapixels is roughly a 8000x5000 capture."""

    min_width: int = 200
    min_height: int = 150
    """Below this a chart cannot carry legible axis labels, so the upload is
    refused rather than accepted and later scored unreadable."""

    def __post_init__(self) -> None:
        if self.min_bytes >= self.max_bytes:
            raise ValueError("min_bytes must be below max_bytes")
        if self.min_width > self.max_width or self.min_height > self.max_height:
            raise ValueError("minimum dimensions must not exceed maximum dimensions")
        for name in ("max_bytes", "max_width", "max_height", "max_pixels"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


def accept_screenshot(
    data: bytes,
    slot: ScreenshotSlot,
    *,
    filename: str | None = None,
    declared_media_type: str | None = None,
    policy: ImagePolicy | None = None,
    screenshot_id: ScreenshotId | None = None,
) -> ScreenshotAsset:
    """Validate an upload and describe it, or refuse it.

    Returns a `ScreenshotAsset` - metadata only. **The bytes are not stored in
    it**; the caller keeps them for the duration of the request and passes
    them to the vision adapter explicitly.

    ``screenshot_id`` is injectable for deterministic tests. Left unset, one is
    generated server-side, which is the only identity this system trusts.
    """
    settings = policy if policy is not None else ImagePolicy()

    if not data:
        raise ScreenshotRejectedError("EMPTY_UPLOAD", "the upload contains no data")

    size = len(data)
    if size > settings.max_bytes:
        raise ScreenshotRejectedError(
            "TOO_LARGE",
            f"the upload is {size} bytes, above the {settings.max_bytes} byte limit",
        )
    if size < settings.min_bytes:
        raise ScreenshotRejectedError(
            "TOO_SMALL",
            f"the upload is {size} bytes, below the {settings.min_bytes} byte minimum; "
            "it cannot be a complete image",
        )

    try:
        facts = read_image_facts(data)
    except UnreadableImageError as error:
        raise ScreenshotRejectedError("UNREADABLE_IMAGE", error.reason) from error

    _check_dimensions(facts, settings)

    return ScreenshotAsset(
        screenshot_id=screenshot_id or ScreenshotId.generate(),
        slot=slot,
        image_format=facts.image_format.value,
        width=facts.width,
        height=facts.height,
        byte_size=size,
        digest=content_digest(data),
        display_filename=sanitise_filename(filename),
        declared_media_type=(declared_media_type or "").strip(),
    )


@dataclass(frozen=True, slots=True)
class AcceptedScreenshot:
    """A fully verified screenshot and the payload safe to send onward.

    Holding these together is what makes the boundary hard to bypass: the
    analyser takes this type, so there is no signature that accepts an asset
    plus arbitrary bytes and no way to reach a provider with something that
    was never decoded.
    """

    asset: ScreenshotAsset
    verified: VerifiedImage

    @property
    def payload(self) -> bytes:
        """The bytes to send: normalised when normalisation is on."""
        return self.verified.payload

    @property
    def payload_media_type(self) -> str:
        """What the payload *is* after normalisation, which may differ from
        what was uploaded - a normalised JPEG is sent as a PNG."""
        return (
            ImageFormat.PNG.media_type
            if self.verified.normalised
            else self.verified.image_format.media_type
        )


def accept_and_verify(
    data: bytes,
    slot: ScreenshotSlot,
    *,
    filename: str | None = None,
    declared_media_type: str | None = None,
    policy: ImagePolicy | None = None,
    decode_policy: DecodePolicy | None = None,
    screenshot_id: ScreenshotId | None = None,
) -> AcceptedScreenshot:
    """Run every layer, including a real decode. **The only safe entry point.**

    A failure at any layer raises `ScreenshotRejectedError` with a stable code,
    so a caller never has to distinguish a header failure from a decode
    failure to report one.
    """
    settings = policy if policy is not None else ImagePolicy()
    asset = accept_screenshot(
        data,
        slot,
        filename=filename,
        declared_media_type=declared_media_type,
        policy=settings,
        screenshot_id=screenshot_id,
    )

    decode_settings = decode_policy or DecodePolicy(max_pixels=settings.max_pixels)
    try:
        verified = verify_and_normalise(data, decode_settings)
    except ImageDecodeError as error:
        raise ScreenshotRejectedError(error.failure.value, error.reason) from error

    if verified.image_format.value != asset.image_format:
        # The header parser and the decoder disagree about what this is.
        # Neither is believed over the other; the upload is refused.
        raise ScreenshotRejectedError(
            "FORMAT_DISAGREEMENT",
            f"the header reads {asset.image_format} but the decoder reads "
            f"{verified.image_format.value}",
        )

    return AcceptedScreenshot(asset=asset, verified=verified)


def _check_dimensions(facts: ImageFacts, policy: ImagePolicy) -> None:
    """Bound the declared size before anything would act on it."""
    if facts.pixels > policy.max_pixels:
        raise ScreenshotRejectedError(
            "EXCESSIVE_PIXELS",
            f"the image declares {facts.pixels} pixels "
            f"({facts.width}x{facts.height}), above the {policy.max_pixels} limit",
        )
    if facts.width > policy.max_width or facts.height > policy.max_height:
        raise ScreenshotRejectedError(
            "EXCESSIVE_DIMENSIONS",
            f"the image is {facts.width}x{facts.height}, above the "
            f"{policy.max_width}x{policy.max_height} limit",
        )
    if facts.width < policy.min_width or facts.height < policy.min_height:
        raise ScreenshotRejectedError(
            "TOO_SMALL_DIMENSIONS",
            f"the image is {facts.width}x{facts.height}, below the "
            f"{policy.min_width}x{policy.min_height} minimum for a legible chart",
        )

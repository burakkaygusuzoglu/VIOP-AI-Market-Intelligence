"""Bounded real decode verification before bytes leave the process (review A-C).

Phase 6A validated headers only. The human review is right that this is a fine
*preflight* and an insufficient *final boundary*: a file with a valid header
and a corrupt or truncated body passes a header parser and fails only once
something actually reads the pixels. Letting the external provider be the thing
that discovers that would make the provider our image validator, which the
review explicitly forbids.

So the pipeline is now four layers, cheapest first:

    byte-size bound          → refuse before anything parses
    signature/header preflight → refuse before anything decodes
    dimension/pixel policy     → refuse before anything allocates
    bounded real decode        → refuse before anything is sent

## Why Pillow, and why it was not needed in 6A

6A's decision stands for what 6A did: nothing there decoded, so no decoder was
justified. This layer genuinely decodes, and the review is explicit that
extending the hand-written parser into a home-grown full decoder is the wrong
answer - writing a JPEG decoder is precisely the kind of code that produces
memory-safety bugs. Pillow is the mature, widely-audited option, it is used
**only** here, and its own decompression-bomb protections are configured rather
than suppressed.

## Decompression bombs, and why no request touches global state

An earlier version of this module set `Image.MAX_IMAGE_PIXELS` from the policy
for the duration of each call and restored it in a `finally`, and promoted
Pillow's bomb warning with a per-call `warnings.catch_warnings()`. Both are
**process-global mutable state**, and both were measured leaking across threads:
a concurrent reader observed two different pixel limits while decodes were in
flight, and observed the promoted warning filter while doing no decoding of its
own. Under concurrency that means a strict request could decode beneath a
permissive request's raised ceiling - a bomb guard that depends on which other
requests happen to be running is not a guard.

So the invariant here is: **image validation policy never depends on a request
mutating global Pillow or warnings state.**

Two independent layers, neither of which is request-scoped:

1. **Application policy - the primary boundary.** `DecodePolicy` limits are
   checked by this module's own arithmetic, against the dimensions the decoder
   reports, *before* `verify()` or `load()` is called. `Image.open` reads a
   header and allocates no pixel buffer, so a bomb is refused before it can
   expand. This path is pure, deterministic, and identical under any amount of
   concurrency.
2. **A fixed process-wide backstop.** `configure_image_safety()` sets Pillow's
   own `MAX_IMAGE_PIXELS` **once, at application startup**, to a constant
   ceiling that no policy may exceed - `DecodePolicy` rejects a `max_pixels`
   above it at construction, so the two can never contradict each other. It is
   set once and never written again, so no request can observe it changing.

Pillow's protection is therefore configured, never suppressed and never
weakened: whichever layer fires first, the image is refused with a typed code.

## Static screenshots only (review B)

These inputs are screenshots. An animated WebP or an APNG is not one, and
silently analysing whichever frame the decoder happened to land on would be a
guess about which frame the user meant. Multi-frame input is refused with its
own reason code rather than quietly reduced to frame zero.

## Normalisation (review C)

After verification the image is **re-encoded to a single static PNG** before it
is sent anywhere. This is a deliberate trade:

* it removes EXIF and every other metadata block, which is both a privacy and
  an injection surface;
* it discards trailing or interleaved bytes appended after the image data -
  the polyglot-file trick that a structural check alone can miss;
* it guarantees exactly one frame reaches the provider;
* PNG is **lossless**, so no visible chart information is altered. That matters
  more here than payload size: the whole product depends on what the chart
  shows, and a lossy re-encode could soften the small axis text an extraction
  needs to read.

The re-encoded payload is bounded too, because a PNG of a photographic chart
can be larger than the JPEG it came from.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from enum import StrEnum, unique

from PIL import Image, UnidentifiedImageError

from app.application.vision.images import ImageFormat

PILLOW_PIXEL_CEILING = 80_000_000
"""The hard process-wide backstop, set once at startup.

Deliberately above the default `DecodePolicy.max_pixels` (40 M): policy is the
boundary that should normally fire, and this exists only to keep Pillow's own
guard from being *looser* than anything this application would allow. No
`DecodePolicy` may exceed it, so the two can never disagree.

A constant rather than a setting: it is a safety floor, not a tuning knob, and
a per-deployment value would make the guarantee vary by environment.
"""

_PILLOW_FORMATS: dict[str, ImageFormat] = {
    "PNG": ImageFormat.PNG,
    "JPEG": ImageFormat.JPEG,
    "WEBP": ImageFormat.WEBP,
}
"""Pillow's own format names, mapped to the three §38 accepts.

Anything Pillow recognises that is not in this table - GIF, BMP, TIFF, ICO -
is refused by *name* rather than by guessing from the bytes a second time.
"""


@unique
class DecodeFailure(StrEnum):
    """Why a decode verification failed. Stable codes, safe to log."""

    UNIDENTIFIED = "UNIDENTIFIED"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    MULTI_FRAME = "MULTI_FRAME"
    CORRUPT = "CORRUPT"
    TRUNCATED = "TRUNCATED"
    DECOMPRESSION_BOMB = "DECOMPRESSION_BOMB"
    EXCESSIVE_PIXELS = "EXCESSIVE_PIXELS"
    NORMALISATION_TOO_LARGE = "NORMALISATION_TOO_LARGE"


class ImageDecodeError(ValueError):
    """A verified failure to decode. Never contains image bytes."""

    def __init__(self, failure: DecodeFailure, reason: str) -> None:
        super().__init__(reason)
        self.failure = failure
        self.reason = reason


@dataclass(frozen=True, slots=True)
class DecodePolicy:
    """Decode limits. **Application policy, not market facts.**"""

    max_pixels: int = 40_000_000
    """Checked by this module's own arithmetic against the decoder-reported
    dimensions, before anything decodes. Never written into Pillow's global
    limit - see the module docstring."""

    allow_multi_frame: bool = False
    """False by design: these are screenshots. Configurable so the refusal is
    a stated policy rather than an assumption baked into the code."""

    normalise: bool = True
    """Re-encode to a static PNG before sending. See the module docstring."""

    max_normalised_bytes: int = 12 * 1024 * 1024
    """A lossless re-encode can be larger than its source, so the result is
    bounded rather than assumed smaller."""

    def __post_init__(self) -> None:
        if self.max_pixels <= 0:
            raise ValueError("max_pixels must be positive")
        if self.max_pixels > PILLOW_PIXEL_CEILING:
            # Refused rather than silently clamped: a caller asking for a
            # budget above the process backstop has a belief about what will
            # be accepted that this module cannot honour, and quietly
            # narrowing it would hide that.
            raise ValueError(
                f"max_pixels {self.max_pixels} exceeds the process ceiling {PILLOW_PIXEL_CEILING}"
            )
        if self.max_normalised_bytes <= 0:
            raise ValueError("max_normalised_bytes must be positive")


@dataclass(frozen=True, slots=True)
class VerifiedImage:
    """An image that has actually been decoded, and the payload to send."""

    image_format: ImageFormat
    width: int
    height: int
    frames: int
    payload: bytes
    """What should be sent onward: the normalised PNG when normalisation is
    on, otherwise the original verified bytes."""

    normalised: bool

    @property
    def pixels(self) -> int:
        return self.width * self.height


def configure_image_safety() -> None:
    """Set Pillow's process-wide backstop once, at application startup.

    Called from the composition root, not from a request. Writing the same
    constant every time makes it idempotent, so a second call - a test, a
    second worker in the same process - cannot be observed as a change.

    This is a *backstop*. `DecodePolicy` remains the boundary that normally
    fires, and it is enforced by this module's own arithmetic rather than by
    the value set here.
    """
    Image.MAX_IMAGE_PIXELS = PILLOW_PIXEL_CEILING


def verify_and_normalise(data: bytes, policy: DecodePolicy | None = None) -> VerifiedImage:
    """Fully decode ``data``, refuse anything unsafe, and return what to send.

    Raises `ImageDecodeError` for every failure mode, each with a stable code.
    No exception message contains image content.

    **Mutates no global state.** Every limit applied here comes from ``policy``
    and is checked by this function's own arithmetic, so the result depends
    only on its arguments - never on what another request is doing at the same
    time. See the module docstring for the measurements that forced this.
    """
    settings = policy if policy is not None else DecodePolicy()

    image_format, width, height, frames = _inspect(data, settings)
    payload, normalised = _payload(data, settings)

    if normalised and len(payload) > settings.max_normalised_bytes:
        raise ImageDecodeError(
            DecodeFailure.NORMALISATION_TOO_LARGE,
            f"the normalised image is {len(payload)} bytes, above the "
            f"{settings.max_normalised_bytes} byte limit",
        )

    return VerifiedImage(
        image_format=image_format,
        width=width,
        height=height,
        frames=frames,
        payload=payload,
        normalised=normalised,
    )


def _inspect(data: bytes, policy: DecodePolicy) -> tuple[ImageFormat, int, int, int]:
    """Open, check policy, verify structure, then force a full decode."""
    try:
        with Image.open(io.BytesIO(data)) as opened:
            raw_format = opened.format or ""
            size = opened.size
            frames = int(getattr(opened, "n_frames", 1))
    except Image.DecompressionBombWarning as error:
        raise ImageDecodeError(
            DecodeFailure.DECOMPRESSION_BOMB,
            "the image exceeds the configured pixel limit and was refused before decoding",
        ) from error
    except Image.DecompressionBombError as error:
        raise ImageDecodeError(
            DecodeFailure.DECOMPRESSION_BOMB,
            "the image is far above the configured pixel limit",
        ) from error
    except UnidentifiedImageError as error:
        raise ImageDecodeError(
            DecodeFailure.UNIDENTIFIED, "the content could not be identified as an image"
        ) from error
    except OSError as error:
        raise ImageDecodeError(DecodeFailure.CORRUPT, "the image could not be opened") from error

    if raw_format not in _PILLOW_FORMATS:
        raise ImageDecodeError(
            DecodeFailure.UNSUPPORTED_FORMAT,
            f"decoded format {raw_format or 'unknown'} is not PNG, JPEG or WEBP",
        )

    if frames > 1 and not policy.allow_multi_frame:
        raise ImageDecodeError(
            DecodeFailure.MULTI_FRAME,
            f"the image has {frames} frames; a screenshot must be a single "
            "static frame, and no frame is chosen on the user's behalf",
        )

    width, height = size
    if width * height > policy.max_pixels:
        raise ImageDecodeError(
            DecodeFailure.EXCESSIVE_PIXELS,
            f"the decoded image is {width}x{height}, above the {policy.max_pixels} pixel limit",
        )

    _verify_structure(data)
    _force_decode(data)
    return _PILLOW_FORMATS[raw_format], width, height, frames


def _verify_structure(data: bytes) -> None:
    """`verify()` checks integrity without decoding pixels.

    Pillow requires a fresh handle afterwards, which is why the bytes are
    opened again below rather than reused.
    """
    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.verify()
    except (OSError, SyntaxError, ValueError) as error:
        raise ImageDecodeError(
            DecodeFailure.CORRUPT,
            "the image failed integrity verification; its header is valid but its contents are not",
        ) from error


def _force_decode(data: bytes) -> None:
    """`load()` actually decodes, which is what catches a truncated body.

    `ImageFile.LOAD_TRUNCATED_IMAGES` is left at its default of False so a
    short read raises instead of returning a half-grey picture.
    """
    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.load()
    except Image.DecompressionBombError as error:
        raise ImageDecodeError(
            DecodeFailure.DECOMPRESSION_BOMB, "the image expanded beyond the pixel limit"
        ) from error
    except OSError as error:
        message = str(error).lower()
        failure = (
            DecodeFailure.TRUNCATED
            if "truncated" in message or "incomplete" in message
            else DecodeFailure.CORRUPT
        )
        raise ImageDecodeError(failure, "the image data could not be fully decoded") from error


def _target_mode(opened: Image.Image) -> str:
    """The mode to normalise into, chosen so nothing visible is destroyed.

    Converting unconditionally to ``RGB`` looks harmless and is not: Pillow
    drops the alpha channel rather than compositing it, so every transparent
    pixel takes whatever colour sat underneath it - in practice black. A chart
    exported with a transparent background and dark axis labels would arrive at
    the provider as dark ink on a black field, unreadable, with no error
    raised anywhere. Compositing onto an invented white background is the same
    mistake facing the other way: it would erase a dark-theme chart's light
    text.

    The output container is PNG, which carries alpha, so the honest answer is
    to keep it. Only modes PNG cannot represent faithfully (CMYK, high-bit
    integer, float) are collapsed, and those genuinely cannot survive a PNG
    round trip.
    """
    mode = opened.mode
    if mode in {"RGB", "RGBA", "L", "LA", "1", "I;16"}:
        # Already something PNG stores directly; RGB/RGBA are returned as-is
        # so the conversion is an identity rather than a resample.
        return mode if mode != "1" else "L"
    if mode in {"P", "PA"}:
        # A palette image may carry per-index transparency. Widen to RGBA when
        # it does, so the transparency survives instead of being flattened.
        return "RGBA" if (mode == "PA" or "transparency" in opened.info) else "RGB"
    return "RGB"


def _payload(data: bytes, policy: DecodePolicy) -> tuple[bytes, bool]:
    """The bytes to send onward: a normalised PNG, or the verified original."""
    if not policy.normalise:
        return data, False

    try:
        with Image.open(io.BytesIO(data)) as opened:
            frame = opened.convert(_target_mode(opened))
            buffer = io.BytesIO()
            # `optimize` is off deliberately: it trades CPU for size and this
            # payload is transient. No metadata is carried across, because a
            # fresh image is written rather than the original re-saved.
            frame.save(buffer, format="PNG")
    except (OSError, ValueError) as error:
        raise ImageDecodeError(
            DecodeFailure.CORRUPT, "the image could not be re-encoded safely"
        ) from error

    return buffer.getvalue(), True

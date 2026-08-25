"""Image format detection and dimension reading, from headers only.

## Why there is no image library here

`imghdr` was removed from the standard library in Python 3.13, and this
environment runs 3.14, so the obvious answer is gone. The next obvious answer
is Pillow - and it was rejected after weighing it, not for convenience.

Phase 6A needs three things from an upload: what format it *actually* is, how
large it claims to be, and whether it is structurally coherent. All three live
in the first few dozen bytes of every format §38 accepts. Nothing here needs to
render, resize or re-encode anything.

That distinction matters for security rather than for size. A header parser
**never allocates a pixel buffer**, so a decompression bomb cannot be triggered
by it at all: a PNG declaring 60000x60000 is rejected by comparing two integers
against a policy limit, long before anything would try to expand it. Adding a
decoder would introduce the exact attack surface this module is meant to keep
closed, and CLAUDE.md is explicit that a dependency needs a stated reason - "it
would also have worked" is not one.

**The honest limit of this approach**, stated rather than discovered later: it
validates *structure and declared dimensions*, not that every pixel is
well-formed. A file with a valid header and corrupt image data will pass here.
That is acceptable because nothing in Phase 6A decodes the image - the bytes
are forwarded to a vision model in 6B, which will fail on genuinely unreadable
content, and that failure is reported rather than hidden. What this module
guarantees is that no *unparseable or oversized* input reaches that point.

Should a later phase need real decoding - thumbnailing, redaction, EXIF
stripping - that is the moment to revisit the decision, with the requirement in
hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique


@unique
class ImageFormat(StrEnum):
    """The three formats §38 accepts. Nothing else is parsed."""

    PNG = "PNG"
    JPEG = "JPEG"
    WEBP = "WEBP"

    @property
    def media_type(self) -> str:
        """The IANA type, derived here so no caller has to hard-code one."""
        return _MEDIA_TYPES[self]


_MEDIA_TYPES: dict[ImageFormat, str] = {
    ImageFormat.PNG: "image/png",
    ImageFormat.JPEG: "image/jpeg",
    ImageFormat.WEBP: "image/webp",
}


class UnreadableImageError(ValueError):
    """The bytes are not a parseable image of a supported format.

    Carries a ``reason`` so a caller can report *why* without re-deriving it,
    and so the message shown to a user never contains image content.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ImageFacts:
    """What the header actually says. No pixels were read."""

    image_format: ImageFormat
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise UnreadableImageError(
                f"image reports non-positive dimensions {self.width}x{self.height}"
            )

    @property
    def pixels(self) -> int:
        """Declared pixel count - the number a bomb check compares."""
        return self.width * self.height


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_JPEG_SOF_MARKERS = frozenset(
    # SOF0-SOF15 carry the frame dimensions. 0xC4 (DHT), 0xC8 (JPG extension)
    # and 0xCC (DAC) sit in the same numeric range but are not frame headers.
    set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
)
_JPEG_STANDALONE_MARKERS = frozenset({0x01, *range(0xD0, 0xDA)})
"""Markers with no length field: restart markers, TEM, SOI and EOI."""


def read_image_facts(data: bytes) -> ImageFacts:
    """Identify ``data`` and read its dimensions from the header.

    Raises `UnreadableImageError` for anything that is empty, truncated,
    structurally malformed, or not one of the three supported formats. The
    format is decided by **content**, never by a filename or a declared
    content type.
    """
    if not data:
        raise UnreadableImageError("the upload is empty")

    if data.startswith(_PNG_SIGNATURE):
        return _read_png(data)
    if data.startswith(b"\xff\xd8"):
        return _read_jpeg(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _read_webp(data)

    raise UnreadableImageError(
        "the content is not a PNG, JPEG or WEBP image; only these three are accepted"
    )


def _read_png(data: bytes) -> ImageFacts:
    """PNG: the IHDR chunk is mandatory and always first.

    Its position is fixed by the specification, so a PNG whose first chunk is
    not IHDR is malformed rather than merely unusual.
    """
    if len(data) < 24:
        raise UnreadableImageError("the PNG header is truncated")
    if data[12:16] != b"IHDR":
        raise UnreadableImageError("the PNG is malformed: its first chunk is not IHDR")

    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return ImageFacts(image_format=ImageFormat.PNG, width=width, height=height)


def _read_jpeg(data: bytes) -> ImageFacts:
    """JPEG: walk the segment chain to the frame header.

    Bounded by construction - each step advances past a declared segment
    length, and a length that would not advance is treated as malformed rather
    than looped on.
    """
    position = 2
    limit = len(data)

    while position < limit:
        if data[position] != 0xFF:
            raise UnreadableImageError("the JPEG segment structure is malformed")

        # Fill bytes: any number of 0xFF may precede a marker.
        while position < limit and data[position] == 0xFF:
            position += 1
        if position >= limit:
            raise UnreadableImageError("the JPEG is truncated before a marker")

        marker = data[position]
        position += 1

        if marker in _JPEG_STANDALONE_MARKERS:
            continue
        if marker == 0xD9:  # pragma: no cover - EOI is in the standalone set
            break

        if position + 2 > limit:
            raise UnreadableImageError("the JPEG is truncated inside a segment header")
        length = int.from_bytes(data[position : position + 2], "big")
        if length < 2:
            raise UnreadableImageError("the JPEG declares an impossible segment length")

        if marker in _JPEG_SOF_MARKERS:
            if position + 7 > limit:
                raise UnreadableImageError("the JPEG frame header is truncated")
            height = int.from_bytes(data[position + 3 : position + 5], "big")
            width = int.from_bytes(data[position + 5 : position + 7], "big")
            return ImageFacts(image_format=ImageFormat.JPEG, width=width, height=height)

        position += length

    raise UnreadableImageError("the JPEG contains no frame header, so it has no dimensions")


def _read_webp(data: bytes) -> ImageFacts:
    """WEBP: a RIFF container holding one of three chunk layouts.

    All three are handled because a chart screenshot may be saved as any of
    them, and silently supporting only the common one would reject valid user
    uploads for no stated reason.
    """
    if len(data) < 16:
        raise UnreadableImageError("the WEBP header is truncated")

    chunk = data[12:16]

    if chunk == b"VP8 ":
        # Lossy: a 3-byte frame tag, then the sync code, then 14-bit sizes.
        if len(data) < 30:
            raise UnreadableImageError("the lossy WEBP frame header is truncated")
        if data[23:26] != b"\x9d\x01\x2a":
            raise UnreadableImageError("the lossy WEBP sync code is missing")
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return ImageFacts(image_format=ImageFormat.WEBP, width=width, height=height)

    if chunk == b"VP8L":
        # Lossless: one signature byte, then 14 bits of (width-1) and
        # (height-1) packed across four little-endian bytes.
        if len(data) < 25:
            raise UnreadableImageError("the lossless WEBP header is truncated")
        if data[20] != 0x2F:
            raise UnreadableImageError("the lossless WEBP signature byte is missing")
        packed = int.from_bytes(data[21:25], "little")
        width = (packed & 0x3FFF) + 1
        height = ((packed >> 14) & 0x3FFF) + 1
        return ImageFacts(image_format=ImageFormat.WEBP, width=width, height=height)

    if chunk == b"VP8X":
        # Extended: flags, then 24-bit (width-1) and (height-1).
        if len(data) < 30:
            raise UnreadableImageError("the extended WEBP header is truncated")
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return ImageFacts(image_format=ImageFormat.WEBP, width=width, height=height)

    raise UnreadableImageError("the WEBP container holds no recognised image chunk")

"""Real image bytes for Phase 6A tests, built by hand.

**Every byte here is TEST_FIXTURE data.** None of it is a real chart; these are
the smallest headers each format permits, assembled so the intake path is
exercised against genuine structure rather than against a mock.

Built rather than checked in as binary files for three reasons: a test can ask
for any dimensions it needs, a reviewer can see exactly what makes a fixture
valid or malformed, and there is no opaque blob in the repository whose
contents nobody has read.

The headers are only as complete as the parser reads. That is deliberate - the
parser is header-only by design, so a fixture that satisfies it is exactly the
input the production path accepts.
"""

from __future__ import annotations

import struct
import zlib


def png_bytes(width: int = 1280, height: int = 720) -> bytes:
    """A structurally valid PNG: signature, IHDR, minimal IDAT, IEND.

    The CRCs are computed properly so the file would open in a real decoder,
    even though this project's parser never checks them.
    """
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_body = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr = _png_chunk(b"IHDR", ihdr_body)
    idat = _png_chunk(b"IDAT", zlib.compress(b"\x00" * 16))
    iend = _png_chunk(b"IEND", b"")
    return signature + ihdr + idat + iend


def _png_chunk(kind: bytes, body: bytes) -> bytes:
    return (
        struct.pack(">I", len(body))
        + kind
        + body
        + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    )


def jpeg_bytes(width: int = 1280, height: int = 720) -> bytes:
    """A JPEG with SOI, an APP0 segment, an SOF0 frame header and EOI.

    The APP0 segment is included on purpose: it forces the parser to walk past
    a segment it does not care about before reaching the frame header, which
    is the behaviour a single-segment fixture would not exercise.
    """
    soi = b"\xff\xd8"
    app0_body = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    app0 = b"\xff\xe0" + struct.pack(">H", len(app0_body) + 2) + app0_body
    sof_body = struct.pack(">BHHB", 8, height, width, 3) + b"\x01\x22\x00" * 3
    sof0 = b"\xff\xc0" + struct.pack(">H", len(sof_body) + 2) + sof_body
    return soi + app0 + sof0 + b"\xff\xd9"


def webp_lossy_bytes(width: int = 1280, height: int = 720) -> bytes:
    """A RIFF/WEBP container holding a lossy VP8 frame header."""
    frame = (
        b"\x00\x00\x00"  # frame tag
        + b"\x9d\x01\x2a"  # sync code
        + struct.pack("<HH", width & 0x3FFF, height & 0x3FFF)
        + b"\x00" * 8
    )
    chunk = b"VP8 " + struct.pack("<I", len(frame)) + frame
    return b"RIFF" + struct.pack("<I", len(chunk) + 4) + b"WEBP" + chunk


def webp_lossless_bytes(width: int = 1280, height: int = 720) -> bytes:
    """A VP8L chunk: one signature byte, then 14-bit packed dimensions."""
    packed = ((height - 1) << 14) | (width - 1)
    body = b"\x2f" + struct.pack("<I", packed) + b"\x00" * 8
    chunk = b"VP8L" + struct.pack("<I", len(body)) + body
    return b"RIFF" + struct.pack("<I", len(chunk) + 4) + b"WEBP" + chunk


def webp_extended_bytes(width: int = 1280, height: int = 720) -> bytes:
    """A VP8X chunk: flags, then 24-bit dimensions."""
    body = (
        b"\x00\x00\x00\x00" + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    )
    chunk = b"VP8X" + struct.pack("<I", len(body)) + body
    return b"RIFF" + struct.pack("<I", len(chunk) + 4) + b"WEBP" + chunk


def truncated_png() -> bytes:
    """A valid signature with the IHDR chunk cut short."""
    return png_bytes()[:18]


def png_without_ihdr() -> bytes:
    """A PNG whose first chunk is not IHDR - structurally malformed."""
    signature = b"\x89PNG\r\n\x1a\n"
    return signature + _png_chunk(b"tEXt", b"not an image header")


def jpeg_without_frame() -> bytes:
    """A JPEG that ends before any frame header, so it has no dimensions."""
    soi = b"\xff\xd8"
    app0_body = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    app0 = b"\xff\xe0" + struct.pack(">H", len(app0_body) + 2) + app0_body
    return soi + app0 + b"\xff\xd9"


def gif_bytes() -> bytes:
    """A GIF header - a real image format that §38 does not accept."""
    return b"GIF89a" + b"\x00" * 64


def not_an_image() -> bytes:
    """Bytes that are not any image format."""
    return b"#!/bin/sh\necho this is a script, not a screenshot\n" + b"\x00" * 64

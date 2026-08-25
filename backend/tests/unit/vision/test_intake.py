"""Image intake, format detection and upload security (§38, §98).

Every image here is TEST_FIXTURE data assembled by hand - see
`tests/factories_vision.py`.
"""

from __future__ import annotations

import pytest

from app.application.vision.images import (
    ImageFormat,
    UnreadableImageError,
    read_image_facts,
)
from app.application.vision.intake import (
    ImagePolicy,
    ScreenshotRejectedError,
    accept_screenshot,
)
from app.domain.vision.assets import ScreenshotId, content_digest, sanitise_filename
from app.domain.vision.slots import ScreenshotSlot
from tests.factories_vision import (
    gif_bytes,
    jpeg_bytes,
    jpeg_without_frame,
    not_an_image,
    png_bytes,
    png_without_ihdr,
    truncated_png,
    webp_extended_bytes,
    webp_lossless_bytes,
    webp_lossy_bytes,
)

SLOT = ScreenshotSlot.H1


# ----------------------------------------------------------------------
# The three accepted formats
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("builder", "expected"),
    (
        (png_bytes, ImageFormat.PNG),
        (jpeg_bytes, ImageFormat.JPEG),
        (webp_lossy_bytes, ImageFormat.WEBP),
        (webp_lossless_bytes, ImageFormat.WEBP),
        (webp_extended_bytes, ImageFormat.WEBP),
    ),
)
def test_each_supported_format_is_identified_from_its_content(
    builder: object, expected: ImageFormat
) -> None:
    facts = read_image_facts(builder())  # type: ignore[operator]
    assert facts.image_format is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "builder", (png_bytes, jpeg_bytes, webp_lossy_bytes, webp_lossless_bytes, webp_extended_bytes)
)
def test_dimensions_are_read_from_the_header(builder: object) -> None:
    """Every format encodes its size differently; all three are parsed."""
    facts = read_image_facts(builder(width=1600, height=900))  # type: ignore[operator]
    assert facts.width == 1600
    assert facts.height == 900
    assert facts.pixels == 1_440_000


@pytest.mark.unit
def test_a_valid_upload_becomes_an_asset() -> None:
    data = png_bytes()
    asset = accept_screenshot(data, SLOT, filename="chart.png")
    assert asset.slot is SLOT
    assert asset.image_format == "PNG"
    assert asset.byte_size == len(data)
    assert asset.digest == content_digest(data)
    assert asset.identity


# ----------------------------------------------------------------------
# The client is never believed
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_wrong_extension_does_not_change_the_detected_format() -> None:
    """A PNG named .jpg is still a PNG. Content decides."""
    asset = accept_screenshot(png_bytes(), SLOT, filename="screenshot.jpg")
    assert asset.image_format == "PNG"


@pytest.mark.unit
def test_a_misleading_content_type_is_recorded_but_not_believed() -> None:
    """Browsers get this wrong routinely, so it is audited rather than
    trusted - and a mismatch is visible afterwards."""
    asset = accept_screenshot(png_bytes(), SLOT, filename="c.png", declared_media_type="image/jpeg")
    assert asset.image_format == "PNG"
    assert asset.declared_media_type == "image/jpeg"
    assert asset.media_type_was_misleading


@pytest.mark.unit
def test_a_matching_content_type_is_not_flagged() -> None:
    asset = accept_screenshot(png_bytes(), SLOT, filename="c.png", declared_media_type="image/png")
    assert not asset.media_type_was_misleading


# ----------------------------------------------------------------------
# Rejections
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_an_empty_upload_is_refused() -> None:
    with pytest.raises(ScreenshotRejectedError) as excinfo:
        accept_screenshot(b"", SLOT)
    assert excinfo.value.code == "EMPTY_UPLOAD"


@pytest.mark.unit
def test_an_oversize_upload_is_refused_before_it_is_parsed() -> None:
    """Bounded first, so a hostile payload is never handed to the parser.

    The payload is deliberately *not* a valid image: if size were checked
    after parsing, this would fail as UNREADABLE_IMAGE instead, so the
    rejection code proves the ordering.
    """
    policy = ImagePolicy(max_bytes=1024)
    oversized = b"\x00" * 4096
    with pytest.raises(ScreenshotRejectedError) as excinfo:
        accept_screenshot(oversized, SLOT, policy=policy)
    assert excinfo.value.code == "TOO_LARGE"


@pytest.mark.unit
def test_a_file_too_small_to_be_an_image_is_refused() -> None:
    with pytest.raises(ScreenshotRejectedError) as excinfo:
        accept_screenshot(b"\x89PNG\r\n\x1a\n", SLOT)
    assert excinfo.value.code == "TOO_SMALL"


@pytest.mark.unit
@pytest.mark.parametrize(
    "builder", (truncated_png, png_without_ihdr, jpeg_without_frame, not_an_image, gif_bytes)
)
def test_malformed_truncated_and_unsupported_content_is_refused(builder: object) -> None:
    with pytest.raises(ScreenshotRejectedError) as excinfo:
        accept_screenshot(builder(), SLOT)  # type: ignore[operator]
    assert excinfo.value.code in {"UNREADABLE_IMAGE", "TOO_SMALL"}


@pytest.mark.unit
def test_a_supported_looking_but_empty_container_is_refused() -> None:
    """A RIFF/WEBP wrapper holding no recognised chunk."""
    payload = b"RIFF" + (64).to_bytes(4, "little") + b"WEBP" + b"XXXX" + b"\x00" * 60
    with pytest.raises(UnreadableImageError, match="no recognised image chunk"):
        read_image_facts(payload)


@pytest.mark.unit
def test_the_rejection_message_never_contains_image_content() -> None:
    """Errors are logged; image bytes must never be."""
    with pytest.raises(ScreenshotRejectedError) as excinfo:
        accept_screenshot(not_an_image(), SLOT)
    assert "this is a script" not in str(excinfo.value)


# ----------------------------------------------------------------------
# Decompression-bomb and dimension policy
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_declared_pixel_bomb_is_refused_without_decoding_anything() -> None:
    """The whole reason this parser reads headers only.

    A PNG declaring 60000x60000 is 3.6 billion pixels. It is rejected by
    comparing two integers; nothing ever allocates a buffer for it, so the
    bomb cannot go off.
    """
    payload = png_bytes(width=60_000, height=60_000)
    assert len(payload) < 200, "the fixture must stay tiny - that is the point"
    with pytest.raises(ScreenshotRejectedError) as excinfo:
        accept_screenshot(payload, SLOT)
    assert excinfo.value.code == "EXCESSIVE_PIXELS"


@pytest.mark.unit
def test_an_over_wide_image_is_refused() -> None:
    policy = ImagePolicy(max_width=1000, max_pixels=100_000_000)
    with pytest.raises(ScreenshotRejectedError) as excinfo:
        accept_screenshot(png_bytes(width=2000, height=500), SLOT, policy=policy)
    assert excinfo.value.code == "EXCESSIVE_DIMENSIONS"


@pytest.mark.unit
def test_an_illegibly_small_chart_is_refused() -> None:
    with pytest.raises(ScreenshotRejectedError) as excinfo:
        accept_screenshot(png_bytes(width=64, height=48), SLOT)
    assert excinfo.value.code == "TOO_SMALL_DIMENSIONS"


@pytest.mark.unit
def test_zero_dimensions_are_impossible() -> None:
    with pytest.raises(UnreadableImageError, match="non-positive"):
        read_image_facts(png_bytes(width=0, height=100))


@pytest.mark.unit
def test_the_limits_are_configurable_policy_not_constants() -> None:
    generous = ImagePolicy(min_width=10, min_height=10)
    asset = accept_screenshot(png_bytes(width=64, height=48), SLOT, policy=generous)
    assert asset.width == 64


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    (
        {"max_bytes": 0},
        {"min_bytes": 10_000_000},
        {"min_width": 5000, "max_width": 1000},
    ),
)
def test_an_incoherent_policy_is_rejected(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="must"):
        ImagePolicy(**kwargs)


# ----------------------------------------------------------------------
# Filenames are display text, never identity and never a path
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        # Only the basename survives - the directory part is discarded
        # entirely rather than flattened into the name.
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32", "system32"),
        ("/absolute/path/chart.png", "chart.png"),
        ("chart<>:|?*.png", "chart.png"),
        ("....png", "png"),
        ("", ""),
        (None, ""),
    ),
)
def test_a_filename_is_stripped_of_anything_path_like(raw: str | None, expected: str) -> None:
    result = sanitise_filename(raw)
    assert result == expected
    assert "/" not in result
    assert "\\" not in result
    assert ".." not in result


@pytest.mark.unit
def test_a_long_filename_is_truncated() -> None:
    assert len(sanitise_filename("a" * 500)) <= 96


@pytest.mark.unit
def test_identity_is_server_generated_and_not_the_filename() -> None:
    """§98: a user-supplied name must never become a key or a path."""
    asset = accept_screenshot(png_bytes(), SLOT, filename="../../evil.png")
    assert asset.identity != asset.display_filename
    assert "/" not in asset.identity
    assert ".." not in asset.display_filename


@pytest.mark.unit
def test_two_uploads_of_the_same_bytes_get_different_identities() -> None:
    """Identity is per-upload; the digest is what says they are the same
    image."""
    data = png_bytes()
    first = accept_screenshot(data, SLOT)
    second = accept_screenshot(data, SLOT)
    assert first.identity != second.identity
    assert first.digest == second.digest


@pytest.mark.unit
def test_the_digest_identifies_the_content() -> None:
    assert content_digest(png_bytes()) == content_digest(png_bytes())
    assert content_digest(png_bytes()) != content_digest(jpeg_bytes())


@pytest.mark.unit
def test_a_blank_screenshot_id_is_impossible() -> None:
    with pytest.raises(ValueError, match="cannot be blank"):
        ScreenshotId(value="   ")


@pytest.mark.unit
def test_intake_is_deterministic_given_an_identity() -> None:
    data = png_bytes()
    identity = ScreenshotId.generate()
    first = accept_screenshot(data, SLOT, screenshot_id=identity)
    second = accept_screenshot(data, SLOT, screenshot_id=identity)
    assert first == second


@pytest.mark.unit
def test_the_asset_carries_no_image_bytes() -> None:
    """A domain value that outlives the request must not hold megabytes."""
    from app.domain.vision.assets import ScreenshotAsset  # noqa: PLC0415

    forbidden = {"data", "bytes", "content", "payload", "raw"}
    assert forbidden.isdisjoint(ScreenshotAsset.__dataclass_fields__)

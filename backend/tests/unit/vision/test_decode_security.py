"""Real decode verification and static-screenshot semantics (review A, B, C, E).

These are the probes the human review asked for: a valid header with a corrupt
body, a truncated body, a pixel bomb that passes signature inspection, and
multi-frame input. All fixtures are TEST_FIXTURE data built in this file.
"""

from __future__ import annotations

import io
import struct
import threading
import zlib

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from app.application.vision.decode import (
    PILLOW_PIXEL_CEILING,
    DecodeFailure,
    DecodePolicy,
    ImageDecodeError,
    configure_image_safety,
    verify_and_normalise,
)
from app.application.vision.images import ImageFormat
from app.application.vision.intake import (
    ImagePolicy,
    ScreenshotRejectedError,
    accept_and_verify,
)
from app.domain.vision.slots import ScreenshotSlot
from tests.factories_vision import gif_bytes, png_bytes

SLOT = ScreenshotSlot.H1


def real_png(width: int = 640, height: int = 480, colour: str = "white") -> bytes:
    """A genuine PNG produced by the decoder itself.

    The hand-built fixtures from 6A satisfy a header parser but are not
    complete images; a real decode needs real image data.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def real_jpeg(width: int = 640, height: int = 480) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def real_webp(width: int = 640, height: int = 480) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="WEBP")
    return buffer.getvalue()


def animated_webp(frames: int = 3) -> bytes:
    buffer = io.BytesIO()
    images = [Image.new("RGB", (320, 240), colour) for colour in ("red", "green", "blue")][:frames]
    images[0].save(buffer, format="WEBP", save_all=True, append_images=images[1:], duration=100)
    return buffer.getvalue()


def animated_png(frames: int = 3) -> bytes:
    buffer = io.BytesIO()
    images = [Image.new("RGB", (320, 240), colour) for colour in ("red", "green", "blue")][:frames]
    images[0].save(buffer, format="PNG", save_all=True, append_images=images[1:], duration=100)
    return buffer.getvalue()


def corrupt_body_png() -> bytes:
    """A valid PNG header and IHDR, with the pixel data replaced by garbage.

    This is the exact case the human review named: header validation passes,
    and only a real decode discovers the problem.
    """
    good = real_png()
    marker = good.index(b"IDAT")
    body = b"\xde\xad\xbe\xef" * 24
    length = struct.pack(">I", len(body))
    crc = struct.pack(">I", zlib.crc32(b"IDAT" + body) & 0xFFFFFFFF)
    return good[: marker - 4] + length + b"IDAT" + body + crc + good[-12:]


def truncated_body_png() -> bytes:
    """A complete header with the file cut off mid-pixel-data."""
    good = real_png()
    return good[: len(good) // 2]


# ----------------------------------------------------------------------
# The three formats still decode
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("builder", "expected"),
    (
        (real_png, ImageFormat.PNG),
        (real_jpeg, ImageFormat.JPEG),
        (real_webp, ImageFormat.WEBP),
    ),
)
def test_a_real_image_of_each_format_verifies(builder: object, expected: ImageFormat) -> None:
    verified = verify_and_normalise(builder())  # type: ignore[operator]
    assert verified.image_format is expected
    assert verified.width == 640
    assert verified.height == 480
    assert verified.frames == 1


# ----------------------------------------------------------------------
# Review A: header-valid but body-invalid
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_valid_header_with_a_corrupt_body_is_refused() -> None:
    """The gap Phase 6A left open, and the reason this layer exists."""
    payload = corrupt_body_png()
    with pytest.raises(ImageDecodeError) as excinfo:
        verify_and_normalise(payload)
    assert excinfo.value.failure in {DecodeFailure.CORRUPT, DecodeFailure.TRUNCATED}


@pytest.mark.unit
def test_a_truncated_body_is_refused() -> None:
    with pytest.raises(ImageDecodeError) as excinfo:
        verify_and_normalise(truncated_body_png())
    assert excinfo.value.failure in {DecodeFailure.CORRUPT, DecodeFailure.TRUNCATED}


@pytest.mark.unit
def test_the_header_parser_alone_would_have_accepted_the_corrupt_file() -> None:
    """Proves the layers are not redundant.

    The preflight passes this file; only the decode catches it. If this ever
    stops being true, the decode layer has stopped adding anything.
    """
    from app.application.vision.images import read_image_facts  # noqa: PLC0415

    facts = read_image_facts(corrupt_body_png())
    assert facts.image_format is ImageFormat.PNG
    with pytest.raises(ImageDecodeError):
        verify_and_normalise(corrupt_body_png())


@pytest.mark.unit
def test_the_full_pipeline_refuses_a_corrupt_body() -> None:
    with pytest.raises(ScreenshotRejectedError) as excinfo:
        accept_and_verify(corrupt_body_png(), SLOT)
    assert excinfo.value.code in {"CORRUPT", "TRUNCATED"}


@pytest.mark.unit
def test_an_unidentifiable_payload_is_refused_by_the_decoder_too() -> None:
    with pytest.raises(ImageDecodeError) as excinfo:
        verify_and_normalise(b"not an image at all" * 8)
    assert excinfo.value.failure is DecodeFailure.UNIDENTIFIED


@pytest.mark.unit
def test_a_recognised_but_unsupported_format_is_refused_by_name() -> None:
    """GIF decodes fine; §38 does not accept it."""
    buffer = io.BytesIO()
    Image.new("RGB", (320, 240), "white").save(buffer, format="GIF")
    with pytest.raises(ImageDecodeError) as excinfo:
        verify_and_normalise(buffer.getvalue())
    assert excinfo.value.failure is DecodeFailure.UNSUPPORTED_FORMAT


# ----------------------------------------------------------------------
# Review B: static screenshots only
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_an_animated_webp_is_refused() -> None:
    """A screenshot is one frame. Analysing an arbitrary frame would be a
    guess about which one the user meant."""
    with pytest.raises(ImageDecodeError) as excinfo:
        verify_and_normalise(animated_webp())
    assert excinfo.value.failure is DecodeFailure.MULTI_FRAME
    assert "single" in excinfo.value.reason


@pytest.mark.unit
def test_an_animated_png_is_refused() -> None:
    with pytest.raises(ImageDecodeError) as excinfo:
        verify_and_normalise(animated_png())
    assert excinfo.value.failure is DecodeFailure.MULTI_FRAME


@pytest.mark.unit
def test_the_full_pipeline_refuses_multi_frame_input() -> None:
    with pytest.raises(ScreenshotRejectedError) as excinfo:
        accept_and_verify(animated_webp(), SLOT)
    assert excinfo.value.code == "MULTI_FRAME"


@pytest.mark.unit
def test_multi_frame_can_be_allowed_only_by_explicit_policy() -> None:
    """The refusal is a stated policy, not an assumption baked into code."""
    verified = verify_and_normalise(animated_webp(), DecodePolicy(allow_multi_frame=True))
    assert verified.frames > 1


# ----------------------------------------------------------------------
# Review A/E: decompression bombs
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_pixel_bomb_is_refused_by_the_decoder() -> None:
    """A tiny file declaring an enormous canvas.

    Refused by this module's own arithmetic against the decoder-reported
    dimensions, before `verify()` or `load()` runs - not by a limit borrowed
    from Pillow's global state.
    """
    payload = png_bytes(width=60_000, height=60_000)
    with pytest.raises(ImageDecodeError) as excinfo:
        verify_and_normalise(payload, DecodePolicy(max_pixels=1_000_000))
    assert excinfo.value.failure in {
        DecodeFailure.DECOMPRESSION_BOMB,
        DecodeFailure.EXCESSIVE_PIXELS,
    }


@pytest.mark.unit
def test_a_bomb_that_passes_signature_inspection_is_still_refused() -> None:
    """It passes the header parser's format check and fails here."""
    from app.application.vision.images import read_image_facts  # noqa: PLC0415

    payload = png_bytes(width=50_000, height=50_000)
    assert read_image_facts(payload).image_format is ImageFormat.PNG
    with pytest.raises(ImageDecodeError):
        verify_and_normalise(payload, DecodePolicy(max_pixels=1_000_000))


# ----------------------------------------------------------------------
# Concurrency: validation must not depend on process-global state
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_normal_call_does_not_touch_the_global_pixel_limit() -> None:
    """Global state is not borrowed at all any more.

    The earlier design set `Image.MAX_IMAGE_PIXELS` per call and restored it in
    a `finally`. That is safe single-threaded and unsafe otherwise: the limit
    in force during one decode was whatever another in-flight request had most
    recently written.
    """
    before = Image.MAX_IMAGE_PIXELS

    verify_and_normalise(real_png(200, 150))
    with pytest.raises(ImageDecodeError):
        verify_and_normalise(png_bytes(width=60_000, height=60_000), DecodePolicy(max_pixels=1000))

    assert before == Image.MAX_IMAGE_PIXELS, "a request mutated process-global Pillow state"


@pytest.mark.unit
def test_concurrent_validations_cannot_contaminate_each_others_policy() -> None:
    """The regression test for the defect this section exists to prevent.

    Strict and permissive policies are run simultaneously on the same input.
    Every strict call must refuse and every permissive call must accept,
    whatever interleaving occurs. Under the old design a strict call could be
    decoding while a permissive call had raised the global ceiling.
    """
    payload = real_png(1200, 1000)  # 1.2 M pixels
    strict = DecodePolicy(max_pixels=100_000)
    permissive = DecodePolicy(max_pixels=50_000_000)

    outcomes: list[tuple[str, str]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(16)

    def run(label: str, policy: DecodePolicy) -> None:
        barrier.wait()  # maximise overlap
        for _ in range(12):
            try:
                verify_and_normalise(payload, policy)
            except ImageDecodeError as error:
                result = error.failure.value
            else:
                result = "ACCEPTED"
            with lock:
                outcomes.append((label, result))

    threads = [
        threading.Thread(target=run, args=("strict", strict) if index % 2 else ("wide", permissive))
        for index in range(16)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    strict_results = {result for label, result in outcomes if label == "strict"}
    wide_results = {result for label, result in outcomes if label == "wide"}

    assert strict_results == {"EXCESSIVE_PIXELS"}, (
        f"a strict policy accepted an image under concurrency: {strict_results}"
    )
    assert wide_results == {"ACCEPTED"}, (
        f"a permissive policy was refused under concurrency: {wide_results}"
    )


@pytest.mark.unit
def test_the_global_limit_is_stable_while_many_decodes_run() -> None:
    """A concurrent observer must never see the ceiling move."""
    configure_image_safety()
    payload = real_png(120, 90)
    seen: set[int | None] = set()
    """`MAX_IMAGE_PIXELS` is `int | None` - None disables Pillow's guard
    entirely, which is itself a value this test must be able to catch."""

    stop = threading.Event()

    def observe() -> None:
        while not stop.is_set():
            seen.add(Image.MAX_IMAGE_PIXELS)

    def churn() -> None:
        for _ in range(60):
            verify_and_normalise(payload, DecodePolicy(max_pixels=7_000_000))

    observer = threading.Thread(target=observe)
    observer.start()
    workers = [threading.Thread(target=churn) for _ in range(4)]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join()
    stop.set()
    observer.join()

    assert seen == {PILLOW_PIXEL_CEILING}, (
        "the pixel ceiling changed while decodes were in flight: "
        f"{sorted(str(value) for value in seen)}"
    )


@pytest.mark.unit
def test_startup_configuration_is_idempotent() -> None:
    configure_image_safety()
    first = Image.MAX_IMAGE_PIXELS
    configure_image_safety()
    assert Image.MAX_IMAGE_PIXELS == first == PILLOW_PIXEL_CEILING


@pytest.mark.unit
def test_a_policy_may_not_exceed_the_process_ceiling() -> None:
    """Policy and backstop can never contradict each other."""
    DecodePolicy(max_pixels=PILLOW_PIXEL_CEILING)  # the boundary itself is fine
    with pytest.raises(ValueError, match="exceeds the process ceiling"):
        DecodePolicy(max_pixels=PILLOW_PIXEL_CEILING + 1)


@pytest.mark.unit
@pytest.mark.parametrize("bad", (0, -1))
def test_a_nonsensical_pixel_budget_is_refused(bad: int) -> None:
    with pytest.raises(ValueError, match="max_pixels must be positive"):
        DecodePolicy(max_pixels=bad)


@pytest.mark.unit
def test_no_global_warning_filter_is_installed() -> None:
    """Image safety warnings are promoted locally, never suppressed globally."""
    import warnings  # noqa: PLC0415

    before = list(warnings.filters)
    verify_and_normalise(real_png())
    assert list(warnings.filters) == before


# ----------------------------------------------------------------------
# Review C: normalisation
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_verified_image_is_re_encoded_to_a_static_png() -> None:
    verified = verify_and_normalise(real_jpeg())
    assert verified.normalised
    assert verified.payload.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.unit
def test_normalisation_preserves_the_visible_dimensions() -> None:
    """Lossless: nothing a chart shows may be altered."""
    verified = verify_and_normalise(real_png(1024, 768))
    with Image.open(io.BytesIO(verified.payload)) as reopened:
        assert reopened.size == (1024, 768)


@pytest.mark.unit
def test_normalisation_strips_metadata() -> None:
    """A fresh image is written rather than the original re-saved, so no EXIF
    or comment block survives."""
    buffer = io.BytesIO()
    image = Image.new("RGB", (640, 480), "white")
    image.save(buffer, format="PNG", pnginfo=_with_text())
    verified = verify_and_normalise(buffer.getvalue())
    with Image.open(io.BytesIO(verified.payload)) as reopened:
        assert "secret" not in {str(key).lower() for key in reopened.info}


def _with_text() -> object:
    from PIL.PngImagePlugin import PngInfo  # noqa: PLC0415

    info = PngInfo()
    info.add_text("secret", "should not survive normalisation")
    return info


@pytest.mark.unit
def test_appended_trailing_bytes_do_not_survive_normalisation() -> None:
    """The polyglot trick: valid image, then extra payload appended."""
    payload = real_png() + b"<?php echo 'appended'; ?>"
    verified = verify_and_normalise(payload)
    assert b"php" not in verified.payload


@pytest.mark.unit
def test_normalisation_can_be_turned_off_and_the_original_is_sent() -> None:
    original = real_png()
    verified = verify_and_normalise(original, DecodePolicy(normalise=False))
    assert not verified.normalised
    assert verified.payload == original


@pytest.mark.unit
def test_an_oversized_normalised_payload_is_refused() -> None:
    with pytest.raises(ImageDecodeError) as excinfo:
        verify_and_normalise(real_png(), DecodePolicy(max_normalised_bytes=16))
    assert excinfo.value.failure is DecodeFailure.NORMALISATION_TOO_LARGE


# ----------------------------------------------------------------------
# The pipeline as a whole
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_pipeline_returns_an_asset_and_a_payload_to_send() -> None:
    accepted = accept_and_verify(real_jpeg(), SLOT, filename="chart.jpg")
    assert accepted.asset.image_format == "JPEG"
    assert accepted.payload_media_type == "image/png"
    assert accepted.payload != real_jpeg()


@pytest.mark.unit
def test_the_size_bound_still_runs_before_the_decoder() -> None:
    with pytest.raises(ScreenshotRejectedError) as excinfo:
        accept_and_verify(b"\x00" * 4096, SLOT, policy=ImagePolicy(max_bytes=1024))
    assert excinfo.value.code == "TOO_LARGE"


@pytest.mark.unit
def test_an_unsupported_format_is_caught_by_the_preflight_before_decoding() -> None:
    with pytest.raises(ScreenshotRejectedError) as excinfo:
        accept_and_verify(gif_bytes(), SLOT)
    assert excinfo.value.code == "UNREADABLE_IMAGE"


@pytest.mark.unit
def test_no_rejection_message_contains_image_bytes() -> None:
    """Review E: image content must never reach a log or an error."""
    payload = real_png() + b"SENSITIVE-MARKER-BYTES"
    for candidate in (corrupt_body_png(), truncated_body_png(), animated_webp(), payload[:40]):
        try:
            accept_and_verify(candidate, SLOT)
        except ScreenshotRejectedError as error:
            assert "SENSITIVE-MARKER" not in str(error)
            assert "\x89PNG" not in str(error)


# ----------------------------------------------------------------------
# Review C: normalisation must not alter visible chart information
# ----------------------------------------------------------------------


def alpha_at(image: Image.Image, x: int, y: int) -> int:
    """The alpha channel of one pixel.

    `getpixel` is typed as returning a scalar or a tuple depending on mode, so
    the tuple case is asserted rather than indexed blindly.
    """
    pixel = image.getpixel((x, y))
    assert isinstance(pixel, tuple), f"expected a multi-channel pixel, got {pixel!r}"
    return int(pixel[3])


def transparent_chart(ink: tuple[int, int, int] = (20, 20, 20)) -> bytes:
    """A chart line drawn on a fully transparent background.

    This is what several charting tools export when a region is cropped, and
    it is the case that a blind ``convert("RGB")`` destroys.
    """
    image = Image.new("RGBA", (60, 40), (0, 0, 0, 0))
    for x in range(10, 50):
        image.putpixel((x, 20), (*ink, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.unit
def test_normalisation_does_not_flatten_transparency_onto_black() -> None:
    """A dark line on a transparent field must not become dark on black.

    Pillow's RGBA->RGB conversion drops alpha instead of compositing, which
    turns every transparent pixel black. Dark axis labels on a transparent
    background would arrive at the provider unreadable, with nothing raised.
    """
    verified = verify_and_normalise(transparent_chart())
    result = Image.open(io.BytesIO(verified.payload))

    assert result.mode == "RGBA", "alpha was discarded during normalisation"
    assert result.getpixel((30, 20)) == (20, 20, 20, 255), "the drawn line changed colour"
    assert alpha_at(result, 5, 5) == 0, "the transparent background became opaque"


@pytest.mark.unit
def test_a_palette_image_with_transparency_widens_rather_than_flattens() -> None:
    source = Image.open(io.BytesIO(transparent_chart())).quantize()
    buffer = io.BytesIO()
    source.save(buffer, format="PNG")

    result = Image.open(io.BytesIO(verify_and_normalise(buffer.getvalue()).payload))
    assert result.mode == "RGBA"
    assert alpha_at(result, 5, 5) == 0


@pytest.mark.unit
def test_an_opaque_image_keeps_its_exact_pixels() -> None:
    """Normalisation re-encodes the container; it must not resample content."""
    source = Image.new("RGB", (60, 40), "white")
    for x in range(10, 50):
        source.putpixel((x, 20), (255, 0, 0))
    buffer = io.BytesIO()
    source.save(buffer, format="PNG")

    result = Image.open(io.BytesIO(verify_and_normalise(buffer.getvalue()).payload))
    assert result.mode == "RGB"
    assert result.tobytes() == source.tobytes()


@pytest.mark.unit
def test_a_lossy_source_still_survives_normalisation_pixel_for_pixel() -> None:
    """JPEG loss happens before us; normalisation must not add more."""
    source = Image.open(io.BytesIO(real_jpeg()))
    result = Image.open(io.BytesIO(verify_and_normalise(real_jpeg()).payload))
    assert result.size == source.size
    assert result.convert("RGB").tobytes() == source.convert("RGB").tobytes()


@pytest.mark.unit
def test_normalisation_strips_metadata_before_anything_is_sent() -> None:
    """A screenshot can carry more than the chart.

    PNG text chunks, EXIF and the rest travel with a file and can hold a device
    name, a location or whatever a tool wrote there. Normalisation writes a
    fresh image rather than re-saving the original, so none of it reaches an
    external provider. Asserted here because it is a security property, not an
    incidental effect of the encoder.
    """
    metadata = PngInfo()
    metadata.add_text("Comment", "MARKER-IMAGE-CONTENT-9f3a")
    metadata.add_text("Author", "a name that must not leave the process")
    buffer = io.BytesIO()
    Image.new("RGB", (120, 90), "white").save(buffer, format="PNG", pnginfo=metadata)
    source = buffer.getvalue()
    assert b"MARKER-IMAGE-CONTENT-9f3a" in source, "the fixture carries no metadata"

    verified = verify_and_normalise(source)

    assert b"MARKER-IMAGE-CONTENT-9f3a" not in verified.payload
    assert b"a name that must not leave the process" not in verified.payload
    assert Image.open(io.BytesIO(verified.payload)).size == (120, 90)

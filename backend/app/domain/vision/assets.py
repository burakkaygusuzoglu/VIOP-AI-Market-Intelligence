"""The validated screenshot as a domain value (§38, §98).

A `ScreenshotAsset` is the *description* of an accepted upload: what it is, how
big, which slot, and a digest that identifies it. Two things it deliberately
is not.

**It holds no image bytes.** A domain value object outlives the request that
made it; a multi-megabyte payload inside one would be copied into every log
line, every comparison and every downstream structure that carries it. The
bytes stay in the request boundary and are passed to the vision adapter
explicitly; the asset carries the `digest` that identifies them.

**The filename is not identity.** §98 - a user-supplied name is untrusted
input, and using one as a key or a path is how directory traversal happens.
Identity is the `ScreenshotId`, generated server-side; the original name is
kept only as sanitised display metadata, and `sanitise_filename` strips it to
something that cannot be a path.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field

from app.domain.vision.slots import ScreenshotSlot

MAX_DISPLAY_FILENAME = 96
"""How much of a user-supplied name is kept for display. **Application
policy**, not a format limit."""

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]")
_COLLAPSE_DOTS = re.compile(r"\.{2,}")


def sanitise_filename(raw: str | None) -> str:
    """Reduce a user-supplied name to something safe to display.

    Never used to build a path. It strips directory separators, collapses the
    ``..`` sequences that make traversal possible, drops anything outside a
    conservative character set, and truncates. An empty or fully-stripped name
    becomes ``""`` rather than a placeholder that might be mistaken for a real
    one.
    """
    if not raw:
        return ""
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = _UNSAFE_FILENAME.sub("", name)
    name = _COLLAPSE_DOTS.sub(".", name).strip(". ")
    return name[:MAX_DISPLAY_FILENAME]


def content_digest(data: bytes) -> str:
    """A stable identifier for the bytes themselves.

    SHA-256 because it is what the standard library offers with no ambiguity
    about collisions. Used for deduplication and for tying an extraction back
    to the exact image it came from - never as a secret and never as a
    security control.
    """
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class ScreenshotId:
    """An opaque, server-generated identity.

    A wrapper rather than a bare string so a filename, a digest or a user
    input cannot be passed where an identity is expected.
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("a screenshot id cannot be blank")

    @classmethod
    def generate(cls) -> ScreenshotId:
        return cls(value=uuid.uuid4().hex)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ScreenshotAsset:
    """One accepted screenshot, described rather than carried.

    ``image_format`` is a plain string holding an `ImageFormat` value. The
    format is detected in the application layer, where the byte parsing lives,
    and the domain records what was decided without importing the parser -
    keeping this module free of anything but the standard library.
    """

    screenshot_id: ScreenshotId
    slot: ScreenshotSlot
    image_format: str
    width: int
    height: int
    byte_size: int
    digest: str
    display_filename: str = ""
    declared_media_type: str = ""
    """What the client *said* it was uploading. Kept for auditing precisely
    because it was not trusted - a mismatch with ``image_format`` is evidence
    of a misleading upload, not a reason to change the format."""

    def __post_init__(self) -> None:
        for name, value in (
            ("width", self.width),
            ("height", self.height),
            ("byte_size", self.byte_size),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive, got {value}")
        if not self.digest.strip():
            raise ValueError("a screenshot asset must carry a content digest")
        if not self.image_format.strip():
            raise ValueError("a screenshot asset must record its detected format")

    @property
    def pixels(self) -> int:
        return self.width * self.height

    @property
    def media_type_was_misleading(self) -> bool:
        """True when the client's declared type did not match the content.

        Not an error by itself - browsers get this wrong routinely - but worth
        recording, because the content is what was believed either way.
        """
        if not self.declared_media_type:
            return False
        return not self.declared_media_type.lower().endswith(self.image_format.lower())

    @property
    def identity(self) -> str:
        """The value safe to log or key on. Never the filename."""
        return self.screenshot_id.value


@dataclass(frozen=True, slots=True)
class ScreenshotSet:
    """The screenshots supplied for one analysis, at most one per slot.

    A duplicated slot is refused rather than resolved: two 1H charts is a
    question only the user can answer, and picking one would discard the other
    without saying so.
    """

    assets: tuple[ScreenshotAsset, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        slots = [asset.slot for asset in self.assets]
        if len(slots) != len(set(slots)):
            duplicated = sorted({slot.value for slot in slots if slots.count(slot) > 1})
            raise ValueError(f"more than one screenshot supplied for slot(s): {duplicated}")

    def for_slot(self, slot: ScreenshotSlot) -> ScreenshotAsset | None:
        for asset in self.assets:
            if asset.slot is slot:
                return asset
        return None

    @property
    def filled_slots(self) -> tuple[ScreenshotSlot, ...]:
        return tuple(asset.slot for asset in self.assets)

    @property
    def duplicate_digests(self) -> tuple[str, ...]:
        """Digests appearing under more than one slot.

        The same image filed as both 1H and 15M is legal but almost certainly
        a mistake, so it is surfaced rather than blocked.
        """
        seen: dict[str, int] = {}
        for asset in self.assets:
            seen[asset.digest] = seen.get(asset.digest, 0) + 1
        return tuple(sorted(digest for digest, count in seen.items() if count > 1))

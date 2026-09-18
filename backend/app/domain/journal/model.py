"""What a person may write about a simulated trade, and what it is worth.

A journal annotation is the one mutable thing attached to a paper position. It
is deliberately weak: a note and some tags, both `USER_AUTHORED`. A tag reading
"breakout" records that a person typed the word, not that the structure engine
found a breakout, and nothing in this module can reach a fill, an amount or a
state.

Pure: stdlib only.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

MAX_NOTE_LENGTH = 4000
"""Enough for a considered review of one trade, small enough to bound a row and
a response. Measured in characters after normalisation."""

MAX_TAGS = 12
MAX_TAG_LENGTH = 32
TAG_ALLOWED_EXTRA = "-_/. "
"""Letters, digits and these. No angle brackets, no quotes, no control codes:
a tag is a label, never markup."""

PROVENANCE = "USER_AUTHORED"


class JournalInputError(ValueError):
    """The annotation as written cannot be stored. Says exactly what is wrong."""


def clean_note(raw: str | None) -> str | None:
    """Normalise a note to storable plain text, or refuse it.

    Control characters are removed rather than escaped - they have no meaning in
    a note and they are how a "plain text" field smuggles terminal escapes. The
    text is otherwise kept exactly as typed: it is the person's writing.
    """
    if raw is None:
        return None
    text = unicodedata.normalize("NFC", raw).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(
        character
        for character in text
        if character in "\n\t" or unicodedata.category(character)[0] != "C"
    ).strip()
    if not text:
        return None
    if len(text) > MAX_NOTE_LENGTH:
        raise JournalInputError(
            f"a note may hold at most {MAX_NOTE_LENGTH} characters; this one has {len(text)}"
        )
    return text


def normalise_tag(raw: str) -> str:
    """One tag, comparably normalised: trimmed, single-spaced, lower case.

    Comparison has to be stable for a person who types "Breakout", "breakout "
    and "BREAKOUT" on three different days and means one thing. ``casefold``
    rather than ``lower`` because it folds more scripts correctly.
    """
    text = unicodedata.normalize("NFC", raw).strip()
    text = " ".join(text.split())
    if not text:
        raise JournalInputError("a tag cannot be empty")
    if len(text) > MAX_TAG_LENGTH:
        raise JournalInputError(
            f"a tag may hold at most {MAX_TAG_LENGTH} characters; {text[:16]!r}... is longer"
        )
    for character in text:
        if not (character.isalnum() or character in TAG_ALLOWED_EXTRA):
            raise JournalInputError(
                f"a tag may contain letters, digits, spaces and {TAG_ALLOWED_EXTRA.strip()!r}; "
                f"{character!r} is not allowed"
            )
    return text.casefold()


def clean_tags(raw: Sequence[str] | None) -> tuple[str, ...]:
    """Normalise, de-duplicate and order tags deterministically.

    Duplicates under normalisation collapse to one - "Breakout" and "breakout"
    are the same tag - and the result is sorted so two equal sets are stored and
    rendered identically.
    """
    if not raw:
        return ()
    if len(raw) > MAX_TAGS * 4:  # refuse absurd input before normalising it
        raise JournalInputError(f"at most {MAX_TAGS} tags are allowed")
    seen: set[str] = set()
    for item in raw:
        seen.add(normalise_tag(item))
    if len(seen) > MAX_TAGS:
        raise JournalInputError(f"at most {MAX_TAGS} tags are allowed; {len(seen)} were given")
    return tuple(sorted(seen))


@dataclass(frozen=True, slots=True)
class JournalAnnotation:
    """A person's own writing about one simulated position.

    ``version`` exists so two windows editing the same note cannot silently
    overwrite one another; ``created_at`` and ``updated_at`` are audit stamps,
    not market time. There is no edit history - see the phase report.
    """

    position_id: str
    note: str | None = None
    tags: tuple[str, ...] = ()
    version: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    provenance: str = PROVENANCE

    @property
    def empty(self) -> bool:
        return self.note is None and not self.tags

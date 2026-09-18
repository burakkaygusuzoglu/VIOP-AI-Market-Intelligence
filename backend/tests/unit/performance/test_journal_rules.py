"""What a person may write, and what the system refuses to do with it.

Journal content is untrusted text from the one person using the application.
The rules here are not about attackers so much as about honesty and bounds: a
note is plain text with a stated maximum, a tag is a label rather than markup,
and "Breakout" typed twice is one tag rather than two.
"""

from __future__ import annotations

import pytest

from app.domain.journal import (
    MAX_NOTE_LENGTH,
    MAX_TAG_LENGTH,
    MAX_TAGS,
    PROVENANCE,
    JournalAnnotation,
    JournalInputError,
    clean_note,
    clean_tags,
    normalise_tag,
)


class TestNotes:
    def test_a_note_is_kept_as_written(self) -> None:
        assert clean_note("Girişi erken aldım.\nStopu plana sadık tuttum.") == (
            "Girişi erken aldım.\nStopu plana sadık tuttum."
        )

    def test_surrounding_whitespace_goes_and_empty_becomes_nothing(self) -> None:
        assert clean_note("   ") is None
        assert clean_note("") is None
        assert clean_note(None) is None
        assert clean_note("  hello  ") == "hello"

    def test_control_characters_are_removed_rather_than_stored(self) -> None:
        assert clean_note("bell\x07 and null\x00 and escape\x1b[31m") == (
            "bell and null and escape[31m"
        )

    def test_newlines_and_tabs_survive_because_people_use_them(self) -> None:
        assert clean_note("one\ntwo\tthree") == "one\ntwo\tthree"
        assert clean_note("windows\r\nline") == "windows\nline"

    def test_a_note_at_the_bound_is_accepted_and_one_past_it_is_refused(self) -> None:
        assert clean_note("a" * MAX_NOTE_LENGTH) == "a" * MAX_NOTE_LENGTH

        with pytest.raises(JournalInputError) as error:
            clean_note("a" * (MAX_NOTE_LENGTH + 1))

        assert str(MAX_NOTE_LENGTH) in str(error.value)

    @pytest.mark.parametrize(
        "hostile",
        [
            "<script>alert(1)</script>",
            "<img src=x onerror=alert(1)>",
            "<b>bold</b>",
            "javascript:alert(1)",
            "'; DROP TABLE paper_positions; --",
            "{{7*7}}",
        ],
    )
    def test_markup_in_a_note_is_stored_as_the_text_it_is(self, hostile: str) -> None:
        """A note is never interpreted here; the frontend renders it as text."""
        assert clean_note(hostile) == hostile


class TestTags:
    def test_a_tag_is_normalised_for_comparison(self) -> None:
        assert normalise_tag("  Breakout  ") == "breakout"
        assert normalise_tag("Trend   Following") == "trend following"

    def test_case_and_whitespace_variants_collapse_to_one_tag(self) -> None:
        assert clean_tags(["Breakout", "breakout", " BREAKOUT "]) == ("breakout",)

    def test_tags_are_ordered_deterministically(self) -> None:
        assert clean_tags(["zeta", "alpha", "mu"]) == ("alpha", "mu", "zeta")
        assert clean_tags(["mu", "zeta", "alpha"]) == clean_tags(["alpha", "zeta", "mu"])

    def test_an_empty_tag_is_refused(self) -> None:
        with pytest.raises(JournalInputError):
            clean_tags(["ok", "   "])

    @pytest.mark.parametrize("hostile", ["<script>", "a<b", "tag'quote", 'tag"quote', "tag\x00"])
    def test_a_tag_may_not_contain_markup_or_control_characters(self, hostile: str) -> None:
        with pytest.raises(JournalInputError):
            clean_tags([hostile])

    def test_letters_digits_and_a_few_separators_are_allowed(self) -> None:
        assert clean_tags(["risk-2", "a/b", "v1.0", "iş_planı"]) == (
            "a/b",
            "iş_planı",
            "risk-2",
            "v1.0",
        )

    def test_too_many_tags_are_refused_rather_than_truncated(self) -> None:
        with pytest.raises(JournalInputError) as error:
            clean_tags([f"tag{index}" for index in range(MAX_TAGS + 1)])

        assert str(MAX_TAGS) in str(error.value)

    def test_a_tag_at_the_length_bound_is_accepted_and_one_past_it_is_not(self) -> None:
        assert clean_tags(["a" * MAX_TAG_LENGTH]) == ("a" * MAX_TAG_LENGTH,)

        with pytest.raises(JournalInputError):
            clean_tags(["a" * (MAX_TAG_LENGTH + 1)])

    def test_absurd_input_is_refused_before_it_is_normalised(self) -> None:
        with pytest.raises(JournalInputError):
            clean_tags([f"tag{index}" for index in range(1000)])


class TestAnnotationIsUserAuthored:
    def test_an_annotation_declares_who_wrote_it(self) -> None:
        annotation = JournalAnnotation(position_id="PP-1", note="my thinking", tags=("breakout",))

        assert annotation.provenance == PROVENANCE == "USER_AUTHORED"
        assert not annotation.empty

    def test_an_untouched_annotation_is_empty_at_version_zero(self) -> None:
        annotation = JournalAnnotation(position_id="PP-1")

        assert annotation.empty
        assert annotation.version == 0

    def test_the_annotation_carries_no_financial_field(self) -> None:
        """A tag is a label a person typed, never a verified classification."""
        fields = set(JournalAnnotation.__dataclass_fields__)

        for forbidden in (
            "realized_gross",
            "realized_net",
            "state",
            "outcome",
            "fill_price",
            "quantity",
            "direction",
            "setup",
            "regime",
        ):
            assert forbidden not in fields

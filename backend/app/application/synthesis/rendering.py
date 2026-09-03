"""Claude selects the fact; Python renders the number (§4, §5).

The validator already stops an invented figure becoming **calculation
authority**: no schema field holds a number, and prose is scanned. That
protects the engines. It does not, by itself, protect the *reader* - a model
could still write "support sits at 61.27" and a user would reasonably take the
digits as authoritative merely because they appeared in an authoritative-looking
paragraph.

The fix is structural rather than lexical, because a lexical one would mean
parsing every way a number can be written in English and Turkish, which is
unbounded and would still miss "around sixty-one".

## The mechanism

A narrative does not write the number. It writes a **placeholder**:

    "RSI is stretched at {{FACT-RSI-BIAS}} while the trend holds"

and this module resolves it against the deterministic fact registry, producing
alternating segments:

    TextSegment("RSI is stretched at ")
    FactSegment(ref_id="FACT-RSI-BIAS", name="RSI_BIAS", value="61.27")
    TextSegment(" while the trend holds")

The model chose *which fact was relevant* - genuine interpretive work, and the
thing it is good at. Every digit came from Python. A consumer renders
`FactSegment` distinctly from `TextSegment`, so a reader can see which parts of
a sentence are AI narrative and which are measured values.

## Raw value != display value

`FactSegment.value` is the **raw authoritative representation**, not a polished
user-facing one. A float-derived indicator renders as `24.658334322196957`,
because `Decimal(str(value))` is exact with respect to what Phase 1 computed and
rounding it here would silently alter a number this layer does not own.

Presentation formatting - how many decimals an RSI or a price shows, per
instrument and per field - is a deterministic, versioned decision that belongs
with the phase that owns display. **Phase 8 carry-forward:** define that
formatting, apply it above this layer, and keep the raw value reachable so an
audit can still see what was calculated. Nothing here should start rounding in
the meantime.

## What this deliberately does not do

It does not stop a model writing a bare number in prose. Nothing structural
can. What it does is remove the *reason* to: if the model needs a real figure,
there is a correct way to obtain one, and the validator rejects a bare number
that is not in the registry anyway. The two work together - this makes correct
behaviour easy, the validator makes incorrect behaviour invalid.

An unknown placeholder is a hard failure, not a passthrough. Leaving
`{{FACT-NOPE}}` visible in a rendered narrative would be a fabricated citation
that merely looks broken instead of looking authoritative.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.application.synthesis.context import SynthesisContext

REFERENCE_PLACEHOLDER = re.compile(r"\{\{\s*((?:FACT|VIS)-[A-Z0-9_-]+)\s*\}\}")
"""How a narrative cites a value it must not write itself.

Two kinds, because there are two kinds of number a reader might be shown:

* `FACT-…` - a figure a deterministic engine calculated.
* `VIS-…`  - a figure a model *read off a picture*.

Both are rendered by Python from their own registry. A vision reading is not
authoritative and must not be presented as if it were, but it is still a real
observation a user may need to see - and if the model retypes the digits, the
digits are model-controlled again. So it gets a reference too, and the segment
it produces says which kind it is.

Doubled braces because they do not occur in ordinary Turkish or English prose,
so nothing a model writes naturally collides with the syntax.

Hyphens are part of an id (`FACT-RSI-BIAS`), not a separator. The first version
of this pattern omitted them, so every real placeholder silently failed to match
and passed through as literal text - a citation that reached a reader looking
broken instead of being resolved or rejected. Caught by rendering against a
real assembled context rather than a hand-written fixture.
"""

FACT_PLACEHOLDER = REFERENCE_PLACEHOLDER
"""Backwards-compatible alias. Both kinds share one pattern."""


@dataclass(frozen=True, slots=True)
class TextSegment:
    """Narrative written by the model. Interpretation, never measurement."""

    text: str

    @property
    def is_fact(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class FactSegment:
    """An authoritative value rendered by Python from the fact registry."""

    ref_id: str
    name: str
    value: str
    unit: str = ""

    @property
    def is_fact(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class ObservationSegment:
    """A value a model read off a chart, rendered by Python from the registry.

    Kept distinct from `FactSegment` on purpose. Both are Python-rendered, so
    neither can be altered by narrative; but one was *calculated* and the other
    was *read from a picture*, and a reader shown "RSI 99" deserves to know
    which. `is_authoritative` is `False` here and a consumer is expected to
    present it accordingly.
    """

    ref_id: str
    field_name: str
    value: str
    source: str
    """The `DataSourcePriority` this observation entered at."""

    @property
    def is_fact(self) -> bool:
        return True

    @property
    def is_authoritative(self) -> bool:
        """Always `False`. A screenshot reading never outranks structured data."""
        return False


type NarrativeSegment = TextSegment | FactSegment | ObservationSegment


class UnknownFactReferenceError(ValueError):
    """A narrative cited a fact or observation the context does not hold.

    A hard failure. Rendering the placeholder as-is would leave a fabricated
    citation on screen; substituting nothing would silently drop a claim the
    model thought it was making.
    """

    def __init__(self, ref_id: str) -> None:
        self.ref_id = ref_id
        super().__init__(f"{ref_id} is not a fact or observation in this context")


def _resolve(ref_id: str, context: SynthesisContext) -> NarrativeSegment:
    """One reference to one Python-rendered segment."""
    if ref_id.startswith("VIS-"):
        observation = context.observation(ref_id)
        if observation is None:
            raise UnknownFactReferenceError(ref_id)
        return ObservationSegment(
            ref_id=observation.ref.ref_id,
            field_name=observation.field_name,
            # The observation's own recorded text, neutralised by
            # `UntrustedText` - never re-typed by the narrative.
            value=observation.value.safe_content,
            source=observation.source_priority.name,
        )

    fact = context.fact(ref_id)
    if fact is None:
        raise UnknownFactReferenceError(ref_id)
    return FactSegment(ref_id=fact.ref_id, name=fact.name, value=fact.rendered, unit=fact.unit)


def render_segments(text: str, context: SynthesisContext) -> tuple[NarrativeSegment, ...]:
    """Split a narrative into model text and Python-rendered values.

    Raises `UnknownFactReferenceError` for a placeholder naming something the
    context does not hold.
    """
    segments: list[NarrativeSegment] = []
    cursor = 0

    for match in REFERENCE_PLACEHOLDER.finditer(text):
        if match.start() > cursor:
            segments.append(TextSegment(text[cursor : match.start()]))
        segments.append(_resolve(match.group(1), context))
        cursor = match.end()

    if cursor < len(text):
        segments.append(TextSegment(text[cursor:]))
    return tuple(segments)


def render_plain(text: str, context: SynthesisContext) -> str:
    """The narrative as one string, with every placeholder resolved.

    For consumers that cannot render segments. The distinction between model
    text, calculated value and screenshot reading is lost, so `render_segments`
    is preferred wherever the consumer can show it.
    """
    return "".join(
        segment.text if isinstance(segment, TextSegment) else segment.value
        for segment in render_segments(text, context)
    )


def referenced_fact_ids(text: str) -> tuple[str, ...]:
    """Every fact or observation a narrative cites, in order."""
    return tuple(match.group(1) for match in REFERENCE_PLACEHOLDER.finditer(text))

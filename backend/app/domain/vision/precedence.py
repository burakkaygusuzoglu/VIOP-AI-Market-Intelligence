"""Source precedence, and the conflicts it must not hide (§1, §38, §65).

§38's worked example is the whole module in three lines:

    structured RSI = 61.27
    screenshot reads RSI = 63.2
    the structured value wins

§65 states the hierarchy that produces that outcome, and Phase 0 already
encoded it as `DataSourcePriority` - structured market data, then user
confirmation, then verified contract metadata, then screenshot extraction,
then visual inference. **That enum is reused rather than re-declared.** A
second ordering of the same five sources would eventually disagree with the
first, and the disagreement would be silent.

What this module adds is the two halves of §13:

**The stronger source wins authority.** `resolve` returns exactly one
authoritative value, chosen by rank, never by recency and never by confidence -
a model reporting 0.99 does not outrank a computed number.

**The disagreement survives.** Losing candidates are not discarded. They are
returned as `ValueConflict` entries so a user can see that the screenshot said
something else, which is the difference between a system that is right and a
system that can be checked.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum, unique

from app.domain.common.enums import DataSourcePriority
from app.domain.vision.extraction import ExtractedValue, VisionConfidence


@unique
class ValueKind(StrEnum):
    """How two claims about this field should be compared (review D).

    Phase 6A compared every value as text, which the human review correctly
    flagged: `61.27` and `61.270` are the same reading written differently,
    and reporting them as a conflict would put a disagreement in front of a
    user that does not exist.
    """

    NUMERIC = "NUMERIC"
    """Compared as an exact `Decimal`. `61.27`, `61.270` and `"61.27"` are one
    observation; `61.27` and `63.2` are two."""

    CATEGORICAL = "CATEGORICAL"
    """Compared as trimmed, case-sensitive text. A symbol, a timeframe or a
    structure label - `ASELS` and `asels` are deliberately *not* merged,
    because instrument codes are not case-insensitive in general and assuming
    so would be an exchange convention this project has not verified."""


@dataclass(frozen=True, slots=True)
class SourcedValue:
    """One claim about one field, from one source.

    Holds **both** representations, and the distinction matters:

    ``value`` is what the source actually said, preserved verbatim for
    auditability - a screenshot that reads "61.270" should still display as
    "61.270" when a user asks what the picture showed.

    ``comparable`` is the canonical form used to decide whether two claims
    agree. For a numeric field that is an exact `Decimal`; there is **no
    tolerance**, because the specification provides none and inventing one
    would silently merge genuinely different readings.
    """

    field: str
    value: str
    source: DataSourcePriority
    kind: ValueKind = ValueKind.CATEGORICAL
    """Categorical by default. A field is only compared numerically when a
    caller says its semantics are numeric - guessing from the text would make
    a symbol like "600" numeric by accident."""

    detail: str = ""
    confidence: VisionConfidence | None = None
    """Present only for observations that carry one. Recorded for display and
    never used to decide precedence - see the module docstring."""

    def __post_init__(self) -> None:
        if not self.field.strip():
            raise ValueError("a sourced value must name its field")
        if not self.value.strip():
            raise ValueError(f"the claim about {self.field} carries no value")
        if self.kind is ValueKind.NUMERIC and self.numeric is None:
            raise ValueError(
                f"the claim about {self.field} is declared numeric but "
                f"{self.value!r} is not a number"
            )

    @property
    def numeric(self) -> Decimal | None:
        """The value as an exact `Decimal`, or ``None`` if it is not one.

        **Never a float.** A float would make 61.27 and 61.270 compare equal
        for the wrong reason and would eventually make two genuinely different
        financial values compare equal too.
        """
        try:
            parsed = Decimal(self.value.strip())
        except InvalidOperation:
            return None
        return parsed if parsed.is_finite() else None

    @property
    def comparable(self) -> str:
        """The canonical form two claims are compared on.

        For numerics, `normalize()` collapses trailing zeros so 61.270 and
        61.27 produce the same key; the exponent is pinned so 6.127E+1 does
        not become a different string than 61.27.
        """
        if self.kind is ValueKind.NUMERIC:
            parsed = self.numeric
            if parsed is not None:
                normalised = parsed.normalize()
                # normalize() can produce an exponent form for integers
                # (1E+2); restoring the plain form keeps the key stable.
                return format(normalised, "f")
        return self.value.strip()

    def agrees_with(self, other: SourcedValue) -> bool:
        """Whether two claims are the same observation.

        Kinds must match: a numeric claim and a categorical one about the same
        field are not comparable, and treating them as equal would depend on
        which one happened to be declared first.
        """
        if self.kind is not other.kind:
            return False
        return self.comparable == other.comparable

    @classmethod
    def from_observation(
        cls, observation: ExtractedValue, kind: ValueKind = ValueKind.CATEGORICAL
    ) -> SourcedValue:
        """Lift a vision observation into the precedence vocabulary.

        The rank comes from `ObservationKind`, so a directly visible reading
        and a visual inference enter at their own levels rather than sharing
        one.

        ``kind`` is the caller's statement about the field's semantics. It
        defaults to categorical because a screenshot reading is text until
        something says otherwise, and a value that will not parse as a number
        is refused rather than silently downgraded.
        """
        return cls(
            field=observation.field.value,
            value=observation.value,
            source=observation.source_priority,
            kind=kind,
            detail=observation.note,
            confidence=observation.confidence,
        )

    def outranks(self, other: SourcedValue) -> bool:
        return self.source.wins_over(other.source)


@dataclass(frozen=True, slots=True)
class ValueConflict:
    """A claim that lost, and what beat it.

    §13: never discard a disagreement silently. This is what remains
    inspectable after `resolve` has chosen.
    """

    field: str
    authoritative: SourcedValue
    rejected: SourcedValue

    @property
    def describe(self) -> str:
        return (
            f"{self.field}: {self.authoritative.source.name} reports "
            f"{self.authoritative.value!r} and takes authority; "
            f"{self.rejected.source.name} reported {self.rejected.value!r}"
        )


@dataclass(frozen=True, slots=True)
class ResolvedValue:
    """The authoritative claim for one field, plus every claim it displaced.

    The tuple defaults are literals rather than ``field(default_factory=...)``
    because this class declares an attribute *named* ``field`` - §38's own
    vocabulary - which shadows the ``dataclasses.field`` import inside the
    class body. An empty tuple is immutable, so the factory buys nothing here
    anyway.
    """

    field: str
    authoritative: SourcedValue
    conflicts: tuple[ValueConflict, ...] = ()
    agreeing: tuple[SourcedValue, ...] = ()
    """Weaker sources that said the same thing. Not conflicts - corroboration,
    and worth showing as such."""

    unusable: tuple[str, ...] = ()
    """Claims that could not enter the ranking at all, each with a reason.

    A model asked for a price can answer "roughly 61 or so". That is not a
    competing value - there is nothing to compare - but it is also not
    nothing, and dropping it silently would let a screenshot appear to have
    said nothing about a field it did in fact comment on. Recorded here so the
    reading survives without ever becoming a candidate for authority.
    """

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)

    @property
    def value(self) -> str:
        return self.authoritative.value

    @property
    def source(self) -> DataSourcePriority:
        return self.authoritative.source


def resolve(candidates: tuple[SourcedValue, ...]) -> ResolvedValue:
    """Pick the authoritative claim for one field and keep the rest.

    All candidates must describe the same field; mixing fields would produce a
    winner that answers a different question than the losers.

    Ties are broken by **input order**, not by confidence: two sources of equal
    rank disagreeing is a genuine ambiguity, and letting a model's self-report
    settle it would make the weaker-but-more-confident claim win.
    """
    if not candidates:
        raise ValueError("no candidates to resolve")

    fields = {item.field for item in candidates}
    if len(fields) != 1:
        raise ValueError(f"candidates describe different fields: {sorted(fields)}")

    winner = candidates[0]
    for candidate in candidates[1:]:
        if candidate.outranks(winner):
            winner = candidate

    conflicts: list[ValueConflict] = []
    agreeing: list[SourcedValue] = []
    for candidate in candidates:
        if candidate is winner:
            continue
        # Canonical comparison, so 61.27 and 61.270 corroborate rather than
        # conflict (review D). Precedence is untouched by this: the winner was
        # already chosen by rank above, and agreement only decides whether the
        # loser is recorded as support or as a disagreement.
        if candidate.agrees_with(winner):
            agreeing.append(candidate)
        else:
            conflicts.append(
                ValueConflict(field=winner.field, authoritative=winner, rejected=candidate)
            )

    return ResolvedValue(
        field=winner.field,
        authoritative=winner,
        conflicts=tuple(conflicts),
        agreeing=tuple(agreeing),
    )


def resolve_all(candidates: tuple[SourcedValue, ...]) -> tuple[ResolvedValue, ...]:
    """Resolve every field present, in first-seen order."""
    order: list[str] = []
    grouped: dict[str, list[SourcedValue]] = {}
    for candidate in candidates:
        if candidate.field not in grouped:
            grouped[candidate.field] = []
            order.append(candidate.field)
        grouped[candidate.field].append(candidate)
    return tuple(resolve(tuple(grouped[name])) for name in order)


def conflicts_of(resolved: tuple[ResolvedValue, ...]) -> tuple[ValueConflict, ...]:
    """Every surviving disagreement across a resolution."""
    return tuple(conflict for item in resolved for conflict in item.conflicts)

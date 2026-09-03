"""Claude selects the fact; Python renders the number (§4, §5).

The validator stops an invented figure becoming *calculation authority*. These
tests cover the other half: what a **reader** sees. A model could write
"support sits at 61.27" and a user would reasonably take the digits as
authoritative merely because they appeared in an authoritative-looking
paragraph.

So a narrative cites `{{FACT-…}}` and this layer resolves it against the
deterministic registry, producing segments a consumer can render distinctly.
Every digit shown comes from Python; the model contributed the choice of which
fact mattered.

Every value is TEST_FIXTURE data.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.application.synthesis.context import build_synthesis_context
from app.application.synthesis.rendering import (
    FactSegment,
    ObservationSegment,
    TextSegment,
    UnknownFactReferenceError,
    referenced_fact_ids,
    render_plain,
    render_segments,
)
from app.application.synthesis.validator import RejectionCode, validate_synthesis
from app.domain.analysis.engine import analyse_multi_timeframe
from app.domain.analysis.evidence import EvidenceDirection
from app.domain.analysis.quality import score_setup
from app.domain.analysis.timeframes import ROLES_BROADEST_FIRST
from app.domain.suitability.no_trade import assess_no_trade
from app.domain.synthesis.actions import FinalAction
from tests.factories_analysis import BULLISH_DRIFT, market_view
from tests.factories_synthesis import draft, minimal_context, numeric_fact, sizing


def context_with_rsi(value: str = "61.27"):  # type: ignore[no-untyped-def]
    return replace(
        minimal_context(),
        numeric_facts=(numeric_fact("RSI_BIAS", Decimal(value), unit="0-100"),),
    )


# ----------------------------------------------------------------------
# §5: the mandated probe
# ----------------------------------------------------------------------


def context_with_both(structured: str = "61.27", observed: str = "99"):  # type: ignore[no-untyped-def]
    """A deterministic RSI and a conflicting screenshot reading of the same field.

    The exact situation §1 names: both numbers must reach the reader, both from
    Python, and visibly not as the same kind of thing.
    """
    from app.application.synthesis.context import ContextVisionObservation  # noqa: PLC0415
    from app.application.synthesis.untrusted import (  # noqa: PLC0415
        UntrustedOrigin,
        UntrustedText,
    )
    from app.domain.common.enums import DataSourcePriority  # noqa: PLC0415
    from app.domain.synthesis.references import ReferenceKind  # noqa: PLC0415
    from app.domain.vision.extraction import ObservationKind  # noqa: PLC0415
    from tests.factories_synthesis import make_ref  # noqa: PLC0415

    ref = make_ref(ReferenceKind.VISION_OBSERVATION, 1)
    return replace(
        minimal_context(),
        numeric_facts=(numeric_fact("RSI_BIAS", Decimal(structured), unit="0-100"),),
        vision_observations=(
            ContextVisionObservation(
                ref=ref,
                field_name="INDICATOR_READING",
                value=UntrustedText(
                    origin=UntrustedOrigin.SCREENSHOT_TEXT, content=observed, ref_id=ref.ref_id
                ),
                observation_kind=ObservationKind.DIRECTLY_VISIBLE,
                source_priority=DataSourcePriority.SCREENSHOT_EXTRACTED,
                confidence=None,
            ),
        ),
    )


@pytest.mark.unit
def test_vision_and_deterministic_values_are_both_rendered_from_their_registries() -> None:
    """§1's example: neither number is model-controlled prose.

    Vision read 99, the engine calculated 61.27, and the narrative supplies
    neither digit - it cites two references and Python renders both.
    """
    context = context_with_both()
    vision_ref = context.vision_observations[0].ref.ref_id
    narrative = (
        f"Görüntüdeki okuma {{{{{vision_ref}}}}}; "
        "hesaplanan değer {{FACT-RSI_BIAS}} seviyesinde."
    )

    segments = render_segments(narrative, context)
    observations = [item for item in segments if isinstance(item, ObservationSegment)]
    facts = [item for item in segments if isinstance(item, FactSegment)]

    assert [item.value for item in observations] == ["99"]
    assert [item.value for item in facts] == ["61.27"]
    assert "99" not in "".join(item.text for item in segments if isinstance(item, TextSegment)), (
        "the vision digits must not be model prose"
    )


@pytest.mark.unit
def test_the_two_renderings_stay_visibly_distinct() -> None:
    """One was calculated, the other read off a picture. A reader must see which."""
    context = context_with_both()
    vision_ref = context.vision_observations[0].ref.ref_id
    segments = render_segments(f"{{{{{vision_ref}}}}} / {{{{FACT-RSI_BIAS}}}}", context)

    observation = next(item for item in segments if isinstance(item, ObservationSegment))
    fact = next(item for item in segments if isinstance(item, FactSegment))

    assert not observation.is_authoritative
    assert observation.source == "SCREENSHOT_EXTRACTED"
    assert observation.field_name == "INDICATOR_READING"
    # Both are Python-rendered values, so both report `is_fact`; what separates
    # them is that only one carries a provenance caveat.
    assert observation.is_fact and fact.is_fact
    assert observation.value != fact.value, "the fixture must actually disagree"
    assert fact.unit == "0-100" and fact.name == "RSI_BIAS"


@pytest.mark.unit
def test_a_narrative_cannot_alter_either_value() -> None:
    """The digits come from the registries; the narrative only points."""
    context = context_with_both()
    vision_ref = context.vision_observations[0].ref.ref_id

    for segment in render_segments(f"{{{{{vision_ref}}}}} {{{{FACT-RSI_BIAS}}}}", context):
        if isinstance(segment, FactSegment):
            calculated = context.fact(segment.ref_id)
            assert calculated is not None
            assert segment.value == calculated.rendered
        elif isinstance(segment, ObservationSegment):
            observed = context.observation(segment.ref_id)
            assert observed is not None
            assert segment.value == observed.value.safe_content


@pytest.mark.unit
def test_the_conflict_does_not_touch_calculation_state() -> None:
    """§1: rendering is presentation. Precedence is unchanged by it."""
    from app.domain.common.enums import DataSourcePriority  # noqa: PLC0415

    context = context_with_both()
    observation = context.vision_observations[0]

    assert DataSourcePriority.STRUCTURED_MARKET_DATA.wins_over(observation.source_priority)
    assert context.fact("FACT-RSI_BIAS") is not None


@pytest.mark.unit
def test_an_unknown_observation_placeholder_invalidates_the_synthesis() -> None:
    context = context_with_both()
    report = validate_synthesis(
        draft(action=FinalAction.WAIT, summary="Okuma {{VIS-NOTREAL}}."), context
    )
    assert RejectionCode.UNKNOWN_REFERENCE in report.codes


@pytest.mark.unit
def test_the_authoritative_rsi_is_rendered_even_when_vision_disagrees() -> None:
    """structured 61.27, Claude cites the fact → 61.27 from Python."""
    context = context_with_rsi("61.27")
    narrative = "Hesaplanan değer {{FACT-RSI_BIAS}} seviyesinde."

    segments = render_segments(narrative, context)
    facts = [item for item in segments if isinstance(item, FactSegment)]

    assert len(facts) == 1
    assert facts[0].value == "61.27", "the rendered value must come from the registry"
    assert facts[0].ref_id == "FACT-RSI_BIAS"
    assert render_plain(narrative, context).endswith("61.27 seviyesinde.")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "value", "unit"),
    (
        ("SETUP_QUALITY", "72", "points 0-100"),
        ("RISK_AMOUNT", "1000", "account currency"),
        ("ALLOWED_CONTRACTS", "4", "contracts"),
        ("STOP_DISTANCE", "2.5", "price"),
        ("MAXIMUM_BY_MARGIN", "4", "contracts"),
    ),
)
def test_each_representative_fact_renders_from_python(name: str, value: str, unit: str) -> None:
    """§5's list: quality, risk amount, sizing, stop distance."""
    context = replace(
        minimal_context(), numeric_facts=(numeric_fact(name, Decimal(value), unit=unit),)
    )
    rendered = render_segments(f"Değer {{{{FACT-{name}}}}} olarak ölçüldü.", context)
    facts = [item for item in rendered if isinstance(item, FactSegment)]

    assert len(facts) == 1
    assert facts[0].value == value
    assert facts[0].unit == unit


@pytest.mark.unit
def test_a_decimal_renders_exactly_and_never_through_a_float() -> None:
    context = replace(minimal_context(), numeric_facts=(numeric_fact("PRICE", Decimal("61.270")),))
    facts = [
        item for item in render_segments("{{FACT-PRICE}}", context) if isinstance(item, FactSegment)
    ]
    assert facts[0].value == "61.27", "trailing zeros normalise; the value stays exact"


# ----------------------------------------------------------------------
# §4: the four minimum proofs
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_authoritative_values_come_only_from_the_fact_registry() -> None:
    """Every `FactSegment` value is looked up, never taken from the narrative."""
    context = context_with_rsi("61.27")
    narrative = "Model 99.99 yazdı ama {{FACT-RSI_BIAS}} otoritedir."

    for segment in render_segments(narrative, context):
        if isinstance(segment, FactSegment):
            fact = context.fact(segment.ref_id)
            assert fact is not None
            assert segment.value == fact.rendered


@pytest.mark.unit
def test_a_hallucinated_prose_number_never_becomes_a_fact_segment() -> None:
    """It stays text: unattributed, and visibly not a measured value."""
    context = context_with_rsi()
    segments = render_segments("Destek 58.40 seviyesinde.", context)

    assert all(isinstance(item, TextSegment) for item in segments)
    assert not any(isinstance(item, FactSegment) for item in segments)


@pytest.mark.unit
def test_a_hallucinated_prose_number_is_still_rejected_by_the_validator() -> None:
    """The two defences are independent: rendering, and validation."""
    context = context_with_rsi()
    report = validate_synthesis(
        draft(action=FinalAction.WAIT, summary="Destek 58.40 seviyesinde."), context
    )
    assert RejectionCode.UNSUPPORTED_NUMERIC_CLAIM in report.codes


@pytest.mark.unit
def test_narrative_and_rendered_facts_are_distinguishable_by_type() -> None:
    """§4: a consumer can show which parts are AI prose and which are measured."""
    context = context_with_rsi()
    segments = render_segments("RSI {{FACT-RSI_BIAS}} seviyesinde.", context)

    assert [item.is_fact for item in segments] == [False, True, False]
    assert {type(item).__name__ for item in segments} == {"TextSegment", "FactSegment"}


# ----------------------------------------------------------------------
# An unknown placeholder is a hard failure
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_an_unknown_fact_placeholder_raises_rather_than_passing_through() -> None:
    """Leaving it visible would be a fabricated citation that looks broken."""
    with pytest.raises(UnknownFactReferenceError, match="FACT-NOPE"):
        render_segments("Değer {{FACT-NOPE}}.", context_with_rsi())


@pytest.mark.unit
def test_an_unknown_fact_placeholder_invalidates_the_synthesis() -> None:
    context = context_with_rsi()
    report = validate_synthesis(
        draft(action=FinalAction.WAIT, summary="Değer {{FACT-NOPE}}."), context
    )
    assert RejectionCode.UNKNOWN_REFERENCE in report.codes


@pytest.mark.unit
def test_a_known_placeholder_passes_validation() -> None:
    context = context_with_rsi()
    report = validate_synthesis(
        draft(action=FinalAction.WAIT, summary="RSI {{FACT-RSI_BIAS}} seviyesinde."), context
    )
    assert RejectionCode.UNKNOWN_REFERENCE not in report.codes
    assert RejectionCode.UNSUPPORTED_NUMERIC_CLAIM not in report.codes


@pytest.mark.unit
def test_placeholders_are_extracted_in_order() -> None:
    assert referenced_fact_ids("{{FACT-A}} then {{ FACT-B }}") == ("FACT-A", "FACT-B")


@pytest.mark.unit
def test_text_without_placeholders_is_one_segment() -> None:
    segments = render_segments("Hiç referans yok.", context_with_rsi())
    assert len(segments) == 1
    assert isinstance(segments[0], TextSegment)


# ----------------------------------------------------------------------
# The registry is populated from real engine output
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_real_assembly_publishes_indicator_facts_a_narrative_can_cite() -> None:
    """Otherwise the mechanism would be theoretical."""
    analysis = analyse_multi_timeframe(
        tuple(market_view(role, drift=BULLISH_DRIFT) for role in ROLES_BROADEST_FIRST)
    )
    direction = EvidenceDirection.BULLISH
    context = build_synthesis_context(
        analysis,
        direction,
        assess_no_trade(analysis, direction),
        setup_quality=score_setup(analysis.fused, direction),
        sizing=sizing(),
    )

    names = {fact.name for fact in context.numeric_facts}
    assert any(name.startswith("RSI_") for name in names)
    assert any(name.startswith("ATR_") for name in names)
    assert "SETUP_QUALITY" in names
    assert "ALLOWED_CONTRACTS" in names

    rsi = next(fact for fact in context.numeric_facts if fact.name.startswith("RSI_"))
    facts = [
        item
        for item in render_segments("RSI {{" + rsi.ref_id + "}}.", context)
        if isinstance(item, FactSegment)
    ]
    assert facts[0].value == rsi.rendered


@pytest.mark.unit
def test_every_published_fact_is_a_citable_reference() -> None:
    context = replace(
        minimal_context(), numeric_facts=(numeric_fact("SETUP_QUALITY", Decimal("72")),)
    )
    assert context.numeric_facts[0].ref_id in context.ref_ids

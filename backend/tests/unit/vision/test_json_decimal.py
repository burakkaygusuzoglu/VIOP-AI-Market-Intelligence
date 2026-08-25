"""JSON numbers reach the domain as exact decimals, or not at all (§4).

The hazard is narrow and expensive. A model may answer ``{"value": 61.27}``
instead of ``{"value": "61.27"}``. Parsed the default way that becomes a binary
float, and `Decimal(61.27)` is

    61.27000000000000312638803734444081783294677734375

which is *not* equal to a structured `Decimal("61.27")`. The screenshot and the
market data would then be reported as conflicting when they say the same thing,
and no tolerance may be used to paper over it - a tolerance wide enough to hide
this is wide enough to merge two genuinely different prices.

So the response is parsed with ``parse_float=Decimal``, which hands the JSON
module's own lexical token to `Decimal`, and a bare `float` is refused
outright so that path cannot be bypassed.

Every value here is TEST_FIXTURE data.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.application.vision.schemas import VisionExtractionSchema
from app.domain.common.enums import DataSourcePriority
from app.domain.vision.assets import ScreenshotId
from app.domain.vision.extraction import ObservedField
from app.domain.vision.precedence import SourcedValue, ValueKind, resolve

SCREENSHOT_ID = ScreenshotId("fixture-screenshot-id")


def response(value_literal: str) -> str:
    """A vision response whose LAST_PRICE is written exactly as given."""
    return (
        '{"slot": "1H", "detected_timeframe": "1H", "values": ['
        '{"field": "LAST_PRICE", "value": ' + value_literal + ", "
        '"kind": "DIRECTLY_VISIBLE", "confidence": 0.9}], '
        '"unreadable": [], "model": "fixture-vision-model"}'
    )


def parse(value_literal: str) -> VisionExtractionSchema:
    """The adapter's own parse path, in one line."""
    parsed = json.loads(response(value_literal), parse_float=Decimal, parse_int=Decimal)
    return VisionExtractionSchema.model_validate(parsed)


def observed_price(value_literal: str) -> str:
    extraction = parse(value_literal).to_domain(SCREENSHOT_ID)
    return extraction.of_field(ObservedField.LAST_PRICE)[0].value


def against_structured(screen_value: str, structured: str):  # type: ignore[no-untyped-def]
    """Resolve a screenshot reading against a structured market figure."""
    return resolve(
        (
            SourcedValue(
                field=ObservedField.LAST_PRICE.value,
                value=screen_value,
                source=DataSourcePriority.SCREENSHOT_EXTRACTED,
                kind=ValueKind.NUMERIC,
            ),
            SourcedValue(
                field=ObservedField.LAST_PRICE.value,
                value=structured,
                source=DataSourcePriority.STRUCTURED_MARKET_DATA,
                kind=ValueKind.NUMERIC,
            ),
        )
    )


# ----------------------------------------------------------------------
# The lexical value survives the parse
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("literal", "expected"),
    (
        ("61.27", "61.27"),
        ("61.270", "61.270"),
        ('"61.27"', "61.27"),
        ('"61.270"', "61.270"),
        ("61", "61"),
        ("0.00000001", "0.00000001"),
        ("123456789012345678901234567890.12345", "123456789012345678901234567890.12345"),
        ("-3.5", "-3.5"),
    ),
)
def test_a_json_number_keeps_its_exact_written_form(literal: str, expected: str) -> None:
    assert observed_price(literal) == expected


@pytest.mark.unit
def test_no_float_noise_is_introduced_anywhere() -> None:
    """The specific corruption this whole module exists to prevent."""
    assert "61.2700000" not in observed_price("61.27")
    assert Decimal(observed_price("61.27")) == Decimal("61.27")


@pytest.mark.unit
def test_an_exponent_form_is_rendered_as_plain_digits() -> None:
    """`str(Decimal("1E+2"))` is "1E+2", which would not equal "100" as text."""
    assert observed_price("1E+2") == "100"


# ----------------------------------------------------------------------
# The required conflict matrix
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("literal", ("61.27", "61.270", '"61.27"', '"61.2700"', "6.127E+1"))
def test_equivalent_representations_never_conflict_with_the_structured_decimal(
    literal: str,
) -> None:
    resolved = against_structured(observed_price(literal), "61.27")
    assert not resolved.has_conflict, f"{literal} created a formatting-only conflict"
    assert resolved.source is DataSourcePriority.STRUCTURED_MARKET_DATA
    assert resolved.value == "61.27"


@pytest.mark.unit
@pytest.mark.parametrize("literal", ("61.28", "61.271", "610.27", "-61.27"))
def test_a_genuinely_different_value_still_conflicts(literal: str) -> None:
    resolved = against_structured(observed_price(literal), "61.27")
    assert resolved.has_conflict, f"{literal} was wrongly merged with 61.27"


@pytest.mark.unit
def test_the_screenshots_own_representation_is_preserved_in_the_agreement() -> None:
    """§D: agreeing on the value does not mean discarding how it was written."""
    resolved = against_structured(observed_price("61.270"), "61.27")
    assert not resolved.has_conflict
    assert any(claim.value == "61.270" for claim in resolved.agreeing)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("screen", "structured", "same"),
    (
        ("0.00000001", "0.00000001000", True),
        ("0.00000001", "0.00000002", False),
        ("1000000000000.5", "1000000000000.50", True),
        ("1000000000000.5", "1000000000000.51", False),
    ),
)
def test_very_small_and_very_large_values_compare_exactly(
    screen: str, structured: str, same: bool
) -> None:
    resolved = against_structured(observed_price(screen), structured)
    assert resolved.has_conflict is (not same)


# ----------------------------------------------------------------------
# The exact path cannot be bypassed
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_bare_float_value_is_refused() -> None:
    """If a float exists the damage is already done; do not stringify it."""
    payload = json.loads(response("61.27"))  # parsed WITHOUT parse_float=Decimal
    assert isinstance(payload["values"][0]["value"], float)
    with pytest.raises(ValidationError, match="floating-point observation value is refused"):
        VisionExtractionSchema.model_validate(payload)


@pytest.mark.unit
def test_a_bare_float_confidence_is_refused() -> None:
    payload = json.loads(response('"61.27"'))
    assert isinstance(payload["values"][0]["confidence"], float)
    with pytest.raises(ValidationError, match="floating-point confidence is refused"):
        VisionExtractionSchema.model_validate(payload)


@pytest.mark.unit
def test_a_confidence_written_as_a_json_number_stays_exact() -> None:
    schema = parse("61.27")
    assert schema.values[0].confidence == Decimal("0.9")


@pytest.mark.unit
def test_a_boolean_is_not_an_observation() -> None:
    """`bool` is an `int` subclass and would otherwise stringify to 'True'."""
    with pytest.raises(ValidationError, match="must not be a boolean"):
        VisionExtractionSchema.model_validate(
            json.loads(response("true"), parse_float=Decimal, parse_int=Decimal)
        )


@pytest.mark.unit
@pytest.mark.parametrize("literal", ("NaN", "Infinity", "-Infinity"))
def test_a_non_finite_number_is_refused(literal: str) -> None:
    """Python's json accepts these extensions; a price cannot be one."""
    with pytest.raises(ValidationError):
        VisionExtractionSchema.model_validate(
            json.loads(response(literal), parse_float=Decimal, parse_int=Decimal)
        )


@pytest.mark.unit
def test_source_precedence_is_unchanged_by_any_of_this() -> None:
    """§4: the equality work must not touch the authority ordering."""
    resolved = against_structured(observed_price("99.99"), "61.27")
    assert resolved.source is DataSourcePriority.STRUCTURED_MARKET_DATA
    assert resolved.value == "61.27"

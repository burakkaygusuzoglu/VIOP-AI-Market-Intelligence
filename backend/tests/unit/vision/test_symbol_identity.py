"""Instrument identity is one rule across every phase.

The Phase 6 human review asked whether Vision had invented its own
symbol-equality policy. It had: `SymbolAgreement` case-folded, while Phase 3's
`require_matching_quote` compares exactly after stripping and says so in as
many words - no case folding, because that would assume an exchange convention
this project has not verified.

These tests pin the *shared* rule and prove the two layers cannot drift apart
again. Every symbol here is TEST_FIXTURE data.
"""

from __future__ import annotations

import pytest

from app.domain.common.identity import canonical_symbol, same_instrument
from app.domain.futures.contract import QuoteMismatchError, require_matching_quote
from app.domain.vision.slots import ScreenshotSlot
from tests.factories_futures import contract, quote

# (expected, detected, is the same instrument?)
IDENTITY_CASES = (
    ("FIXTURE_A", "FIXTURE_A", True),
    ("FIXTURE_A", "fixture_a", False),
    ("FIXTURE_A", "FIXTURE_a", False),
    ("  FIXTURE_A  ", "FIXTURE_A", True),
    ("FIXTURE_A\n", "\tFIXTURE_A", True),
    ("FIXTURE_A", "FIXTURE_B", False),
    ("FIXTURE_A", "FIXTURE_A2", False),
    ("FIXTURE A", "FIXTURE_A", False),
)


@pytest.mark.unit
@pytest.mark.parametrize(("first", "second", "expected"), IDENTITY_CASES)
def test_the_canonical_rule_strips_whitespace_and_nothing_else(
    first: str, second: str, expected: bool
) -> None:
    assert same_instrument(first, second) is expected


@pytest.mark.unit
@pytest.mark.parametrize(("first", "second", "expected"), IDENTITY_CASES)
def test_the_futures_engine_follows_the_canonical_rule(
    first: str, second: str, expected: bool
) -> None:
    """Phase 3's behaviour, unchanged - the source of the rule."""
    subject = contract(symbol=first)
    observation = quote(symbol=second)

    if expected:
        require_matching_quote(subject, observation, "identity fixture")
    else:
        with pytest.raises(QuoteMismatchError):
            require_matching_quote(subject, observation, "identity fixture")


@pytest.mark.unit
@pytest.mark.parametrize(("first", "second", "expected"), IDENTITY_CASES)
async def test_the_screenshot_check_follows_the_same_rule(
    first: str, second: str, expected: bool
) -> None:
    """Phase 6 must agree with Phase 3 on every case above."""
    from app.application.vision.analysis import review_screenshot  # noqa: PLC0415
    from tests.unit.vision.test_adapter_and_workflow import (  # noqa: PLC0415
        accepted,
        ok_analyzer,
    )

    engine, _ = ok_analyzer(symbol=second)
    review = await review_screenshot(engine, accepted(ScreenshotSlot.H1), expected_symbol=first)
    assert review.symbol.is_mismatch is (not expected)


@pytest.mark.unit
def test_the_canonical_form_normalises_nothing_but_whitespace() -> None:
    """Guards against a future 'helpful' upper-case or root extraction."""
    assert canonical_symbol("  fixture_a  ") == "fixture_a"
    assert canonical_symbol("FiXtUrE") == "FiXtUrE"
    assert canonical_symbol("FIXTURE_2512") == "FIXTURE_2512"


@pytest.mark.unit
def test_an_absent_expectation_is_not_a_mismatch() -> None:
    """Nothing to compare against is not a disagreement."""
    from app.application.vision.analysis import SymbolAgreement  # noqa: PLC0415

    assert not SymbolAgreement(expected="", detected="FIXTURE_A").is_mismatch
    assert not SymbolAgreement(expected="   ", detected="FIXTURE_A").is_mismatch
    assert not SymbolAgreement(expected="FIXTURE_A", detected=None).is_mismatch

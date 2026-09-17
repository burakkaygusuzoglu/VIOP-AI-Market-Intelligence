"""VİOP/futures behaviour is exactly what Phase 8 produced (§8, §21).

The expected values were recorded from the **unmodified Phase 8 code** before
any Phase 8.5 file existed - see ``golden_cases.py``. This test never writes
the baseline; it only compares against it.

Every case is its own parametrised test, so a regression names the case that
moved rather than reporting one opaque mismatch.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.unit.multi_asset import golden_cases

BASELINE: dict[str, Any] = json.loads(golden_cases.GOLDEN_PATH.read_text(encoding="utf-8"))
DOMAIN_CASES = dict(golden_cases.domain_cases())


def test_the_baseline_was_recorded_from_phase_8() -> None:
    assert "1c3d555" in BASELINE["captured_from"]
    assert "before any Phase 8.5 change" in BASELINE["captured_from"]


def test_no_case_was_added_or_dropped_silently() -> None:
    """A case removed from the module would otherwise just stop being checked."""
    assert set(DOMAIN_CASES) == set(BASELINE["domain"])
    assert {name for name, _, _ in golden_cases.api_cases()} == set(BASELINE["api"])


def test_the_baseline_exercises_every_outcome() -> None:
    """A parity test over only the happy path would prove little."""
    outcomes = {
        value["result"]["outcome"]
        for name, value in BASELINE["domain"].items()
        if name.startswith("size/") and "result" in value
    }
    refusals = {value["raises"] for value in BASELINE["domain"].values() if "raises" in value}

    assert outcomes == {"ALLOWED", "NOT_PERMITTED", "UNDETERMINED", "INVALID"}
    assert {
        "ContractValidationError",
        "UnsupportedValuationModelError",
        "UnverifiedFinancialFactError",
        "PnLInputError",
    } <= refusals


@pytest.mark.parametrize("name", sorted(BASELINE["domain"]))
def test_domain_result_is_unchanged(name: str) -> None:
    current = golden_cases.outcome(DOMAIN_CASES[name])

    assert current == BASELINE["domain"][name]


def test_api_responses_are_unchanged() -> None:
    """Phase 8 API output, digest-compared after removing only additive keys."""
    current = golden_cases.run_api_cases()

    for name, expected in BASELINE["api"].items():
        actual = current[name]
        assert actual["status"] == expected["status"], name
        assert actual["extract"] == expected["extract"], name
        assert actual["digest"] == expected["digest"], name

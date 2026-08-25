"""The correction workflow, end to end through the real application (§12, §6).

The Phase 6 human review asked whether CONFIRM / CORRECT / REJECT was actually
reachable by a consumer, or existed only as functions a test could call. It was
the latter: `record_correction` was invoked from nothing in `app/`. These tests
drive the workflow through the HTTP boundary that closed that gap.

Every value is TEST_FIXTURE data.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_clock
from app.application.ports.system import ClockPort
from app.application.vision.correction_workflow import (
    CorrectionRequest,
    ServerAnalysisContext,
    apply_correction,
)
from app.domain.common.enums import DataSourcePriority
from app.domain.vision.assets import ScreenshotId
from app.domain.vision.corrections import CorrectionType
from app.domain.vision.extraction import ObservedField
from app.domain.vision.precedence import ValueKind
from app.domain.vision.slots import ScreenshotSlot
from app.main import create_app

REPLAY_INSTANT = datetime(2021, 6, 4, 9, 30, tzinfo=UTC)


class FixedClock:
    """A `ClockPort` returning an instant a wall clock would never produce."""

    def now(self) -> datetime:
        return REPLAY_INSTANT


@pytest.fixture
def client() -> Iterator[TestClient]:
    application = create_app()
    application.dependency_overrides[get_clock] = FixedClock
    with TestClient(application) as test_client:
        yield test_client
    application.dependency_overrides.clear()


def correct(client: TestClient, **overrides: object):  # type: ignore[no-untyped-def]
    body: dict[str, object] = {
        "screenshot_id": "fixture-screenshot-id",
        "slot": "1H",
        "field": "INDICATOR_READING",
        "replayed_observation": "RSI 63.2",
        "action": "CONFIRMED",
    }
    body.update(overrides)
    return client.post("/api/screenshots/corrections", json=body)


# ----------------------------------------------------------------------
# Reachability: the three actions work over HTTP
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_confirming_is_reachable_and_keeps_the_value(client: TestClient) -> None:
    body = correct(client, action="CONFIRMED").json()
    assert body["action"] == "CONFIRMED"
    assert body["observed_screen_value"] == "RSI 63.2"
    assert body["user_value"] == "RSI 63.2"
    assert body["authoritative_source"] == "USER_CONFIRMED"


@pytest.mark.unit
def test_correcting_is_reachable_and_replaces_the_value(client: TestClient) -> None:
    body = correct(client, action="CORRECTED", corrected_value="RSI 61.3").json()
    assert body["user_value"] == "RSI 61.3"
    assert body["observed_screen_value"] == "RSI 63.2", "the model's reading was erased"
    assert body["authoritative_value"] == "RSI 61.3"


@pytest.mark.unit
def test_rejecting_is_reachable_and_leaves_the_field_unknown(client: TestClient) -> None:
    """A rejected reading is not a usable reading, and does not revert."""
    body = correct(client, action="REJECTED").json()
    assert body["user_value"] is None
    assert body["observed_screen_value"] == "RSI 63.2"
    assert body["authoritative_value"] is None, "a rejected value came back as authoritative"


@pytest.mark.unit
def test_the_original_is_preserved_by_every_action(client: TestClient) -> None:
    for action, value in (
        ("CONFIRMED", None),
        ("CORRECTED", "RSI 61.3"),
        ("REJECTED", None),
    ):
        body = correct(client, action=action, corrected_value=value).json()
        assert body["observed_screen_value"] == "RSI 63.2", f"{action} erased the original"


# ----------------------------------------------------------------------
# Authority: a correction changes rank, not arithmetic
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_structured_market_data_still_outranks_a_user_correction() -> None:
    """§13: the user confirms the screen, the engine keeps its own figure.

    Driven through the **application layer**, because the structured figure
    reaches the workflow only through `ServerAnalysisContext` - trusted
    server-side code. It is deliberately not reachable over HTTP; see
    `test_provenance_trust.py` for the adversarial half of that.
    """
    outcome = apply_correction(
        CorrectionRequest(
            screenshot_id=ScreenshotId("fixture-screenshot-id"),
            slot=ScreenshotSlot.H1,
            field=ObservedField.LAST_PRICE,
            replayed_observation="63.2",
            action=CorrectionType.CONFIRMED,
            value_kind=ValueKind.NUMERIC,
            server_context=ServerAnalysisContext(structured_value="61.27"),
        ),
        FixedClock(),
    )

    assert outcome.observed_screen_value == "63.2", "the screen value must stay answerable"
    assert outcome.user_value == "63.2"
    assert outcome.authoritative_value == "61.27"
    assert outcome.authoritative_source is DataSourcePriority.STRUCTURED_MARKET_DATA
    assert outcome.user_input_was_overridden
    assert outcome.authority is not None and outcome.authority.conflicts


@pytest.mark.unit
def test_a_user_correction_beats_the_raw_screenshot_reading(client: TestClient) -> None:
    body = correct(client, action="CORRECTED", corrected_value="RSI 61.3").json()
    assert body["authoritative_source"] == "USER_CONFIRMED"
    assert body["user_input_was_overridden"] is False


@pytest.mark.unit
def test_a_numerically_equal_correction_is_not_a_conflict() -> None:
    """61.270 confirmed against a structured 61.27 is agreement, not dispute."""
    outcome = apply_correction(
        CorrectionRequest(
            screenshot_id=ScreenshotId("fixture-screenshot-id"),
            slot=ScreenshotSlot.H1,
            field=ObservedField.LAST_PRICE,
            replayed_observation="61.270",
            action=CorrectionType.CONFIRMED,
            value_kind=ValueKind.NUMERIC,
            server_context=ServerAnalysisContext(structured_value="61.27"),
        ),
        FixedClock(),
    )
    assert outcome.authority is not None
    assert not outcome.authority.has_conflict
    assert outcome.authoritative_value == "61.27"
    assert any(claim.value == "61.270" for claim in outcome.authority.agreeing)


# ----------------------------------------------------------------------
# Time comes from the port
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_timestamp_comes_from_the_clock_port(client: TestClient) -> None:
    body = correct(client).json()
    assert body["corrected_at"] == REPLAY_INSTANT.isoformat(), "an ambient clock was used"


# ----------------------------------------------------------------------
# Incoherent requests are refused, typed, without internals
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_correction_without_a_replacement_value_is_refused(client: TestClient) -> None:
    response = correct(client, action="CORRECTED")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_CORRECTION"


@pytest.mark.unit
def test_a_confirmation_carrying_a_replacement_value_is_refused(client: TestClient) -> None:
    response = correct(client, action="CONFIRMED", corrected_value="RSI 61.3")
    assert response.status_code == 422


@pytest.mark.unit
@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("action", "MAYBE"),
        ("field", "NOT_A_FIELD"),
        ("slot", "4H"),
    ),
)
def test_an_unknown_enum_member_is_refused(client: TestClient, key: str, value: str) -> None:
    assert correct(client, **{key: value}).status_code == 422


@pytest.mark.unit
def test_an_unexpected_field_is_refused_not_ignored(client: TestClient) -> None:
    assert correct(client, recommendation="LONG").status_code == 422


@pytest.mark.unit
def test_prose_in_a_numeric_field_is_refused_with_no_stack_trace(client: TestClient) -> None:
    response = correct(
        client,
        field="LAST_PRICE",
        replayed_observation="roughly 61",
        action="CONFIRMED",
        numeric=True,
    )
    assert response.status_code == 422
    assert "Traceback" not in response.text


@pytest.mark.unit
def test_the_response_offers_no_trade_action(client: TestClient) -> None:
    body = correct(client).json()
    forbidden = {"action_signal", "decision", "recommendation", "direction", "verdict"}
    assert forbidden.isdisjoint(body)


# ----------------------------------------------------------------------
# The use case itself, without HTTP
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_use_case_is_usable_directly_and_takes_a_clock_port() -> None:
    clock = FixedClock()
    assert isinstance(clock, ClockPort)

    outcome = apply_correction(
        CorrectionRequest(
            screenshot_id=ScreenshotId("fixture-screenshot-id"),
            slot=ScreenshotSlot.H1,
            field=ObservedField.LAST_PRICE,
            replayed_observation="63.2",
            action=CorrectionType.CORRECTED,
            corrected_value="61.30",
            value_kind=ValueKind.NUMERIC,
            server_context=ServerAnalysisContext(structured_value="61.27"),
        ),
        clock,
    )

    assert outcome.observed_screen_value == "63.2"
    assert outcome.user_value == "61.30"
    assert outcome.authoritative_source is DataSourcePriority.STRUCTURED_MARKET_DATA
    assert outcome.user_input_was_overridden
    assert outcome.corrected_at == REPLAY_INSTANT
    assert len(outcome.log.corrections) == 1
    assert outcome.log.corrections[0].original_value == "63.2"

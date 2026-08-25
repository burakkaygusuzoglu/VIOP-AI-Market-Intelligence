"""A client may supply a value; it may never choose that value's trust rank.

The Phase 6 provenance audit found the corrections endpoint violating this. The
public schema had a `structured_value` field and the workflow stamped whatever
arrived in it as `STRUCTURED_MARKET_DATA`, so

    {"structured_value": "999"}

returned 999 as authoritative validated market data. `extra="forbid"` blocked
the *label* while the value that receives the label walked through - the same
escalation wearing a different hat.

These tests are adversarial: each one tries to obtain an authority the caller is
not entitled to, and asserts it cannot. Every value is TEST_FIXTURE data.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_clock
from app.api.schemas.screenshots import CorrectionRequestBody
from app.application.vision.correction_workflow import (
    CorrectionRequest,
    ObservationOrigin,
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
    def now(self) -> datetime:
        return REPLAY_INSTANT


@pytest.fixture
def client() -> Iterator[TestClient]:
    application = create_app()
    application.dependency_overrides[get_clock] = FixedClock
    with TestClient(application) as test_client:
        yield test_client
    application.dependency_overrides.clear()


def body(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "screenshot_id": "fixture-screenshot-id",
        "slot": "1H",
        "field": "LAST_PRICE",
        "replayed_observation": "63.2",
        "action": "CONFIRMED",
        "numeric": True,
    }
    payload.update(overrides)
    return payload


def post(client: TestClient, **overrides: object):  # type: ignore[no-untyped-def]
    return client.post("/api/screenshots/corrections", json=body(**overrides))


# ----------------------------------------------------------------------
# 1. The client cannot self-label a source
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "forged",
    (
        {"source": "STRUCTURED_MARKET_DATA"},
        {"source": "VALIDATED_CONTRACT_METADATA"},
        {"source_priority": 1},
        {"priority": 1},
        {"authority": "STRUCTURED_MARKET_DATA"},
        {"verification_status": "VERIFIED"},
        {"status": "VERIFIED"},
        {"verified": True},
        {"trusted": True},
        {"origin": "SERVER_VISION_RESULT"},
        {"observed_value_origin": "SERVER_VISION_RESULT"},
        {"server_context": {"structured_value": "999"}},
        {"structured_value": "999"},
        {"authoritative_value": "999"},
        {"authoritative_source": "STRUCTURED_MARKET_DATA"},
    ),
)
def test_no_request_may_name_a_source_or_a_trust_level(
    client: TestClient, forged: dict[str, object]
) -> None:
    """Every one of these is refused as an unexpected field, not ignored."""
    response = post(client, **forged)
    assert response.status_code == 422, f"{forged} was accepted"
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


@pytest.mark.unit
def test_the_public_schema_has_no_provenance_field_at_all() -> None:
    """Structural proof, independent of any single forged-field guess."""
    fields = set(CorrectionRequestBody.model_fields)
    forbidden = {
        "source",
        "source_priority",
        "priority",
        "authority",
        "verification_status",
        "verified",
        "trusted",
        "origin",
        "structured_value",
        "server_context",
        "original_kind",
    }
    assert fields.isdisjoint(forbidden), f"the schema exposes provenance: {fields & forbidden}"
    assert fields == {
        "screenshot_id",
        "slot",
        "field",
        "replayed_observation",
        "action",
        "corrected_value",
        "note",
        "numeric",
    }


@pytest.mark.unit
def test_a_server_context_cannot_be_built_from_the_request_schema() -> None:
    """The escalation has no representation, rather than being validated away."""
    assert "server_context" not in CorrectionRequestBody.model_fields
    request_annotations = {
        name: str(field.annotation) for name, field in CorrectionRequestBody.model_fields.items()
    }
    assert not any("ServerAnalysisContext" in text for text in request_annotations.values())


# ----------------------------------------------------------------------
# 2. The hostile request from the audit
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_client_cannot_obtain_structured_market_data_authority(client: TestClient) -> None:
    """The exact request that used to return 999 as validated market data."""
    response = post(client, replayed_observation="63.2", action="CONFIRMED")
    assert response.status_code == 200

    payload = response.json()
    assert payload["authoritative_source"] == "USER_CONFIRMED"
    assert payload["authoritative_source"] != "STRUCTURED_MARKET_DATA"
    assert payload["authoritative_value"] == "63.2"


@pytest.mark.unit
def test_no_response_to_any_client_request_claims_structured_authority(
    client: TestClient,
) -> None:
    """Sweep every action: none of them can reach the top two ranks."""
    for action, corrected in (("CONFIRMED", None), ("CORRECTED", "999"), ("REJECTED", None)):
        payload = post(client, action=action, corrected_value=corrected).json()
        assert payload["authoritative_source"] in {"USER_CONFIRMED", None}, (
            f"{action} reached {payload['authoritative_source']}"
        )


@pytest.mark.unit
def test_a_corrected_value_is_user_confirmed_not_market_data(client: TestClient) -> None:
    """A number the user typed is a user claim, however plausible it looks."""
    payload = post(client, action="CORRECTED", corrected_value="61.27").json()
    assert payload["user_value"] == "61.27"
    assert payload["authoritative_value"] == "61.27"
    assert payload["authoritative_source"] == "USER_CONFIRMED"


@pytest.mark.unit
def test_the_whole_response_never_names_a_forbidden_source(client: TestClient) -> None:
    rendered = json.dumps(post(client).json())
    assert "STRUCTURED_MARKET_DATA" not in rendered
    assert "VALIDATED_CONTRACT_METADATA" not in rendered


# ----------------------------------------------------------------------
# 3. Client-replayed context is described honestly
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_replayed_observation_is_reported_as_unverified(client: TestClient) -> None:
    """§2: do not describe client-replayed data as server-verified."""
    payload = post(client).json()
    assert payload["observed_screen_value"] == "63.2"
    assert payload["observed_value_origin"] == "CLIENT_REPLAYED_UNVERIFIED"


@pytest.mark.unit
def test_a_client_cannot_claim_its_replay_came_from_a_server_vision_result(
    client: TestClient,
) -> None:
    assert post(client, origin="SERVER_VISION_RESULT").status_code == 422
    assert post(client).json()["observed_value_origin"] == "CLIENT_REPLAYED_UNVERIFIED"


@pytest.mark.unit
def test_the_replayed_observation_enters_at_the_weakest_rank() -> None:
    """A caller cannot promote its replay by calling it directly visible.

    `DIRECTLY_VISIBLE` maps to SCREENSHOT_EXTRACTED and `VISUALLY_INFERRED` to
    AI_VISUAL_INFERENCE - one rank apart. Letting the request pick would be
    letting it choose its own precedence, one notch at a time.
    """
    outcome = apply_correction(
        CorrectionRequest(
            screenshot_id=ScreenshotId("fixture-screenshot-id"),
            slot=ScreenshotSlot.H1,
            field=ObservedField.LAST_PRICE,
            replayed_observation="63.2",
            action=CorrectionType.REJECTED,
        ),
        FixedClock(),
    )
    original = outcome.log.corrections[0].original
    assert original.source_priority is DataSourcePriority.AI_VISUAL_INFERENCE


# ----------------------------------------------------------------------
# 4. The trusted server path still works, and still outranks the user
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_server_supplied_structured_value_still_beats_a_user_confirmation() -> None:
    """§3: 61.27 wins - but only entering through the trusted path."""
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

    assert outcome.authoritative_value == "61.27"
    assert outcome.authoritative_source is DataSourcePriority.STRUCTURED_MARKET_DATA
    assert outcome.user_input_was_overridden
    assert outcome.observed_screen_value == "63.2", "the screen value must stay answerable"
    assert outcome.authority is not None and outcome.authority.conflicts


@pytest.mark.unit
def test_a_user_confirmation_beats_the_raw_replayed_observation() -> None:
    """§4: USER_CONFIRMED outranks screenshot and AI inference."""
    outcome = apply_correction(
        CorrectionRequest(
            screenshot_id=ScreenshotId("fixture-screenshot-id"),
            slot=ScreenshotSlot.H1,
            field=ObservedField.LAST_PRICE,
            replayed_observation="63.2",
            action=CorrectionType.CORRECTED,
            corrected_value="61.30",
            value_kind=ValueKind.NUMERIC,
        ),
        FixedClock(),
    )
    assert outcome.authoritative_source is DataSourcePriority.USER_CONFIRMED
    assert outcome.authoritative_value == "61.30"


@pytest.mark.unit
def test_a_user_correction_never_becomes_structured_market_data() -> None:
    """§4: it may outrank AI, it may not become validated market data."""
    for value_kind in (ValueKind.CATEGORICAL, ValueKind.NUMERIC):
        outcome = apply_correction(
            CorrectionRequest(
                screenshot_id=ScreenshotId("fixture-screenshot-id"),
                slot=ScreenshotSlot.H1,
                field=ObservedField.LAST_PRICE,
                replayed_observation="63.2",
                action=CorrectionType.CORRECTED,
                corrected_value="61.30",
                value_kind=value_kind,
            ),
            FixedClock(),
        )
        assert outcome.authoritative_source is not DataSourcePriority.STRUCTURED_MARKET_DATA
        assert outcome.authoritative_source is not DataSourcePriority.VALIDATED_CONTRACT_METADATA


@pytest.mark.unit
def test_the_server_context_defaults_to_offering_nothing() -> None:
    """Absent context must not be read as an empty structured claim."""
    assert ServerAnalysisContext().structured_value is None
    outcome = apply_correction(
        CorrectionRequest(
            screenshot_id=ScreenshotId("fixture-screenshot-id"),
            slot=ScreenshotSlot.H1,
            field=ObservedField.LAST_PRICE,
            replayed_observation="63.2",
            action=CorrectionType.CONFIRMED,
            server_context=ServerAnalysisContext(),
        ),
        FixedClock(),
    )
    assert outcome.authoritative_source is DataSourcePriority.USER_CONFIRMED


@pytest.mark.unit
def test_a_server_loaded_observation_can_be_marked_as_such() -> None:
    """The honest half of the distinction: server-side callers may say so."""
    outcome = apply_correction(
        CorrectionRequest(
            screenshot_id=ScreenshotId("fixture-screenshot-id"),
            slot=ScreenshotSlot.H1,
            field=ObservedField.LAST_PRICE,
            replayed_observation="63.2",
            action=CorrectionType.CONFIRMED,
            origin=ObservationOrigin.SERVER_VISION_RESULT,
        ),
        FixedClock(),
    )
    assert outcome.observed_value_origin is ObservationOrigin.SERVER_VISION_RESULT


# ----------------------------------------------------------------------
# 5. Audit relationship, statelessness, Phase 7 boundary
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_original_survives_every_action_and_keeps_its_timestamp(
    client: TestClient,
) -> None:
    for action, corrected in (("CONFIRMED", None), ("CORRECTED", "61.30"), ("REJECTED", None)):
        payload = post(client, action=action, corrected_value=corrected).json()
        assert payload["observed_screen_value"] == "63.2"
        assert payload["corrected_at"] == REPLAY_INSTANT.isoformat()


@pytest.mark.unit
def test_the_endpoint_remains_stateless(client: TestClient) -> None:
    """Two identical requests produce identical results; nothing accumulates."""
    first = post(client).json()
    second = post(client).json()
    assert first == second

    other = post(client, replayed_observation="99.9").json()
    assert other["observed_screen_value"] == "99.9", "a previous request leaked into this one"
    assert post(client).json() == first


@pytest.mark.unit
def test_no_phase_seven_synthesis_appears_in_the_response(client: TestClient) -> None:
    payload = post(client).json()
    assert {"decision", "recommendation", "direction", "signal", "verdict"}.isdisjoint(payload)
    rendered = json.dumps(payload)
    for banned in ("LONG", "SHORT", "NO TRADE", "position_size", "stop_loss", "take_profit"):
        assert banned not in rendered

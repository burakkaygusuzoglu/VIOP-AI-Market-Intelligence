"""Where Shadow Mode exists at all, and who may drive it (Phase 14 Part 2B).

Shadow orchestration is unauthenticated, holds tasks and writes a journal, so
it must be exactly as closed as the Phase 13 live workspace it watches: composed
only on the explicit opt-in in development or test, never in production, never
because a variable is missing, and behind the same Host and Origin checks.

Settings come from the environment through ``monkeypatch.setenv`` (CLAUDE.md,
trap 2), with the helpers the Phase 13 deployment tests use.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.unit.live.test_live_deployment import client_for, settings_from_env

pytestmark = pytest.mark.unit

__all__ = ["client_for"]  # the fixture, re-exported for pytest

SHADOW = "/api/shadow"
RUN = f"{SHADOW}/runs/SR-{'0' * 24}"
BODY = {
    "session_id": "LS-" + "0" * 24,
    "strategy_id": "ema-crossover-atr",
    "strategy_version": "1.0.0",
    "driver": "5M",
    "timeframes": ["5M"],
}

MATRIX = [
    (None, None, False),
    (None, "true", True),  # APP_ENV defaults to development; explicitly opted in
    ("development", None, False),
    ("development", "false", False),
    ("development", "true", True),
    ("test", None, False),
    ("test", "true", True),
    ("production", None, False),
    ("production", "false", False),
    ("production", "true", False),  # production ignores the opt-in
]


class TestTheConfigurationMatrix:
    @pytest.mark.parametrize(("app_env", "enabled", "composed"), MATRIX)
    def test_shadow_exists_exactly_where_the_live_workspace_does(
        self,
        client_for: object,
        app_env: str | None,
        enabled: str | None,
        composed: bool,
    ) -> None:
        client: TestClient = client_for(app_env, enabled)  # type: ignore[operator]
        state = client.app.state  # type: ignore[attr-defined]

        assert (state.shadow_workspace is not None) is composed
        assert (state.live_workspace is not None) is composed
        assert client.get(f"{SHADOW}/capability").json()["available"] is composed

    @pytest.mark.parametrize("app_env", ["", "prod", "PRODUCTION", " production", "staging"])
    def test_an_ambiguous_app_env_refuses_to_start(
        self, monkeypatch: pytest.MonkeyPatch, app_env: str
    ) -> None:
        with pytest.raises(ValidationError):
            settings_from_env(monkeypatch, app_env, "true")

    @pytest.mark.parametrize("enabled", ["", "maybe", "2", "yes please"])
    def test_an_ambiguous_opt_in_refuses_to_start(
        self, monkeypatch: pytest.MonkeyPatch, enabled: str
    ) -> None:
        with pytest.raises(ValidationError):
            settings_from_env(monkeypatch, "development", enabled)


class TestADisabledShadowRefusesEverything:
    @pytest.mark.parametrize(
        ("app_env", "enabled"),
        [(None, None), ("development", "false"), ("test", None), ("production", "true")],
    )
    def test_every_route_answers_disabled(
        self, client_for: object, app_env: str | None, enabled: str | None
    ) -> None:
        client: TestClient = client_for(app_env, enabled)  # type: ignore[operator]

        for method, path, body in (
            ("GET", f"{SHADOW}/runs", None),
            ("POST", f"{SHADOW}/runs", BODY),
            ("GET", RUN, None),
            ("POST", f"{RUN}/cancel", None),
            ("GET", f"{RUN}/journal", None),
            ("GET", f"{RUN}/outcomes", None),
        ):
            response = client.request(method, path, json=body)
            assert response.status_code == 503, (method, path)
            assert response.json()["detail"]["code"] == "SHADOW_DISABLED"

    def test_production_says_why_in_the_capability(self, client_for: object) -> None:
        client: TestClient = client_for("production", "true")  # type: ignore[operator]

        body = client.get(f"{SHADOW}/capability").json()

        assert body["available"] is False
        assert body["execution_enabled"] is False
        assert body["financial_metadata_available"] is False


class TestTheBrowserBoundary:
    @pytest.mark.parametrize("host", ["attacker.example", "192.168.1.20", "localhost.evil"])
    def test_a_foreign_host_is_refused_even_for_reads(self, client_for: object, host: str) -> None:
        client: TestClient = client_for("development", "true")  # type: ignore[operator]

        for path in (f"{SHADOW}/runs", RUN, f"{RUN}/journal"):
            response = client.get(path, headers={"host": host})
            assert response.status_code == 403, (host, path)
            assert response.json()["detail"]["code"] == "LIVE_HOST_REFUSED"

    @pytest.mark.parametrize(
        "origin", ["https://attacker.example", "null", "http://localhost:9999"]
    )
    def test_a_foreign_origin_cannot_create_or_cancel(
        self, client_for: object, origin: str
    ) -> None:
        client: TestClient = client_for("development", "true")  # type: ignore[operator]

        for path, body in ((f"{SHADOW}/runs", BODY), (f"{RUN}/cancel", None)):
            response = client.post(path, json=body, headers={"origin": origin})
            assert response.status_code == 403, (origin, path)
            assert response.json()["detail"]["code"] == "LIVE_ORIGIN_REFUSED"

    def test_a_request_without_origin_is_a_script_and_is_allowed_through(
        self, client_for: object
    ) -> None:
        client: TestClient = client_for("development", "true")  # type: ignore[operator]

        # Not a browser forgery: it reaches validation and is refused there.
        response = client.post(f"{SHADOW}/runs", json=BODY)

        assert response.status_code == 404  # no such live session

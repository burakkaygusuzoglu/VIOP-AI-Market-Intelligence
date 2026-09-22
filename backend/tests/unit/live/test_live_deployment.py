"""Where the live workspace exists at all, and who may drive it (Phase 13 Part 2B).

The live API is unauthenticated and holds a task and memory per session, so
the deployment boundary is part of its correctness:

* it is composed only on an explicit opt-in *and* in development or test;
* a missing, empty or unexpected ``APP_ENV`` never opens it;
* where it is composed, a foreign ``Host`` (DNS rebinding) and a foreign
  ``Origin`` on a state-changing request (cross-site forgery) are refused.

Settings are built from the **environment**, through ``monkeypatch.setenv``,
because constructing ``Settings(...)`` directly bypasses the parsing path a
real deployment takes (CLAUDE.md, trap 2).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app

pytestmark = pytest.mark.unit

LIVE = "/api/live"
BODY = {"source_id": "RD-" + "a" * 32, "timeframes": ["5M"]}


def settings_from_env(
    monkeypatch: pytest.MonkeyPatch, app_env: str | None, enabled: str | None
) -> Settings:
    for name in ("APP_ENV", "LIVE_SIMULATION_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    if app_env is not None:
        monkeypatch.setenv("APP_ENV", app_env)
    if enabled is not None:
        monkeypatch.setenv("LIVE_SIMULATION_ENABLED", enabled)
    monkeypatch.setenv("POSTGRES_PASSWORD", "fixture-password")  # TEST_FIXTURE value
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def client_for(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    opened: list[TestClient] = []

    def build(app_env: str | None, enabled: str | None) -> TestClient:
        client = TestClient(
            create_app(settings_from_env(monkeypatch, app_env, enabled)),
            base_url="http://localhost",
        )
        client.__enter__()
        opened.append(client)
        return client

    yield build
    for client in opened:
        client.__exit__(None, None, None)


class TestTheConfigurationMatrix:
    @pytest.mark.parametrize(
        ("app_env", "enabled", "composed"),
        [
            (None, None, False),  # nothing set at all
            (None, "true", True),  # APP_ENV defaults to development; opted in
            ("development", None, False),  # development alone is not an opt-in
            ("development", "false", False),
            ("development", "true", True),  # the documented local configuration
            ("test", "true", True),
            ("production", None, False),
            ("production", "true", False),  # production ignores the opt-in
        ],
    )
    def test_only_an_explicit_opt_in_outside_production_composes_it(
        self,
        monkeypatch: pytest.MonkeyPatch,
        app_env: str | None,
        enabled: str | None,
        composed: bool,
    ) -> None:
        settings = settings_from_env(monkeypatch, app_env, enabled)
        assert settings.live_simulation_composed is composed

    @pytest.mark.parametrize("app_env", ["", "prod", "PRODUCTION", " production", "staging"])
    def test_an_ambiguous_app_env_refuses_to_start(
        self, monkeypatch: pytest.MonkeyPatch, app_env: str
    ) -> None:
        with pytest.raises(ValidationError):
            settings_from_env(monkeypatch, app_env, "true")

    @pytest.mark.parametrize("enabled", ["", "maybe", "2"])
    def test_an_ambiguous_opt_in_refuses_to_start(
        self, monkeypatch: pytest.MonkeyPatch, enabled: str
    ) -> None:
        with pytest.raises(ValidationError):
            settings_from_env(monkeypatch, "development", enabled)


class TestTheComposedApplication:
    @pytest.mark.parametrize(
        ("app_env", "enabled"),
        [(None, None), ("development", None), ("production", "true")],
    )
    def test_a_disabled_workspace_refuses_every_session_route(
        self, client_for: object, app_env: str | None, enabled: str | None
    ) -> None:
        client = client_for(app_env, enabled)  # type: ignore[operator]

        assert client.get(f"{LIVE}/capability").json()["state"] == "DISABLED"
        for method, path, body in (
            ("GET", f"{LIVE}/sources", None),
            ("GET", f"{LIVE}/sessions", None),
            ("POST", f"{LIVE}/sessions", BODY),
            ("POST", f"{LIVE}/sessions/LS-{'0' * 24}/cancel", None),
            ("DELETE", f"{LIVE}/sessions/LS-{'0' * 24}", None),
            ("GET", f"{LIVE}/sessions/LS-{'0' * 24}/events", None),
        ):
            response = client.request(method, path, json=body)
            assert response.status_code == 503, (method, path)
            assert response.json()["detail"]["code"] == "LIVE_DISABLED"
        assert client.app.state.live_workspace is None

    def test_the_explicit_local_configuration_composes_it(self, client_for: object) -> None:
        client = client_for("development", "true")  # type: ignore[operator]

        assert client.get(f"{LIVE}/capability").json()["state"] == "AVAILABLE"


class TestTheBrowserBoundary:
    @pytest.fixture
    def local(self, client_for: object) -> TestClient:
        client: TestClient = client_for("development", "true")  # type: ignore[operator]
        return client

    @pytest.mark.parametrize(
        "host",
        ["evil.example", "attacker.test:5173", "192.168.1.20:8000", "localhost.evil.example"],
    )
    def test_a_foreign_host_is_refused_even_for_reads(self, local: TestClient, host: str) -> None:
        """DNS rebinding: a hostile name pointing at 127.0.0.1."""
        response = local.get(f"{LIVE}/capability", headers={"Host": host})

        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "LIVE_HOST_REFUSED"

    @pytest.mark.parametrize(
        "host", ["localhost", "localhost:5173", "127.0.0.1:8000", "[::1]:8000"]
    )
    def test_loopback_hosts_are_served(self, local: TestClient, host: str) -> None:
        assert local.get(f"{LIVE}/capability", headers={"Host": host}).status_code == 200

    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("POST", f"{LIVE}/sessions", BODY),
            ("POST", f"{LIVE}/sessions/LS-{'0' * 24}/cancel", None),
            ("POST", f"{LIVE}/sessions/LS-{'0' * 24}/analysis", {}),
            ("DELETE", f"{LIVE}/sessions/LS-{'0' * 24}", None),
        ],
    )
    @pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://localhost:6666"])
    def test_a_foreign_origin_cannot_change_anything(
        self,
        local: TestClient,
        method: str,
        path: str,
        body: dict[str, object] | None,
        origin: str,
    ) -> None:
        response = local.request(method, path, json=body, headers={"Origin": origin})

        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "LIVE_ORIGIN_REFUSED"

    def test_a_cross_site_simple_post_cannot_cancel(self, local: TestClient) -> None:
        """A form or a text/plain fetch needs no preflight; the Origin check
        is what stops it, not CORS."""
        response = local.post(
            f"{LIVE}/sessions/LS-{'0' * 24}/cancel",
            content=b"x",
            headers={"Origin": "https://evil.example", "Content-Type": "text/plain"},
        )
        assert response.status_code == 403

    def test_the_configured_origin_and_scripts_are_allowed(self, local: TestClient) -> None:
        allowed = local.post(
            f"{LIVE}/sessions/LS-{'0' * 24}/cancel", headers={"Origin": "http://localhost:5173"}
        )
        scripted = local.post(f"{LIVE}/sessions/LS-{'0' * 24}/cancel")

        assert allowed.status_code == scripted.status_code == 404  # reached the workspace

    def test_the_cors_policy_is_not_widened(self, local: TestClient) -> None:
        preflight = local.options(
            f"{LIVE}/sessions",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert preflight.headers.get("access-control-allow-origin") != "https://evil.example"
        assert preflight.headers.get("access-control-allow-origin") != "*"

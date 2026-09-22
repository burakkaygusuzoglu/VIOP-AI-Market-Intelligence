"""What Phase 13 Part 1 must not contain, checked rather than claimed.

Absence tests ban the *shape of a capability* - a class, an import, a
constructor call - never the prose that forbids it. Several modules mention
exchanges and brokers precisely in order to say they are not used.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.live.events import RawCandleEvent, StreamProvenance

pytestmark = pytest.mark.unit

APP = Path(__file__).resolve().parents[3] / "app"
LIVE = sorted(
    [
        *(APP / "domain" / "live").rglob("*.py"),
        *(APP / "application" / "live").rglob("*.py"),
        *(APP / "adapters" / "live").rglob("*.py"),
    ]
)


def text_of(paths: list[Path]) -> list[tuple[str, str]]:
    return [(p.relative_to(APP).as_posix(), p.read_text(encoding="utf-8")) for p in paths]


def test_the_live_modules_exist_and_are_being_checked() -> None:
    assert len(LIVE) >= 10


class TestNoFalseLiveData:
    def test_the_only_provenance_is_the_simulated_one(self) -> None:
        assert [member.value for member in StreamProvenance] == ["SIMULATED_HISTORICAL_STREAM"]

    @pytest.mark.parametrize(
        "claim", ["EXCHANGE_VERIFIED", "LIVE_EXCHANGE_FEED", "BROKER_VERIFIED", "REAL_TIME_QUOTE"]
    )
    def test_no_module_can_mint_an_exchange_claim(self, claim: str) -> None:
        offenders = [name for name, body in text_of(LIVE) if f'"{claim}"' in body]
        assert offenders == []

    def test_a_raw_event_has_no_provenance_field_to_forge(self) -> None:
        assert "provenance" not in RawCandleEvent.__dataclass_fields__


class TestNoExecutionAndNoSecondEngine:
    @pytest.mark.parametrize(
        "capability",
        [
            "def place_order",
            "def submit_order",
            "import midas",
            "BrokerPort",
            "OrderPort",
            "open_position(",
            "PaperTradingService",
            "BacktestRunner",
            "ReplayService",
            "shadow_mode",
        ],
    )
    def test_no_live_module_can_trade(self, capability: str) -> None:
        offenders = [name for name, body in text_of(LIVE) if capability in body]
        assert offenders == []

    @pytest.mark.parametrize(
        "engine",
        [
            "def ema(",
            "def rsi(",
            "def atr(",
            "def adx(",
            "def macd(",
            "compute_technicals(",
            "analyse_structure(",
            "class LiveRSI",
            "class LiveEMA",
        ],
    )
    def test_no_live_module_computes_an_indicator(self, engine: str) -> None:
        offenders = [name for name, body in text_of(LIVE) if engine in body]
        assert offenders == []

    def test_the_session_reaches_analysis_only_through_run_analysis(self) -> None:
        body = (APP / "application" / "live" / "session.py").read_text(encoding="utf-8")

        assert "run_analysis(" in body
        for module in ("app.application.synthesis", "app.application.vision", "anthropic"):
            assert module not in body, f"session.py reaches {module}"


class TestNoRealProviderAndNoProductionWiring:
    @pytest.mark.parametrize(
        "network", ["websockets.connect", "httpx", "aiohttp", "requests.get", "socket("]
    )
    def test_no_live_adapter_opens_a_network_connection(self, network: str) -> None:
        offenders = [name for name, body in text_of(LIVE) if network in body]
        assert offenders == []

    def test_the_composition_root_wires_only_the_stored_dataset_playback(self) -> None:
        """Part 2A composes the workspace - with the simulated dataset
        playback and nothing else. No scripted mock, no other provider."""
        main = (APP / "main.py").read_text(encoding="utf-8")

        assert "ReplayDatasetCatalog" in main
        assert "MockStreamProvider" not in main
        assert "MockPushProvider" not in main
        assert "settings.live_simulation_composed" in main  # explicit opt-in only

    def test_the_live_api_can_stream_but_cannot_trade(self) -> None:
        """Part 2A adds the live routes (Part 1 asserted their absence). What
        they must still never contain is a way to trade or a second socket."""
        route = (APP / "api" / "routes" / "live.py").read_text(encoding="utf-8")

        for banned in (
            "websocket",
            "WebSocket",
            "/positions",
            "/orders",
            "paper",
            "backtest",
            "synthesise(",
            "get_synthesizer",
        ):
            assert banned not in route, f"routes/live.py contains {banned}"

    def test_no_fixture_metadata_reaches_the_live_code(self) -> None:
        for name, body in text_of(LIVE):
            for banned in ("paper_contract(", "ManualContractMetadataProvider", "TEST_FIXTURE"):
                assert banned not in body, f"{name} contains {banned}"


class TestNoPersistence:
    def test_live_state_is_not_stored(self) -> None:
        """Ephemeral by design. Nothing in the live code touches a database."""
        for name, body in text_of(LIVE):
            for banned in ("sqlalchemy", "Database(", "session.commit", "alembic"):
                assert banned not in body, f"{name} contains {banned}"

    def test_no_live_migration_exists(self) -> None:
        versions = APP.parent / "alembic" / "versions"
        assert not any("live" in path.name for path in versions.glob("*.py"))

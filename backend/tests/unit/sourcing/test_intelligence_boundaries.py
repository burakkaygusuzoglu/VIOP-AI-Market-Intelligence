"""Failure, trading and order boundaries of Part 2A (tests Q, T, U).

The import contracts themselves run in ``tests/unit/test_architecture.py``;
these tests pin that the contracts exist with the modules they must cover, so
deleting a line from ``pyproject.toml`` fails here too.
"""

from __future__ import annotations

import logging
import tomllib
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.application.sourcing.intelligence import (
    BreadthReader,
    OpenInterestReader,
    RecordedSessionCalendar,
)
from app.domain.sourcing.breadth import BreadthStatus, ConstituentMove, Universe
from app.domain.sourcing.calendar import CalendarDay, SessionStatus
from app.domain.sourcing.limits import ProviderLimits
from app.domain.sourcing.open_interest import (
    OpenInterestObservation,
    OpenInterestScope,
    OpenInterestStatus,
)
from tests.unit.sourcing.support import NOW, reference

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[3]
SOURCING = [
    *(BACKEND / "app" / "domain" / "sourcing").glob("*.py"),
    *(BACKEND / "app" / "application" / "sourcing").glob("*.py"),
    BACKEND / "app" / "application" / "ports" / "intelligence.py",
]
SECRET = "sk-intel-do-not-leak"
FAILURE = f"https://vendor.example/v1?api_key={SECRET}&account=ACC-0001"


class Broken:
    async def observations(
        self, instrument: str, scope: OpenInterestScope, start: datetime, end: datetime
    ) -> Sequence[OpenInterestObservation]:
        raise ConnectionError(FAILURE)

    async def universe(self, universe_id: str) -> Universe | None:
        raise ConnectionError(FAILURE)

    async def moves(self, universe_id: str, observed_at: datetime) -> Sequence[ConstituentMove]:
        raise ConnectionError(FAILURE)

    async def days(self, venue: str, start: date, end: date) -> Sequence[CalendarDay]:
        raise ConnectionError(FAILURE)


class TestQAProviderFailureLeaksNoSecret:
    async def test_every_reader_answers_unavailable_and_logs_the_type_only(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)
        limits = ProviderLimits()

        oi = await OpenInterestReader(Broken(), limits=limits, max_age=timedelta(days=1)).at(
            "TEST_FIXTURE_FUT",
            OpenInterestScope.CONTRACT,
            decision_time=NOW,
            lookback=timedelta(days=1),
        )
        breadth = await BreadthReader(Broken(), limits=limits, min_coverage=Decimal(1)).at(
            "TEST_FIXTURE_INDEX", observed_at=NOW, decision_time=NOW
        )
        session = await RecordedSessionCalendar(
            Broken(),
            venue="TEST_FIXTURE_VENUE",
            categories={"TEST_FIXTURE_FUT": reference("TEST_FIXTURE_CATEGORY")},
            limits=limits,
        ).session_at("TEST_FIXTURE_FUT", NOW)

        assert oi.status is OpenInterestStatus.UNAVAILABLE
        assert breadth.status is BreadthStatus.UNAVAILABLE
        assert session.status is SessionStatus.UNAVAILABLE
        for text in (caplog.text, oi.reason, breadth.reason, session.reason):
            assert SECRET not in text
            assert "vendor.example" not in text
            assert "ACC-0001" not in text
        assert all(record.exc_info is None for record in caplog.records)
        assert {getattr(r, "error_type", None) for r in caplog.records} == {"ConnectionError"}


def contracts() -> dict[str, dict[str, list[str]]]:
    data = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    return {c["name"]: c for c in data["tool"]["importlinter"]["contracts"]}


class TestTNoAutomaticPaperPosition:
    def test_intelligence_cannot_reach_paper_risk_or_strategy(self) -> None:
        contract = contracts()["External intelligence reaches no trading, risk or strategy code"]

        assert {"app.domain.sourcing", "app.application.sourcing"} <= set(
            contract["source_modules"]
        )
        assert {
            "app.domain.paper",
            "app.application.paper",
            "app.domain.risk",
            "app.application.strategy",
        } <= set(contract["forbidden_modules"])

    def test_no_decision_path_reads_intelligence(self) -> None:
        contract = contracts()["No decision path reads external intelligence yet"]

        assert {
            "app.application.paper",
            "app.application.strategy",
            "app.application.shadow",
            "app.application.backtest",
            "app.domain.risk",
        } <= set(contract["source_modules"])
        assert {
            "app.domain.sourcing.news",
            "app.domain.sourcing.open_interest",
            "app.domain.sourcing.breadth",
            "app.domain.sourcing.calendar",
            "app.domain.sourcing.facts",
        } <= set(contract["forbidden_modules"])

    def test_nothing_in_sourcing_names_a_position_or_an_approval(self) -> None:
        for path in SOURCING:
            code = [
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.lstrip().startswith(("from ", "import "))
            ]
            assert not any("paper" in line or "risk" in line for line in code), path.name


class TestUNoBrokerOrder:
    @pytest.mark.parametrize("path", SOURCING, ids=lambda p: p.name)
    def test_no_sourcing_module_speaks_a_network_protocol_or_places_an_order(
        self, path: Path
    ) -> None:
        text = path.read_text(encoding="utf-8")
        imports = [
            line for line in text.splitlines() if line.lstrip().startswith(("from ", "import "))
        ]
        for forbidden in ("httpx", "websockets", "requests", "aiohttp", "socket", "urllib"):
            assert not any(forbidden in line for line in imports), (path.name, forbidden)
        for verb in ("place_order", "submit_order", "send_order", "OrderRequest"):
            assert verb not in text, (path.name, verb)

"""Replay may drive an engine; it may not become one (Phase 11).

The import contracts stop replay from *importing* a calculation engine. They do
not stop someone writing a small one inside replay - four lines that look like
a convenience and are in fact a second P&L. These scans do, by reading the
replay packages and refusing the shapes such a thing takes.

The same technique caught a Phase 9 defect that clock injection could not: a
rule about behaviour is only as good as the seam it is observed through, and a
package that reads the process clock directly has no seam at all.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[3]
PACKAGES = (
    BACKEND / "app" / "domain" / "replay",
    BACKEND / "app" / "application" / "replay",
)
API_MODULES = (
    BACKEND / "app" / "api" / "routes" / "replay.py",
    BACKEND / "app" / "api" / "schemas" / "replay.py",
    BACKEND / "app" / "api" / "schemas" / "replay_projection.py",
)


def sources() -> list[Path]:
    found = [path for package in PACKAGES for path in package.rglob("*.py")]
    assert found, "the replay packages are missing"
    return found


def code_of(path: Path) -> str:
    """The module's source with docstrings removed.

    Every rule below is about what the code does. A module that *explains* why
    it does not compute a fill would otherwise fail the rule it documents.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            node.value.value = ""
    return ast.unparse(tree)


@pytest.mark.unit
class TestReplayComputesNoMoney:
    @pytest.mark.parametrize(
        "banned",
        [
            "def pnl",
            "def _pnl",
            "def realized",
            "def unrealized",
            "def gross",
            "def fill_price",
            "def _fill",
            "def apply_fill",
            "def win_rate",
            "def profit_factor",
            "def expectancy",
            "def drawdown",
            "def margin",
            "def point_value",
            "def multiplier",
            "def position_size",
        ],
    )
    def test_no_replay_owned_financial_routine(self, banned: str) -> None:
        for path in sources():
            assert banned not in code_of(path), f"{path.name} defines {banned}"

    @pytest.mark.parametrize(
        "banned",
        ["def rsi", "def ema", "def sma", "def atr", "def macd", "def adx", "def bollinger"],
    )
    def test_no_replay_owned_indicator(self, banned: str) -> None:
        for path in sources():
            assert banned not in code_of(path), f"{path.name} defines {banned}"

    def test_no_arithmetic_on_a_price_field(self) -> None:
        """A replay may read a candle; it may not do sums with one.

        Multiplying a close by a quantity is the first line of a second P&L,
        and it is the line that never looks like one in review.
        """
        for path in sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.BinOp):
                    continue
                if not isinstance(node.op, ast.Mult | ast.Sub | ast.Add | ast.Div):
                    continue
                names = {child.attr for child in ast.walk(node) if isinstance(child, ast.Attribute)}
                money = names & {"open", "high", "low", "close", "quantity", "point_value"}
                assert not money, f"{path.name}:{node.lineno} does arithmetic on {money}"


@pytest.mark.unit
class TestReplayReadsNoProcessClock:
    """Market time comes from the cursor, never from the machine.

    A replay that read the wall clock would be right in every test that froze
    time and wrong the moment a person left the tab open overnight.
    """

    @pytest.mark.parametrize(
        "banned",
        [
            "datetime.now(",
            "datetime.utcnow(",
            "datetime.today(",
            "time.time(",
            "date.today(",
            "monotonic(",
        ],
    )
    def test_no_direct_clock_read_in_the_replay_packages(self, banned: str) -> None:
        for path in sources():
            assert banned not in code_of(path), f"{path.name} reads the process clock"

    def test_no_direct_clock_read_in_the_replay_api(self) -> None:
        for path in API_MODULES:
            assert path.exists(), path
            source = code_of(path)
            for banned in ("datetime.now(", "datetime.utcnow(", "time.time("):
                assert banned not in source, f"{path.name} reads the process clock"


@pytest.mark.unit
class TestReplayStaysInsideItsPhase:
    @pytest.mark.parametrize(
        "banned",
        [
            "backtest",
            "walk_forward",
            "monte_carlo",
            "optimi",
            "shadow_mode",
            "strategy_runner",
            "parameter_sweep",
            "websocket",
            "event_source",
            "broker",
            "midas",
            "place_order",
            "send_order",
        ],
    )
    def test_no_later_phase_or_execution_concept(self, banned: str) -> None:
        # Docstrings stripped, as everywhere here: a module that states it
        # reaches no broker is documenting the rule, not breaking it.
        for path in [*sources(), *API_MODULES]:
            assert banned not in code_of(path).lower(), f"{path.name} mentions {banned}"

    def test_replay_is_forward_only_in_its_vocabulary(self) -> None:
        """No rewind, no seek, no set-cursor. There is one verb and it is step."""
        for path in sources():
            source = code_of(path).lower()
            for banned in ("def rewind", "def seek", "def set_cursor", "def goto", "def undo"):
                assert banned not in source, f"{path.name} defines {banned}"

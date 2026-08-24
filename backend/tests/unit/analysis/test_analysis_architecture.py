"""Phase 4A boundaries: no LLM, no Phase 4B, no duplicated Phase 1-3 maths.

Mechanical checks rather than review habits. Imports are read with ``ast``
rather than grepped, because a text search flags the word in a docstring
explaining why the thing is absent - a mistake made once in Phase 3 and not
repeated.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.domain.analysis import engine as analysis_engine
from app.domain.analysis.contradictions import ContradictionType
from app.domain.analysis.evidence import EvidenceSource

PACKAGE = Path(analysis_engine.__file__).parent
MODULES = sorted(PACKAGE.glob("*.py"))


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


ALL_IMPORTS = {module.name: imports_of(module) for module in MODULES}


@pytest.mark.unit
def test_the_package_has_modules_to_check() -> None:
    """Guards every scan below from passing on an empty set."""
    assert len(MODULES) >= 5


# ----------------------------------------------------------------------
# No LLM, no network, no infrastructure
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_no_module_imports_an_llm_or_any_client() -> None:
    """Section 16: evidence comes from deterministic engines.

    An import here would put a model in the path of a value the rest of the
    system treats as measured fact.
    """
    forbidden = {
        "anthropic",
        "openai",
        "httpx",
        "requests",
        "aiohttp",
        "urllib",
        "urllib3",
        "socket",
        "langchain",
        "transformers",
    }
    for name, imports in ALL_IMPORTS.items():
        roots = {item.split(".")[0] for item in imports}
        assert forbidden.isdisjoint(roots), f"{name} imports {sorted(forbidden & roots)}"


@pytest.mark.unit
def test_no_module_imports_a_framework_or_a_database() -> None:
    forbidden = {"fastapi", "pydantic", "sqlalchemy", "alembic", "psycopg", "starlette"}
    for name, imports in ALL_IMPORTS.items():
        roots = {item.split(".")[0] for item in imports}
        assert forbidden.isdisjoint(roots), f"{name} imports {sorted(forbidden & roots)}"


@pytest.mark.unit
def test_the_package_reaches_no_outer_layer() -> None:
    for name, imports in ALL_IMPORTS.items():
        for item in imports:
            assert not item.startswith(("app.api", "app.adapters", "app.application")), name


@pytest.mark.unit
def test_evidence_never_depends_on_the_risk_engine() -> None:
    """Evidence describes the market; risk describes an account.

    Also mechanically preserves the Phase 4B boundary - converting evidence
    into a trade needs both, and that conversion does not exist yet.
    """
    for name, imports in ALL_IMPORTS.items():
        assert not any(item.startswith("app.domain.risk") for item in imports), name


# ----------------------------------------------------------------------
# No duplicated Phase 1-3 mathematics
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_no_module_imports_a_phase_1_formula_module() -> None:
    """The indicator formula modules are off limits.

    Importing ``smoothing`` or ``momentum`` would mean Phase 4 is about to
    compute something Phase 1 already owns; those modules hold mathematics and
    nothing else, so there is no legitimate reason to reach them. Reading a
    finished ``TechnicalSnapshot`` is the supported route.

    The Phase 2 structure modules are deliberately *not* on this list: they
    hold the ``Zone``, ``SwingPoint`` and ``DivergenceEvent`` dataclasses that
    evidence generation must name to read them. Banning those imports would
    ban the translation this phase exists to do. What matters is that no
    detector is *called* and no formula is *defined*, which the next two tests
    check directly.
    """
    forbidden = {
        "app.domain.technical.smoothing",
        "app.domain.technical.momentum",
        "app.domain.technical.volatility",
        "app.domain.technical.trend_strength",
        "app.domain.technical.vwap",
        "app.domain.technical.volume",
    }
    for name, imports in ALL_IMPORTS.items():
        assert forbidden.isdisjoint(imports), f"{name} imports {sorted(forbidden & imports)}"


@pytest.mark.unit
def test_no_module_calls_a_phase_1_to_3_detector() -> None:
    """Phase 4A translates finished output; it never runs an engine itself."""
    forbidden = {
        "compute_technicals",
        "analyse_structure",
        "detect_swings",
        "label_swings",
        "detect_structural_events",
        "build_zones",
        "detect_breakouts",
        "detect_retests",
        "detect_volume_divergence",
        "classify_regime",
        "historical_volatility",
    }
    for module in MODULES:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert forbidden.isdisjoint(called), f"{module.name} calls {sorted(forbidden & called)}"


@pytest.mark.unit
def test_the_package_defines_no_indicator_of_its_own() -> None:
    forbidden = {"ema", "sma", "rsi", "macd", "atr", "adx", "vwap", "wilder", "bollinger"}
    for module in MODULES:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        defined = {
            node.name.lstrip("_").lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        assert forbidden.isdisjoint(defined), f"{module.name} defines {sorted(forbidden & defined)}"


# ----------------------------------------------------------------------
# Phase 4B and Phase 5+ are absent
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_nothing_routes_a_strategy_or_argues_a_case() -> None:
    """The Phase 5+ boundary.

    Setup quality, entry quality and the Bull/Bear/Neutral scenarios now exist
    here - that is Phase 4B, and this test moved with it. What must stay
    absent is everything downstream: the §15 strategy router, the §24 devil's
    advocate and the Claude synthesis that owns the narrative.
    """
    forbidden = {
        "strategy_router",
        "route_strategy",
        "devils_advocate",
        "devil_advocate",
        "synthesise",
        "synthesize",
        "narrate",
        "final_action",
        "decide",
    }
    for module in MODULES:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        names = {
            node.name.lstrip("_").lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.ClassDef)
        }
        assert forbidden.isdisjoint(names), f"{module.name} defines {sorted(forbidden & names)}"


@pytest.mark.unit
def test_no_module_produces_a_final_trade_action() -> None:
    """Phase 4 stops at describing each case. LONG / SHORT / WAIT needs
    account risk and belongs to the later synthesis phase."""
    forbidden = {"LONG", "SHORT", "WAIT", "BUY", "SELL", "NO_TRADE"}
    for module in MODULES:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                value = node.value.value
                if isinstance(value, str) and value in forbidden:
                    targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
                    raise AssertionError(f"{module.name} defines {targets} = {value!r}")


@pytest.mark.unit
def test_no_analysis_type_carries_a_probability() -> None:
    """§19 forbids calling an analysis score a probability.

    ``quality`` and ``score`` are no longer on this list, because Phase 4B
    genuinely produces both - explicitly labelled heuristic, never calibrated
    against outcomes. What remains forbidden is the vocabulary of likelihood,
    which is the claim §19 actually rules out.
    """
    forbidden = {
        "probability",
        "confidence",
        "percent",
        "likelihood",
        "win_rate",
        "success_chance",
        "calibrated_probability",
        "odds",
        "edge",
    }
    for module in MODULES:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
                continue
            assert node.target.id.lower() not in forbidden, f"{module.name}: {node.target.id}"


@pytest.mark.unit
def test_no_contradiction_or_evidence_value_is_a_trade_instruction() -> None:
    """Evidence is bullish or bearish; only a later phase may say LONG."""
    forbidden = {"LONG", "SHORT", "BUY", "SELL", "ENTER", "EXIT"}
    assert forbidden.isdisjoint({member.value for member in ContradictionType})
    assert forbidden.isdisjoint({member.value for member in EvidenceSource})


@pytest.mark.unit
def test_the_analysis_package_has_no_execution_surface() -> None:
    """Section 120: nothing here can send, or grow into sending, an order."""
    forbidden = {"order", "broker", "midas", "execute", "position_open", "submit"}
    for module in MODULES:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        names = {
            node.name.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.ClassDef)
        }
        assert forbidden.isdisjoint(names), f"{module.name} defines {sorted(forbidden & names)}"

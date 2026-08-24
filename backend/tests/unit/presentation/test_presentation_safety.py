"""Explanation safety and package boundaries (§2, §7, §19, §25, §109).

Two jobs. First, prove the scanner still bites - a guard relaxed to tolerate
Turkish negation is worthless if it now tolerates everything. Second, prove no
Turkish text has leaked into a financial engine and no later phase has been
started here.

Every market is TEST_FIXTURE data.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.application.presentation import presenter as presenter_module
from app.application.presentation.education import all_concepts
from app.application.presentation.presenter import present_analysis
from app.application.presentation.safety import FORBIDDEN_CLAIMS, is_safe, violations
from app.application.presentation.terms import all_terms
from app.domain.analysis.engine import analyse_multi_timeframe
from app.domain.analysis.timeframes import ROLES_BROADEST_FIRST, TimeframeRole
from tests.factories_analysis import BEARISH_DRIFT, BULLISH_DRIFT, FLAT_DRIFT, market_view

PACKAGE = Path(presenter_module.__file__).parent
MODULES = sorted(PACKAGE.glob("*.py"))
DOMAIN = PACKAGE.parents[1] / "domain"


def analysis(drift: float = BULLISH_DRIFT, roles=ROLES_BROADEST_FIRST):  # type: ignore[no-untyped-def]
    return analyse_multi_timeframe(tuple(market_view(role, drift=drift) for role in roles))


def every_sentence() -> tuple[str, ...]:
    """Every sentence the layer can produce across several markets."""
    texts: list[str] = []
    for drift in (BULLISH_DRIFT, BEARISH_DRIFT, FLAT_DRIFT):
        texts.extend(present_analysis(analysis(drift)).simple.texts)
    texts.extend(present_analysis(analysis(roles=(TimeframeRole.REGIME,))).simple.texts)
    for explanation in all_concepts():
        texts.extend(explanation.answers)
    return tuple(texts)


# ----------------------------------------------------------------------
# The scanner still catches what it is for
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "forbidden",
    (
        "Bu işlem garanti kazandırır.",
        "Risksiz bir fırsat.",
        "Fiyat kesinlikle yükselecek.",
        "%78 ihtimalle yükselir.",
        "Kazanma olasılığı %65.",
        "Hemen al.",
        "RSI > 70 = SAT",
        "This trade is guaranteed.",
        "78% chance of a rally.",
        "Buy now.",
    ),
)
def test_the_scanner_catches_a_forbidden_claim(forbidden: str) -> None:
    """The negation allowance must not have gutted the guard.

    Without these, relaxing the patterns to tolerate *"garanti etmez"* could
    silently have made every pattern unreachable.
    """
    assert not is_safe(forbidden), f"not caught: {forbidden}"


@pytest.mark.unit
@pytest.mark.parametrize(
    "safe",
    (
        "Destek fiyatın oradan döneceğini garanti etmez.",
        "Bu bir başarı olasılığı değildir.",
        "Yüksek RSI 'sat' anlamına gelmez.",
        "Kaldıraç kazanma ihtimalini artırmaz.",
        "This does not guarantee anything.",
    ),
)
def test_the_scanner_allows_a_claim_being_denied(safe: str) -> None:
    """These sentences *are* the safety content.

    A guard that flagged them would push an author to delete the caveat, which
    is the opposite of what it exists for.
    """
    assert is_safe(safe), f"false positive: {violations(safe)}"


@pytest.mark.unit
def test_a_denial_far_away_does_not_excuse_a_promise() -> None:
    """The negation window is deliberately short."""
    text = "Bu işlem garanti kazandırır. " + "Piyasa dalgalıdır. " * 5 + "Kesin değil."
    assert not is_safe(text)


@pytest.mark.unit
def test_every_forbidden_claim_cites_the_section_it_comes_from() -> None:
    for claim in FORBIDDEN_CLAIMS:
        assert claim.section.startswith("§")
        assert claim.why.strip()


# ----------------------------------------------------------------------
# Nothing the layer produces makes a forbidden claim
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_no_produced_sentence_makes_a_forbidden_claim() -> None:
    offenders = [(text, violations(text)) for text in every_sentence() if not is_safe(text)]
    assert not offenders, offenders


@pytest.mark.unit
def test_no_sentence_instructs_the_reader_to_act() -> None:
    """§25's decision belongs to a later phase; this layer describes.

    Matched as imperatives rather than as substrings. Turkish builds the
    infinitive from the same stem, so a naive scan for "pozisyon aç" flags
    *"pozisyon açmak için bloke edilen tutar"* - which is the definition of
    margin, not an instruction to open one.
    """
    imperatives = (
        r"\bhemen\s+(al|sat)\b",
        r"\bşimdi\s+(al|sat)\b",
        r"\bpozisyon\s+aç\b(?!\w)",
        r"\bgir\b(?!\w)",
        r"\bçık\b(?!\w)",
    )
    for text in every_sentence():
        for pattern in imperatives:
            assert not re.search(pattern, text, flags=re.IGNORECASE), f"{pattern!r}: {text}"


@pytest.mark.unit
def test_a_quality_band_is_never_called_a_probability() -> None:
    result = present_analysis(analysis())
    setup = [item for item in result.simple.statements if item.topic.value == "SETUP"]
    assert setup
    for statement in setup:
        assert "olasılığı değildir" in statement.text


# ----------------------------------------------------------------------
# Turkish stays out of the financial engines
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_no_turkish_term_has_leaked_into_the_domain() -> None:
    """The engines must stay language-free.

    Scans for the registry's own Turkish strings, so the test cannot drift out
    of step with the vocabulary it protects. Short or ambiguous words are
    skipped - "Trend" and "Momentum" are English too.
    """
    distinctive = [
        item.turkish
        for item in all_terms()
        if item.turkish != item.english and len(item.turkish) > 4
    ]
    assert len(distinctive) > 20, "the guard would be checking almost nothing"

    for module in DOMAIN.rglob("*.py"):
        source = module.read_text(encoding="utf-8")
        for word in distinctive:
            assert word not in source, f"{module.name} contains the Turkish term {word!r}"


@pytest.mark.unit
def test_the_domain_never_imports_the_presentation_layer() -> None:
    """Also an import-linter contract; asserted here next to its reason."""
    for module in DOMAIN.rglob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("app.application"), module.name


# ----------------------------------------------------------------------
# Package boundaries
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_package_has_modules_to_check() -> None:
    assert len(MODULES) >= 6


@pytest.mark.unit
def test_no_module_imports_an_llm_or_a_client() -> None:
    """§16 keeps evidence deterministic; presenting it is deterministic too."""
    forbidden = {"anthropic", "openai", "httpx", "requests", "aiohttp", "langchain", "socket"}
    for module in MODULES:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        assert forbidden.isdisjoint(roots), f"{module.name} imports {sorted(forbidden & roots)}"


@pytest.mark.unit
def test_no_module_reaches_infrastructure_or_the_api_layer() -> None:
    for module in MODULES:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(("app.adapters", "app.api")), module.name


@pytest.mark.unit
def test_no_phase_6_or_later_feature_is_scaffolded() -> None:
    """Claude Vision, screenshots, the Devil's Advocate and the final
    LONG/SHORT/WAIT synthesis all belong to later phases."""
    forbidden = {
        "claude",
        "vision",
        "screenshot",
        "devils_advocate",
        "devil_advocate",
        "synthesise",
        "synthesize",
        "final_action",
        "decide",
        "strategy_router",
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
def test_no_phase_8_dashboard_or_ui_is_present() -> None:
    """Phase 5A builds typed outputs a later UI consumes; it is not the UI."""
    forbidden = {"render", "html", "template", "widget", "component", "dashboard", "chart"}
    for module in MODULES:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        names = {
            node.name.lstrip("_").lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.ClassDef)
        }
        assert forbidden.isdisjoint(names), f"{module.name} defines {sorted(forbidden & names)}"

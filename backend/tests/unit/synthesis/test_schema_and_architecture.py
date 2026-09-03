"""The strict output schema, and the Phase 7A boundaries (§11, §23, §25, §26).

Two concerns share a file because they are the same concern from two sides:
what a model is allowed to send, and what this phase is allowed to contain.

Every value is TEST_FIXTURE data.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.application.ports.synthesis import (
    MarketSynthesisProvider,
    SynthesisRequest,
)
from app.application.synthesis.prompt import build_prompt
from app.application.synthesis.rendered import SynthesisPrompt
from app.application.synthesis.schemas import (
    OUTPUT_SCHEMA_VERSION,
    SynthesisOutputSchema,
    scenario_cases_are_complete,
)
from app.domain.analysis.scenarios import ScenarioCase
from app.domain.synthesis.actions import FinalAction
from tests.factories_synthesis import minimal_context

BACKEND_ROOT = Path(__file__).resolve().parents[3]
SYNTHESIS_PATHS = (
    BACKEND_ROOT / "app" / "domain" / "synthesis",
    BACKEND_ROOT / "app" / "application" / "synthesis",
    BACKEND_ROOT / "app" / "application" / "ports" / "synthesis.py",
)


def payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "proposed_action": "WAIT",
        "summary": "A fixture summary.",
        "bull": {"case": "BULL", "narrative": "Bullish reading."},
        "bear": {"case": "BEAR", "narrative": "Bearish reading."},
        "neutral": {"case": "NEUTRAL", "narrative": "Neutral reading."},
        "devils_advocate": {"challenge": "A challenge.", "evidence_is_limited": True},
    }
    body.update(overrides)
    return body


# ----------------------------------------------------------------------
# §11: the strict contract
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_well_formed_response_parses_and_converts() -> None:
    schema = SynthesisOutputSchema.model_validate(payload())
    draft = schema.to_draft()

    assert draft.proposed_action is FinalAction.WAIT
    assert {item.case for item in draft.narratives} == set(ScenarioCase)
    assert draft.devils_advocate.evidence_is_limited


@pytest.mark.unit
def test_an_unexpected_field_is_refused_not_ignored() -> None:
    with pytest.raises(ValidationError):
        SynthesisOutputSchema.model_validate(payload(confidence=0.9))


@pytest.mark.unit
@pytest.mark.parametrize("section", ("bull", "bear", "neutral", "devils_advocate"))
def test_every_mandatory_section_is_required(section: str) -> None:
    body = payload()
    del body[section]
    with pytest.raises(ValidationError):
        SynthesisOutputSchema.model_validate(body)


@pytest.mark.unit
def test_an_invalid_action_is_refused() -> None:
    with pytest.raises(ValidationError):
        SynthesisOutputSchema.model_validate(payload(proposed_action="BUY"))


@pytest.mark.unit
def test_an_invalid_scenario_case_is_refused() -> None:
    with pytest.raises(ValidationError):
        SynthesisOutputSchema.model_validate(payload(bull={"case": "SIDEWAYS", "narrative": "x"}))


@pytest.mark.unit
def test_a_case_in_the_wrong_slot_is_detectable() -> None:
    """Field names can be satisfied while the content is swapped."""
    schema = SynthesisOutputSchema.model_validate(payload(bull={"case": "BEAR", "narrative": "x"}))
    assert not scenario_cases_are_complete(schema)
    assert scenario_cases_are_complete(SynthesisOutputSchema.model_validate(payload()))


@pytest.mark.unit
def test_an_empty_narrative_is_refused_at_the_schema() -> None:
    with pytest.raises(ValidationError):
        SynthesisOutputSchema.model_validate(payload(summary=""))


@pytest.mark.unit
def test_reference_lists_are_bounded() -> None:
    with pytest.raises(ValidationError):
        SynthesisOutputSchema.model_validate(
            payload(supporting_refs=tuple(f"EV-BULL-{i:03d}" for i in range(1, 200)))
        )


@pytest.mark.unit
def test_narratives_are_length_bounded() -> None:
    with pytest.raises(ValidationError):
        SynthesisOutputSchema.model_validate(payload(summary="x" * 5000))


@pytest.mark.unit
def test_the_schema_offers_nowhere_to_state_a_number_or_a_probability() -> None:
    """§14, §15: the cheapest enforcement is leaving no field for it."""
    fields = set(SynthesisOutputSchema.model_fields)
    forbidden = {
        "confidence",
        "probability",
        "entry",
        "entry_price",
        "stop",
        "stop_loss",
        "target",
        "take_profit",
        "position_size",
        "contracts",
        "risk_amount",
        "score",
        "setup_quality",
        "win_rate",
    }
    assert fields.isdisjoint(forbidden), f"schema exposes {fields & forbidden}"


@pytest.mark.unit
def test_no_schema_field_is_an_untyped_dict() -> None:
    for name, field in SynthesisOutputSchema.model_fields.items():
        rendered = str(field.annotation)
        assert "Any" not in rendered, f"{name} is untyped"
        assert "dict" not in rendered.lower(), f"{name} is an untyped mapping"


@pytest.mark.unit
def test_the_schema_version_is_stated() -> None:
    assert OUTPUT_SCHEMA_VERSION.startswith("synthesis-output/")


# ----------------------------------------------------------------------
# §23: the port
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_port_is_a_runtime_checkable_protocol_with_one_method() -> None:
    class Fake:
        async def synthesize(self, request: SynthesisRequest):  # type: ignore[no-untyped-def]
            return None

    assert isinstance(Fake(), MarketSynthesisProvider)


def fixture_request() -> SynthesisRequest:
    context = minimal_context()
    return SynthesisRequest(context=context, prompt=build_prompt(context))


@pytest.mark.unit
def test_the_request_carries_a_rendered_prompt_not_raw_text() -> None:
    """The prompt is a typed object only `build_prompt` produces.

    A bare `str` field would be text that could have skipped the untrusted-data
    boundary; a `SynthesisPrompt` says where it came from. It is carried rather
    than rebuilt in the adapter so the text measured against the token budget
    is the text actually sent.
    """
    request = fixture_request()
    fields = set(request.__dataclass_fields__)

    assert {"context", "prompt"} <= fields
    assert fields.isdisjoint({"system_prompt", "user_content", "images"})
    assert isinstance(request.prompt, SynthesisPrompt)
    assert request.prompt.version.startswith("synthesis-prompt/")


@pytest.mark.unit
def test_the_request_defaults_to_turkish() -> None:
    assert fixture_request().locale == "tr"


# ----------------------------------------------------------------------
# §26: architecture boundaries
# ----------------------------------------------------------------------


def synthesis_sources() -> list[Path]:
    found: list[Path] = []
    for path in SYNTHESIS_PATHS:
        found.extend(path.rglob("*.py") if path.is_dir() else [path])
    return found


@pytest.mark.unit
def test_no_provider_sdk_appears_anywhere_in_phase_7a() -> None:
    for path in synthesis_sources():
        source = path.read_text(encoding="utf-8")
        for banned in ("import anthropic", "from anthropic", "import openai", "import httpx"):
            assert banned not in source, f"{path.name} imports a provider SDK"


@pytest.mark.unit
def test_nothing_in_phase_7a_opens_a_network_connection() -> None:
    for path in synthesis_sources():
        source = path.read_text(encoding="utf-8")
        for banned in (
            "requests.get",
            "requests.post",
            "urllib.request",
            "socket.socket",
            "httpx.AsyncClient",
            "aiohttp",
        ):
            assert banned not in source, f"{path.name} looks like it makes a network call"


def imported_modules(path: Path) -> set[str]:
    """Every module a file actually imports, read from its syntax tree.

    A substring scan cannot do this job: these modules explain the Phase 6
    import cycle in their comments, and naming `app.application.vision` in
    prose is not importing it. The first version of this test failed on
    exactly that, which is the argument for parsing rather than grepping.
    """
    import ast  # noqa: PLC0415

    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.unit
def test_the_domain_half_stays_stdlib_only() -> None:
    """`app.domain.synthesis` may not import pydantic or any framework."""
    forbidden_roots = (
        "pydantic",
        "fastapi",
        "starlette",
        "sqlalchemy",
        "app.application",
        "app.adapters",
        "app.api",
        "app.core",
    )
    for path in (BACKEND_ROOT / "app" / "domain" / "synthesis").rglob("*.py"):
        for module in imported_modules(path):
            assert not module.startswith(forbidden_roots), (
                f"{path.name} imports {module}; the domain half stays stdlib and domain only"
            )


@pytest.mark.unit
def test_no_financial_formula_is_reimplemented_in_synthesis() -> None:
    """§2 and §25: this layer aggregates finished results, it does not compute."""
    for path in synthesis_sources():
        source = path.read_text(encoding="utf-8")
        for banned in (
            "def ema(",
            "def rsi(",
            "def atr(",
            "def adx(",
            "def vwap(",
            "def calculate_position_size",
            "def compute_margin",
            "def score_quality(",
        ):
            assert banned not in source, f"{path.name} reimplements {banned}"


def declared_identifiers(path: Path) -> set[str]:
    """Every name the code defines or uses, from its syntax tree.

    Identifiers, not prose. The synthesis prompt *forbids* broker instructions
    in as many words, and a substring scan flagged that prohibition as
    leakage - which is the wrong answer twice over, since the sentence is the
    safeguard. What matters is whether the code has broker machinery, and a
    name is what machinery has.
    """
    import ast  # noqa: PLC0415

    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            found.add(node.name)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
    return found


@pytest.mark.unit
def test_no_phase_8_or_execution_capability_exists() -> None:
    """§40: no order placement, no broker, no paper trading anywhere here."""
    banned = ("place_order", "submit_order", "broker", "midas", "paper_trade", "execute_trade")
    for path in synthesis_sources():
        for name in declared_identifiers(path):
            lowered = name.lower()
            for token in banned:
                assert token not in lowered, f"{path.name} declares {name}"


@pytest.mark.unit
@pytest.mark.parametrize(
    "module",
    (
        "app.domain.synthesis.actions",
        "app.domain.synthesis.references",
        "app.application.synthesis.context",
        "app.application.synthesis.canonical",
        "app.application.synthesis.validator",
        "app.application.synthesis.budget",
        "app.application.synthesis.audit",
        "app.application.ports.synthesis",
    ),
)
def test_each_module_imports_first_in_a_clean_interpreter(module: str) -> None:
    """The Phase 6 lesson: import-linter checks direction, not cycles.

    A cycle inside one layer satisfies every contract and still stops the
    application from starting, and a normal test run hides it whenever some
    earlier test warmed the package in a lucky order.
    """
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        cwd=BACKEND_ROOT,
        check=False,
    )
    assert result.returncode == 0, f"{module} cannot be imported alone:\n{result.stderr.strip()}"


@pytest.mark.unit
def test_the_synthesis_packages_export_nothing_at_package_level() -> None:
    """Same lesson: the facade that caused the Phase 6 cycle is not repeated."""
    for init in (
        BACKEND_ROOT / "app" / "domain" / "synthesis" / "__init__.py",
        BACKEND_ROOT / "app" / "application" / "synthesis" / "__init__.py",
    ):
        assert imported_modules(init) == set(), f"{init.parent.name} package re-exports"

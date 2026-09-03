"""Phase 6A boundaries: no Pydantic in the domain, no network, no Phase 7.

The load-bearing test here is §16: vision must never become the authority for
a calculated number, and that is enforced by making the calculation engines
unable to see the vision package at all.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.application.ports import screenshot as port_module
from app.application.vision import images as images_module
from app.domain.vision import assets as assets_module

DOMAIN_VISION = Path(assets_module.__file__).parent
APPLICATION_VISION = Path(images_module.__file__).parent
DOMAIN = DOMAIN_VISION.parent
PORT = Path(port_module.__file__)


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


DOMAIN_MODULES = sorted(DOMAIN_VISION.glob("*.py"))
APPLICATION_MODULES = sorted(APPLICATION_VISION.glob("*.py"))


@pytest.mark.unit
def test_there_are_modules_to_check() -> None:
    assert len(DOMAIN_MODULES) >= 5
    assert len(APPLICATION_MODULES) >= 3


# ----------------------------------------------------------------------
# The domain stays pure
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_vision_domain_imports_no_pydantic() -> None:
    """§14: strict schemas live at the boundary, not in the domain."""
    for module in DOMAIN_MODULES:
        roots = {name.split(".")[0] for name in imports_of(module)}
        assert "pydantic" not in roots, module.name


@pytest.mark.unit
def test_the_vision_domain_imports_no_sdk_framework_or_client() -> None:
    forbidden = {
        "anthropic",
        "openai",
        "httpx",
        "requests",
        "aiohttp",
        "urllib",
        "socket",
        "fastapi",
        "starlette",
        "sqlalchemy",
    }
    for module in DOMAIN_MODULES:
        roots = {name.split(".")[0] for name in imports_of(module)}
        assert forbidden.isdisjoint(roots), f"{module.name}: {sorted(forbidden & roots)}"


@pytest.mark.unit
def test_the_vision_domain_reaches_no_outer_layer() -> None:
    for module in DOMAIN_MODULES:
        for name in imports_of(module):
            assert not name.startswith(
                ("app.application", "app.adapters", "app.api", "app.core")
            ), module.name


@pytest.mark.unit
def test_no_module_in_either_package_opens_a_connection() -> None:
    """Phase 6A makes no network call - there is no adapter yet."""
    forbidden = {"anthropic", "openai", "httpx", "requests", "aiohttp", "socket", "urllib"}
    for module in DOMAIN_MODULES + APPLICATION_MODULES + [PORT]:
        roots = {name.split(".")[0] for name in imports_of(module)}
        assert forbidden.isdisjoint(roots), f"{module.name}: {sorted(forbidden & roots)}"


@pytest.mark.unit
def test_the_decoder_is_confined_to_one_module() -> None:
    """Pillow is used, and only in `decode.py`.

    Phase 6A used no decoder at all; the human review required a real one
    before bytes leave the process. The rule that replaced "no decoder" is
    "one decoder, in one place": the header parser stays decoder-free so the
    cheap preflight cannot be the thing that allocates a pixel buffer, and
    every other module reaches decoding through `verify_and_normalise`.
    """
    decoders = {"PIL", "cv2", "imageio", "skimage", "wand"}
    for module in APPLICATION_MODULES:
        roots = {name.split(".")[0] for name in imports_of(module)}
        used = decoders & roots
        if module.name == "decode.py":
            assert used <= {"PIL"}, f"decode.py uses an unexpected decoder: {sorted(used)}"
            continue
        assert not used, f"{module.name} imports a decoder: {sorted(used)}"


@pytest.mark.unit
def test_the_header_parser_still_decodes_nothing() -> None:
    """The preflight layer must stay cheap and allocation-free."""
    roots = {name.split(".")[0] for name in imports_of(APPLICATION_VISION / "images.py")}
    assert "PIL" not in roots


@pytest.mark.unit
def test_the_vision_domain_never_touches_a_decoder() -> None:
    for module in DOMAIN_MODULES:
        roots = {name.split(".")[0] for name in imports_of(module)}
        assert "PIL" not in roots, module.name


# ----------------------------------------------------------------------
# §16: vision is never a numeric authority
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_no_calculation_engine_can_see_the_vision_package() -> None:
    """The mechanical form of §16.

    An EMA, an RSI, a P&L or a position size cannot be derived from something
    a model read off a picture, because the module that would do it cannot
    import the module that holds it. Also an import-linter contract; asserted
    here next to the reason it exists.
    """
    engines = ("technical", "structure", "futures", "risk", "analysis", "suitability", "market")
    for engine in engines:
        for module in (DOMAIN / engine).rglob("*.py"):
            for name in imports_of(module):
                assert not name.startswith("app.domain.vision"), (
                    f"{engine}/{module.name} imports the vision package"
                )


@pytest.mark.unit
def test_the_vision_package_computes_no_indicator() -> None:
    forbidden = {"ema", "sma", "rsi", "macd", "atr", "adx", "vwap", "bollinger", "wilder"}
    for module in DOMAIN_MODULES + APPLICATION_MODULES:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        defined = {
            node.name.lstrip("_").lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        assert forbidden.isdisjoint(defined), f"{module.name}: {sorted(forbidden & defined)}"


@pytest.mark.unit
def test_the_vision_package_computes_no_money() -> None:
    forbidden = {
        "pnl",
        "position_size",
        "size_position",
        "margin",
        "risk_reward",
        "tick_size",
        "multiplier",
        "notional",
        "leverage",
    }
    for module in DOMAIN_MODULES + APPLICATION_MODULES:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        names = {
            node.name.lstrip("_").lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.ClassDef)
        }
        assert forbidden.isdisjoint(names), f"{module.name}: {sorted(forbidden & names)}"


@pytest.mark.unit
def test_no_observed_field_names_a_calculated_quantity() -> None:
    """Vision observes what is *drawn*. There is no field for a computed
    series, a position size or a margin requirement."""
    from app.domain.vision.extraction import ObservedField  # noqa: PLC0415

    forbidden = {"POSITION_SIZE", "MARGIN", "PNL", "RISK_REWARD", "TICK_SIZE", "MULTIPLIER"}
    assert forbidden.isdisjoint({item.value for item in ObservedField})


# ----------------------------------------------------------------------
# The port stays vendor-free
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_port_exposes_no_vendor_type_and_no_dictionary() -> None:
    """§15: no Anthropic request object, no SDK class, no bare dict."""
    source = PORT.read_text(encoding="utf-8")
    tree = ast.parse(source)

    roots = {name.split(".")[0] for name in imports_of(PORT)}
    assert "anthropic" not in roots

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        annotations = [argument.annotation for argument in node.args.args]
        annotations.append(node.returns)
        for annotation in annotations:
            if annotation is None:
                continue
            rendered = ast.unparse(annotation)
            assert "dict" not in rendered.lower(), f"{node.name}: {rendered}"
            assert "Any" not in rendered, f"{node.name}: {rendered}"


@pytest.mark.unit
def test_the_anthropic_sdk_lives_only_in_adapter_packages() -> None:
    """The SDK is confined to adapters, and to nothing else.

    Phase 6B allowed exactly one directory - `adapters/vision`. Phase 7B added
    `adapters/synthesis`, which is a genuine second adapter rather than a
    leak, so the invariant is stated for what it always meant: a vendor type
    may live in an adapter and may never reach a port, a use case or the
    domain. Adding a third adapter is a deliberate edit to this list.
    """
    backend = DOMAIN.parents[1]
    allowed = (
        backend / "app" / "adapters" / "vision",
        backend / "app" / "adapters" / "synthesis",
    )
    for module in (backend / "app").rglob("*.py"):
        if any(directory in module.parents for directory in allowed):
            continue
        roots = {name.split(".")[0] for name in imports_of(module)}
        assert "anthropic" not in roots, f"{module.relative_to(backend)} imports the SDK"


@pytest.mark.unit
def test_the_port_and_application_layer_hold_no_sdk_type() -> None:
    """The transport is the translation point; nothing above it sees a vendor
    class, which is what lets the whole path be faked without a key."""
    for module in [PORT, *APPLICATION_MODULES]:
        roots = {name.split(".")[0] for name in imports_of(module)}
        assert "anthropic" not in roots, module.name


# ----------------------------------------------------------------------
# No Phase 7 leakage
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_nothing_produces_a_trade_action() -> None:
    """§17: no LONG, no SHORT, no WAIT, no NO TRADE synthesis."""
    forbidden = {"LONG", "SHORT", "WAIT", "NO_TRADE", "BUY", "SELL"}
    for module in DOMAIN_MODULES + APPLICATION_MODULES + [PORT]:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                value = node.value.value
                if isinstance(value, str) and value in forbidden:
                    targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
                    raise AssertionError(f"{module.name} defines {targets} = {value!r}")


@pytest.mark.unit
def test_no_phase_7_concept_is_scaffolded() -> None:
    forbidden = {
        "analysisresult",
        "synthesis",
        "synthesise",
        "synthesize",
        "devils_advocate",
        "bull_case",
        "bear_case",
        "scenario",
        "correction_workflow",
        "prompt",
    }
    for module in DOMAIN_MODULES + APPLICATION_MODULES + [PORT]:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        names = {
            node.name.lstrip("_").lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.ClassDef)
        }
        assert forbidden.isdisjoint(names), f"{module.name}: {sorted(forbidden & names)}"


@pytest.mark.unit
def test_no_database_table_was_added() -> None:
    """§4: screenshots are not stored permanently in Phase 6A."""
    persistence = DOMAIN.parents[0] / "adapters" / "persistence"
    for module in persistence.rglob("*.py"):
        source = module.read_text(encoding="utf-8").lower()
        assert "screenshot" not in source, module.name


@pytest.mark.unit
def test_no_duplicated_deterministic_formula() -> None:
    """Vision does not reimplement anything Phases 1-5 own."""
    forbidden = {
        "compute_technicals",
        "analyse_structure",
        "score_setup",
        "score_entry",
        "size_position",
        "assess_margin",
        "risk_reward",
    }
    for module in DOMAIN_MODULES + APPLICATION_MODULES:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert forbidden.isdisjoint(called), f"{module.name}: {sorted(forbidden & called)}"

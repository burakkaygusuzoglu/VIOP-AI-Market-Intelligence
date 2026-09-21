"""The multi-asset boundary: identity, provenance, delegation, refusals (Phase 8.5).

Parity with Phase 8 lives in ``test_futures_parity.py``. This file proves the
properties the refactor *added*:

* an asset class is an enum value, and only one of them is implemented;
* a typed classification never becomes a verified one;
* the generic risk engines read every product fact through the policy, and
  refuse - never fall back - when the product is not one they can compute;
* no fake product policy exists;
* generic code holds no futures constant, tick size or currency.
"""

from __future__ import annotations

import ast
import dataclasses
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.common.enums import Direction
from app.domain.common.verification import VerificationStatus, VerifiedValue
from app.domain.futures.contract import FuturesContract
from app.domain.futures.policy import FUTURES_CAPABILITIES, FuturesProductPolicy
from app.domain.instrument import (
    IMPLEMENTATION,
    AssetClass,
    ImplementationStatus,
    InstrumentId,
    InstrumentIdentityError,
    MarginFeasibility,
    MarginRequirement,
    PriceIncrementCheck,
    ProductCapabilities,
    ProductPolicy,
    ProductVocabulary,
    Support,
    TickFeasibility,
    UnsupportedAssetClassError,
    UnsupportedQuantitySemanticsError,
    implemented_asset_classes,
    require_implemented,
)
from app.domain.risk.margin import assess_margin_for_product
from app.domain.risk.pnl import pnl_for_product
from app.domain.risk.sizing import AccountState, RiskMode, RiskPolicy, size_for_product
from app.domain.risk.whatif import simulate_for_product

APP = Path(__file__).resolve().parents[3] / "app"
AS_OF = datetime(2026, 1, 1, tzinfo=UTC)


def fact[T](
    value: T, status: VerificationStatus = VerificationStatus.VERIFIED_CURRENT_FACT
) -> VerifiedValue[T]:
    return VerifiedValue(value=value, status=status, source="fixture", as_of=AS_OF)


def contract(
    *,
    multiplier_status: VerificationStatus = VerificationStatus.VERIFIED_CURRENT_FACT,
    tick_status: VerificationStatus = VerificationStatus.VERIFIED_CURRENT_FACT,
    margin: VerificationStatus | None = VerificationStatus.VERIFIED_CURRENT_FACT,
) -> FuturesContract:
    return FuturesContract(
        symbol="TEST_FIXTURE_FUT",
        underlying_symbol="TEST_FIXTURE_UNDERLYING",
        contract_name="fixture",
        multiplier=fact(Decimal("10"), multiplier_status),
        tick_size=fact(Decimal("0.25"), tick_status),
        initial_margin=fact(Decimal("5000"), margin) if margin is not None else None,
    )


# ----------------------------------------------------------------------
# A test double. Not a product: it delegates every fact to a real futures
# policy and changes exactly one thing, so a refusal can be attributed.
# ----------------------------------------------------------------------


@dataclass
class Relabelled:
    inner: FuturesProductPolicy
    asset_class: AssetClass = AssetClass.FUTURES
    fractional: Support = Support.UNSUPPORTED
    words: ProductVocabulary | None = None
    calls: list[str] = field(default_factory=list)

    @property
    def instrument(self) -> InstrumentId:
        return InstrumentId(symbol=self.inner.contract.symbol, asset_class=fact(self.asset_class))

    @property
    def capabilities(self) -> ProductCapabilities:
        return dataclasses.replace(FUTURES_CAPABILITIES, fractional_quantity=self.fractional)

    @property
    def vocabulary(self) -> ProductVocabulary:
        return self.words or self.inner.vocabulary

    def require_calculable(self, operation: str) -> None:
        self.calls.append("require_calculable")
        self.inner.require_calculable(operation)

    def point_value(self) -> VerifiedValue[Decimal]:
        self.calls.append("point_value")
        return self.inner.point_value()

    def price_increment(self) -> VerifiedValue[Decimal] | None:
        self.calls.append("price_increment")
        return self.inner.price_increment()

    def price_increment_check(
        self, entry_price: Decimal, stop_price: Decimal
    ) -> PriceIncrementCheck:
        self.calls.append("price_increment_check")
        return self.inner.price_increment_check(entry_price, stop_price)

    def margin_requirement(self) -> MarginRequirement:
        self.calls.append("margin_requirement")
        return self.inner.margin_requirement()


ACCOUNT = AccountState(equity=Decimal("100000"))
BUDGET = RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("1000"))


def every_engine(product: ProductPolicy) -> Iterator[tuple[str, Callable[[], object]]]:
    yield (
        "sizing",
        lambda: size_for_product(
            Direction.LONG, Decimal("120"), Decimal("118"), product, ACCOUNT, BUDGET
        ),
    )
    yield "margin", lambda: assess_margin_for_product(product, ACCOUNT, Decimal("120"), 1, BUDGET)
    yield "pnl", lambda: pnl_for_product(product, Direction.LONG, Decimal("120"), Decimal("121"), 1)
    yield (
        "whatif",
        lambda: simulate_for_product(
            product, Direction.LONG, Decimal("120"), 1, Decimal("1000"), (Decimal("121"),)
        ),
    )


# ----------------------------------------------------------------------
# Asset classes
# ----------------------------------------------------------------------


class TestAnEnumValueIsNotAnImplementation:
    def test_every_asset_class_has_an_explicit_status(self) -> None:
        assert set(IMPLEMENTATION) == set(AssetClass)

    def test_only_futures_is_implemented(self) -> None:
        assert implemented_asset_classes() == (AssetClass.FUTURES,)

    @pytest.mark.parametrize(
        "asset_class",
        [AssetClass.EQUITY, AssetClass.CRYPTO_SPOT, AssetClass.CRYPTO_PERPETUAL, AssetClass.FX],
    )
    def test_unimplemented_classes_are_refused(self, asset_class: AssetClass) -> None:
        assert IMPLEMENTATION[asset_class] is ImplementationStatus.NOT_IMPLEMENTED
        with pytest.raises(UnsupportedAssetClassError, match=asset_class.value):
            require_implemented(asset_class, "anything")

    def test_the_registry_cannot_be_amended_at_runtime(self) -> None:
        with pytest.raises(TypeError):
            # Deliberately writes to a read-only mapping; the type checker forbids it
            # too, and this proves the runtime does as well.
            IMPLEMENTATION[AssetClass.EQUITY] = ImplementationStatus.IMPLEMENTED  # type: ignore[index]


# ----------------------------------------------------------------------
# Identity and provenance
# ----------------------------------------------------------------------


class TestIdentityCarriesProvenance:
    def test_asset_class_has_no_default(self) -> None:
        """Nothing becomes a future by omission."""
        fields = {f.name: f for f in dataclasses.fields(InstrumentId)}

        assert fields["asset_class"].default is dataclasses.MISSING
        with pytest.raises(TypeError):
            # Deliberately omits the required class, to prove there is no default.
            InstrumentId(symbol="X")  # type: ignore[call-arg]

    @pytest.mark.parametrize("asset_class", list(AssetClass))
    def test_a_typed_classification_is_never_verified(self, asset_class: AssetClass) -> None:
        declared = InstrumentId.user_declared("BTCUSDT", asset_class)

        assert declared.asset_class.status is VerificationStatus.UNVERIFIED
        assert declared.classification_is_verified is False
        assert declared.asset_class.source == "user input"
        assert declared.quote_currency is None

    def test_a_user_declared_class_cannot_drive_a_calculation(self) -> None:
        """Even FUTURES, typed by a user, is not a basis for arithmetic."""
        declared = InstrumentId.user_declared("TEST_FIXTURE_FUT", AssetClass.FUTURES)

        with pytest.raises(Exception) as caught:
            declared.asset_class.require_authoritative("sizing")
        assert "UNVERIFIED" in str(caught.value)

    def test_blank_symbol_is_refused(self) -> None:
        with pytest.raises(InstrumentIdentityError):
            InstrumentId(symbol="   ", asset_class=fact(AssetClass.FUTURES))

    def test_a_classification_must_be_an_asset_class(self) -> None:
        with pytest.raises(InstrumentIdentityError):
            # Deliberately passes a string where an AssetClass is required.
            InstrumentId(symbol="X", asset_class=fact("FUTURES"))  # type: ignore[arg-type]

    def test_quote_currency_is_never_filled_in(self) -> None:
        assert FuturesProductPolicy(contract()).instrument.quote_currency is None


# ----------------------------------------------------------------------
# The futures policy
# ----------------------------------------------------------------------


class TestFuturesPolicy:
    def test_it_satisfies_the_protocol(self) -> None:
        assert isinstance(FuturesProductPolicy(contract()), ProductPolicy)

    def test_point_value_is_the_contract_multiplier_itself(self) -> None:
        item = contract()
        assert FuturesProductPolicy(item).point_value() is item.multiplier

    @pytest.mark.parametrize(
        ("margin", "expected"),
        [
            (VerificationStatus.VERIFIED_CURRENT_FACT, MarginFeasibility.KNOWN),
            (VerificationStatus.DEVELOPMENT_DEFAULT, MarginFeasibility.UNVERIFIED),
            (None, MarginFeasibility.MISSING),
        ],
    )
    def test_margin_requirement_states(
        self, margin: VerificationStatus | None, expected: MarginFeasibility
    ) -> None:
        requirement = FuturesProductPolicy(contract(margin=margin)).margin_requirement()

        assert requirement.feasibility is expected
        assert (requirement.per_unit is not None) is (expected is MarginFeasibility.KNOWN)

    def test_contracts_are_whole_units(self) -> None:
        assert FuturesProductPolicy(contract()).capabilities.fractional_quantity is (
            Support.UNSUPPORTED
        )

    def test_an_unverified_tick_is_never_assumed_to_be_a_cent(self) -> None:
        """120.00 and 118.00 sit on any 0.01 grid - and still are not called ON_GRID."""
        check = FuturesProductPolicy(
            contract(tick_status=VerificationStatus.DEVELOPMENT_DEFAULT)
        ).price_increment_check(Decimal("120.00"), Decimal("118.00"))

        assert check.feasibility is TickFeasibility.UNVERIFIED

    def test_the_verified_grid_is_the_contracts_own(self) -> None:
        """0.10 is a cent multiple but not a 0.25 tick."""
        check = FuturesProductPolicy(contract()).price_increment_check(
            Decimal("120.10"), Decimal("118.00")
        )

        assert check.feasibility is TickFeasibility.OFF_GRID
        assert "0.25" in check.detail

    def test_margin_requirement_cannot_hide_an_unknown_as_zero(self) -> None:
        with pytest.raises(ValueError):
            MarginRequirement(MarginFeasibility.UNVERIFIED, Decimal("0"))
        with pytest.raises(ValueError):
            MarginRequirement(MarginFeasibility.KNOWN, None)


# ----------------------------------------------------------------------
# Delegation and refusal
# ----------------------------------------------------------------------


class TestRiskDelegatesToThePolicy:
    def test_sizing_reads_every_product_fact_through_the_policy(self) -> None:
        spy = Relabelled(FuturesProductPolicy(contract()))

        size_for_product(Direction.LONG, Decimal("120"), Decimal("118"), spy, ACCOUNT, BUDGET)

        assert set(spy.calls) == {
            "require_calculable",
            "point_value",
            "price_increment_check",
            "margin_requirement",
        }

    def test_margin_pnl_and_whatif_read_the_point_value_through_the_policy(self) -> None:
        for name, call in every_engine(spy := Relabelled(FuturesProductPolicy(contract()))):
            spy.calls.clear()
            call()
            assert "point_value" in spy.calls, name
            assert "require_calculable" in spy.calls, name

    def test_the_unit_noun_comes_from_the_product(self) -> None:
        """Explanations stay precise per product - without implementing one."""
        spy = Relabelled(
            FuturesProductPolicy(contract()),
            words=ProductVocabulary(
                unit="widget", unit_counted="widget(s)", point_value="w", point_value_qualified="w"
            ),
        )

        sizing = size_for_product(
            Direction.LONG, Decimal("120"), Decimal("118"), spy, ACCOUNT, BUDGET
        )

        assert "widget(s) permitted" in sizing.reason
        assert "contract" not in sizing.reason


class TestNothingFallsBackToFutures:
    @pytest.mark.parametrize(
        "asset_class",
        [AssetClass.EQUITY, AssetClass.CRYPTO_SPOT, AssetClass.CRYPTO_PERPETUAL, AssetClass.FX],
    )
    def test_every_money_engine_refuses_an_unimplemented_class(
        self, asset_class: AssetClass
    ) -> None:
        """Every fact is valid futures data - and the answer is still no."""
        product = Relabelled(FuturesProductPolicy(contract()), asset_class=asset_class)

        for name, call in every_engine(product):
            with pytest.raises(UnsupportedAssetClassError):
                call()
            assert product.calls == [], f"{name} read product facts before refusing"

    @pytest.mark.parametrize("fractional", [Support.SUPPORTED, Support.UNKNOWN])
    def test_integral_engines_refuse_unestablished_quantity(self, fractional: Support) -> None:
        product = Relabelled(FuturesProductPolicy(contract()), fractional=fractional)

        for _, call in every_engine(product):
            with pytest.raises(UnsupportedQuantitySemanticsError):
                call()


# ----------------------------------------------------------------------
# Mechanical scans
# ----------------------------------------------------------------------


def python_files(*packages: str) -> list[Path]:
    return sorted(path for package in packages for path in (APP / package).rglob("*.py"))


def code_only(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


GENERIC = (
    "domain/risk",
    "domain/instrument",
    "domain/technical",
    "domain/structure",
    "domain/market",
    "domain/analysis",
    "domain/suitability",
    "domain/synthesis",
    "application/analysis",
    "application/synthesis",
)


def string_constants(tree: ast.Module) -> Iterator[ast.Constant]:
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            yield node


class TestGenericCodeHoldsNoProductFact:
    def test_no_generic_module_names_the_futures_class(self) -> None:
        """Only the registry and the futures package may say FUTURES."""
        offenders = []
        for path in python_files(*GENERIC):
            if path.name == "asset_class.py":
                continue
            tree = code_only(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "FUTURES":
                    offenders.append(f"{path.name}:{node.lineno}")
            for const in string_constants(tree):
                if const.value == "FUTURES":
                    offenders.append(f"{path.name}:{const.lineno}")

        assert offenders == []

    def test_no_generic_module_assumes_a_price_increment(self) -> None:
        offenders = []
        for path in python_files(*GENERIC):
            tree = code_only(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "quantize":
                    offenders.append(f"{path.name}:{node.lineno} quantize")
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "Decimal"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and re.fullmatch(r"0\.(0*1|0+5)", str(node.args[0].value))
                ):
                    offenders.append(f"{path.name}:{node.lineno} Decimal({node.args[0].value!r})")

        assert offenders == []

    def test_no_currency_is_hard_coded_anywhere_in_domain_or_application(self) -> None:
        codes = {"TRY", "USD", "EUR", "GBP", "USDT", "BTC"}
        offenders = [
            f"{path.name}:{const.lineno} {const.value}"
            for path in python_files("domain", "application")
            for const in string_constants(code_only(path))
            if const.value in codes
        ]

        assert offenders == []

    def test_the_generic_risk_engine_touches_no_futures_attribute(self) -> None:
        """Belt and braces beside the import contract: no duck-typed reach-through."""
        futures_only = {"multiplier", "tick_size", "initial_margin", "contract", "valuation"}
        offenders = [
            f"{path.name}:{node.lineno} .{node.attr}"
            for path in python_files("domain/risk", "domain/instrument")
            for node in ast.walk(code_only(path))
            if isinstance(node, ast.Attribute) and node.attr in futures_only
        ]

        assert offenders == []

    def test_no_asset_class_switch_outside_the_registry(self) -> None:
        """Product behaviour is delegated, not branched on."""
        offenders = []
        for path in python_files("domain", "application", "api"):
            # The registry declares classes, and a product package validates
            # its own record's class. Neither is dispatch on an asset class.
            if path.name == "asset_class.py" or "futures" in path.parts:
                continue
            for node in ast.walk(code_only(path)):
                if isinstance(node, ast.Match):
                    subject = ast.unparse(node.subject)
                    if "asset_class" in subject:
                        offenders.append(f"{path.name}:{node.lineno} match {subject}")
                if isinstance(node, ast.Compare):
                    text = ast.unparse(node)
                    if "AssetClass." in text:
                        offenders.append(f"{path.name}:{node.lineno} {text}")

        assert offenders == []


class TestNoFakeProducts:
    def test_the_only_product_policy_is_the_futures_one(self) -> None:
        """A class with the policy's shape is a policy, whatever it is called."""
        shape = {"point_value", "price_increment_check", "margin_requirement"}
        implementations = []
        for path in python_files("domain", "application", "adapters", "api"):
            for node in ast.walk(code_only(path)):
                if isinstance(node, ast.ClassDef):
                    methods = {
                        item.name
                        for item in node.body
                        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                    }
                    if shape <= methods and node.name != "ProductPolicy":
                        implementations.append(node.name)

        assert implementations == ["FuturesProductPolicy"]

    def test_no_placeholder_policy_names_exist(self) -> None:
        pattern = re.compile(r"class\s+(Equity|Stock|Crypto|Perpetual|Fx|Forex)\w*Policy")
        offenders = [
            path.name
            for path in python_files("domain", "application", "adapters", "api")
            if pattern.search(path.read_text(encoding="utf-8"))
        ]

        assert offenders == []

"""Golden futures behaviour, captured before Phase 8.5 moved anything (§21).

## How this file was used

1. Written, and run once, while ``backend/app`` was still the **Phase 8 code,
   unmodified** (commit ``1c3d555``; ``git diff -- backend/app`` empty at the
   time). That run recorded ``golden_phase8_baseline.json``. No value in that
   file was typed by hand or produced by refactored code.
2. The refactor then moved the futures money paths behind ``ProductPolicy``.
3. ``test_futures_parity.py`` re-runs every case here and requires the output
   to equal the recorded baseline exactly.

The four futures entry points are imported from ``app.domain.futures.risk``,
where Phase 8.5 put them. At capture time that module did not exist yet, so the
one-off capture runner registered it in ``sys.modules`` as an alias of the
Phase 8 functions in ``app.domain.risk`` - without editing any application
file. After the refactor the module was only mechanically reformatted by
``ruff format`` / ``ruff check --fix`` (line wrapping, import order); no case,
input or serialisation rule changed, and the baseline file was never rewritten.
That the cases still compute the same inputs is itself checked: the parity test
compares every result against the recorded file, so an altered input would
surface as a mismatch.

## What is compared

Every authoritative value the Phase 3 engines produce - sizing outcomes and
counts, loss per contract, binding-constraint wording, tick and margin
feasibility, margin panels and warnings, P&L gross/net/bounds, what-if
scenarios, risk/reward, contract issues and state - and the exceptions raised
where the engines refuse (type *and* message).

For the Phase 8 API, each response is reduced to a SHA-256 over its canonical
JSON after removing the one execution-specific field (``generated_at``) and
any key Phase 8.5 added deliberately. A readable extract of the risk-relevant
blocks is stored beside the digest so a failure shows *what* moved.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from app.domain.common.enums import Direction
from app.domain.common.verification import VerificationStatus, VerifiedValue
from app.domain.futures.contract import (
    ContractExpiry,
    FuturesContract,
    SettlementType,
    ValuationModel,
)
from app.domain.futures.risk import (
    assess_margin,
    calculate_contract_pnl,
    simulate_contract,
    size_position,
)
from app.domain.futures.validation import (
    ContractIssue,
    ContractStateResult,
    TickGridResult,
    check_tick_grid,
    contract_issues,
    contract_state,
    implied_tick_value,
)
from app.domain.risk.margin import MarginAssessment
from app.domain.risk.pnl import PnLResult, TradeCosts
from app.domain.risk.reward import RiskReward, risk_reward
from app.domain.risk.sizing import AccountState, PositionSizing, RiskMode, RiskPolicy

GOLDEN_PATH = Path(__file__).with_name("golden_phase8_baseline.json")

AS_OF = datetime(2026, 1, 1, tzinfo=UTC)
SYMBOL = "TEST_FIXTURE_FUT"

PHASE_8_5_ADDITIVE_KEYS: frozenset[str] = frozenset({"asset_class", "asset_class_status"})
"""Response keys Phase 8.5 added on purpose. Removed before digesting, so the
digest proves that *nothing else* in a Phase 8 response changed."""


# ----------------------------------------------------------------------
# Serialisation
# ----------------------------------------------------------------------


def plain(value: Any) -> Any:
    """A JSON-safe, deterministic rendering of an engine result."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: plain(getattr(value, field.name)) for field in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, tuple | list):
        return [plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    return value


def outcome(call: Callable[[], Any]) -> Any:
    """The result, or the refusal - both are behaviour worth pinning."""
    try:
        return {"result": plain(call())}
    except Exception as error:  # noqa: BLE001 - the refusal *is* the recorded value
        return {"raises": type(error).__name__, "message": str(error)}


# ----------------------------------------------------------------------
# Contracts
# ----------------------------------------------------------------------


def fact[T](
    value: T, status: VerificationStatus = VerificationStatus.VERIFIED_CURRENT_FACT
) -> VerifiedValue[T]:
    return VerifiedValue(value=value, status=status, source="golden fixture", as_of=AS_OF)


def futures(
    *,
    multiplier: str = "10",
    tick: str = "0.25",
    margin: str | None = "5000",
    multiplier_status: VerificationStatus = VerificationStatus.VERIFIED_CURRENT_FACT,
    tick_status: VerificationStatus = VerificationStatus.VERIFIED_CURRENT_FACT,
    margin_status: VerificationStatus = VerificationStatus.VERIFIED_CURRENT_FACT,
    tick_value: str | None = None,
    valuation: ValuationModel = ValuationModel.LINEAR,
    expiry: ContractExpiry | None = None,
) -> FuturesContract:
    return FuturesContract(
        symbol=SYMBOL,
        underlying_symbol="TEST_FIXTURE_UNDERLYING",
        contract_name="golden fixture contract",
        multiplier=fact(Decimal(multiplier), multiplier_status),
        tick_size=fact(Decimal(tick), tick_status),
        tick_value=fact(Decimal(tick_value)) if tick_value is not None else None,
        initial_margin=fact(Decimal(margin), margin_status) if margin is not None else None,
        maintenance_margin=None,
        expiry=expiry,
        settlement=fact(SettlementType.CASH),
        valuation=valuation,
    )


CONTRACTS: dict[str, Callable[[], FuturesContract]] = {
    "verified": lambda: futures(),
    "verified_no_margin": lambda: futures(margin=None),
    "unverified_all": lambda: futures(
        multiplier_status=VerificationStatus.UNVERIFIED,
        tick_status=VerificationStatus.UNVERIFIED,
        margin_status=VerificationStatus.UNVERIFIED,
    ),
    "fixture_multiplier": lambda: futures(multiplier_status=VerificationStatus.TEST_FIXTURE),
    "tick_unverified": lambda: futures(tick_status=VerificationStatus.DEVELOPMENT_DEFAULT),
    "margin_unverified": lambda: futures(margin_status=VerificationStatus.MOCK_DATA),
    "spec_section_42": lambda: futures(multiplier="100", tick="1", margin="250"),
    "tick_value_consistent": lambda: futures(tick_value="2.50"),
    "tick_value_inconsistent": lambda: futures(tick_value="3"),
    "inverse": lambda: futures(valuation=ValuationModel.INVERSE),
    "quanto": lambda: futures(valuation=ValuationModel.QUANTO),
    "fractional_multiplier": lambda: futures(multiplier="0.1", tick="0.01", margin="40"),
}


def fixed(risk: str, cap: int | None = None) -> RiskPolicy:
    return RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal(risk), max_contracts=cap)


def percent(ratio: str, cap: int | None = None) -> RiskPolicy:
    return RiskPolicy(mode=RiskMode.PERCENTAGE, risk_ratio=Decimal(ratio), max_contracts=cap)


def account(equity: str, used: str = "0") -> AccountState:
    return AccountState(equity=Decimal(equity), used_margin=Decimal(used))


D = Decimal
LONG, SHORT, NEUTRAL = Direction.LONG, Direction.SHORT, Direction.NEUTRAL


def later[**P, R](fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> Callable[[], R]:
    """Defer a call, typed.

    Replaced default-argument lambdas that mypy could not type, which had
    needed eight misc-code suppressions. The runners below perform, at call time, exactly
    the conversions and the lazy contract construction those lambdas did, so
    no case input changed - which ``test_futures_parity.py`` confirms by
    matching all 99 recorded results.
    """
    return lambda: fn(*args, **kwargs)


def _size(d: Direction, e: str, s: str, k: str, a: AccountState, p: RiskPolicy) -> PositionSizing:
    return size_position(d, D(e), D(s), CONTRACTS[k](), a, p)


def _margin(
    k: str, a: AccountState, e: str, q: int, p: RiskPolicy, r: str | None
) -> MarginAssessment:
    return assess_margin(
        CONTRACTS[k](), a, D(e), q, p, risk_to_stop=D(r) if r is not None else None
    )


def _pnl(k: str, d: Direction, e: str, x: str, q: int, co: TradeCosts | None) -> PnLResult:
    return calculate_contract_pnl(CONTRACTS[k](), d, D(e), D(x), q, co)


def _reward(d: Direction, e: str, s: str, t: str) -> RiskReward:
    return risk_reward(d, D(e), D(s), D(t))


def _issues(build: Callable[[], FuturesContract]) -> tuple[ContractIssue, ...]:
    return contract_issues(build())


def _implied(k: str) -> Decimal | None:
    return implied_tick_value(CONTRACTS[k]())


def _grid(price: str, tick: str) -> TickGridResult:
    return check_tick_grid(D(price), D(tick))


def _state(expiry: ContractExpiry | None, moment: datetime) -> ContractStateResult:
    return contract_state(futures(expiry=expiry), moment)


# ----------------------------------------------------------------------
# Domain cases
# ----------------------------------------------------------------------


def sizing_cases() -> Iterator[tuple[str, Callable[[], Any]]]:
    rows: list[tuple[str, Direction, str, str, str, AccountState, RiskPolicy]] = [
        (
            "long_allowed_margin_binds",
            LONG,
            "120.00",
            "118.00",
            "verified",
            account("100000"),
            fixed("1000"),
        ),
        (
            "long_allowed_risk_binds",
            LONG,
            "120.00",
            "118.00",
            "verified",
            account("1000000"),
            fixed("100"),
        ),
        (
            "long_allowed_cap_binds",
            LONG,
            "120.00",
            "118.00",
            "verified",
            account("100000"),
            fixed("1000", 7),
        ),
        (
            "long_cap_zero",
            LONG,
            "120.00",
            "118.00",
            "verified",
            account("100000"),
            fixed("1000", 0),
        ),
        ("short_allowed", SHORT, "118.00", "120.00", "verified", account("100000"), fixed("1000")),
        (
            "percentage_mode",
            LONG,
            "120.00",
            "118.00",
            "verified",
            account("250000"),
            percent("0.02"),
        ),
        ("wrong_side_long", LONG, "120.00", "121.00", "verified", account("100000"), fixed("1000")),
        (
            "wrong_side_short",
            SHORT,
            "120.00",
            "119.00",
            "verified",
            account("100000"),
            fixed("1000"),
        ),
        (
            "stop_equals_entry",
            LONG,
            "120.00",
            "120.00",
            "verified",
            account("100000"),
            fixed("1000"),
        ),
        (
            "neutral_direction",
            NEUTRAL,
            "120.00",
            "118.00",
            "verified",
            account("100000"),
            fixed("1000"),
        ),
        ("zero_entry", LONG, "0", "118.00", "verified", account("100000"), fixed("1000")),
        ("negative_stop", LONG, "120.00", "-1", "verified", account("100000"), fixed("1000")),
        ("zero_equity", LONG, "120.00", "118.00", "verified", account("0"), fixed("1000")),
        ("entry_off_grid", LONG, "120.10", "118.00", "verified", account("100000"), fixed("1000")),
        ("both_off_grid", LONG, "120.10", "118.05", "verified", account("100000"), fixed("1000")),
        (
            "unverified_contract",
            LONG,
            "120.00",
            "118.00",
            "unverified_all",
            account("100000"),
            fixed("1000"),
        ),
        (
            "fixture_multiplier",
            LONG,
            "120.00",
            "118.00",
            "fixture_multiplier",
            account("100000"),
            fixed("1000"),
        ),
        (
            "margin_missing",
            LONG,
            "120.00",
            "118.00",
            "verified_no_margin",
            account("100000"),
            fixed("1000"),
        ),
        (
            "margin_unverified",
            LONG,
            "120.00",
            "118.00",
            "margin_unverified",
            account("100000"),
            fixed("1000"),
        ),
        (
            "tick_unverified",
            LONG,
            "120.00",
            "118.00",
            "tick_unverified",
            account("100000"),
            fixed("1000"),
        ),
        (
            "tick_unverified_off_grid",
            LONG,
            "120.10",
            "118.00",
            "tick_unverified",
            account("100000"),
            fixed("1000"),
        ),
        (
            "spec_section_42_not_permitted",
            LONG,
            "105",
            "104",
            "spec_section_42",
            account("2500"),
            fixed("75"),
        ),
        (
            "spec_section_42_permitted",
            LONG,
            "105",
            "104",
            "spec_section_42",
            account("2500"),
            fixed("250"),
        ),
        (
            "margin_deficit",
            LONG,
            "120.00",
            "118.00",
            "verified",
            account("2500", "2700"),
            fixed("1000"),
        ),
        (
            "fully_committed",
            LONG,
            "120.00",
            "118.00",
            "verified",
            account("5000", "5000"),
            fixed("1000"),
        ),
        (
            "tick_value_consistent",
            LONG,
            "120.00",
            "118.00",
            "tick_value_consistent",
            account("100000"),
            fixed("1000"),
        ),
        (
            "tick_value_inconsistent",
            LONG,
            "120.00",
            "118.00",
            "tick_value_inconsistent",
            account("100000"),
            fixed("1000"),
        ),
        (
            "inverse_valuation",
            LONG,
            "120.00",
            "118.00",
            "inverse",
            account("100000"),
            fixed("1000"),
        ),
        ("quanto_valuation", LONG, "120.00", "118.00", "quanto", account("100000"), fixed("1000")),
        (
            "fractional_multiplier",
            SHORT,
            "50.00",
            "50.37",
            "fractional_multiplier",
            account("10000"),
            fixed("1"),
        ),
    ]
    for name, direction, entry, stop, key, acct, policy in rows:
        yield (
            f"size/{name}",
            later(_size, direction, entry, stop, key, acct, policy),
        )


def margin_cases() -> Iterator[tuple[str, Callable[[], Any]]]:
    rows: list[tuple[str, str, AccountState, str, int, RiskPolicy, str | None]] = [
        ("healthy_with_risk", "verified", account("100000"), "120.00", 3, fixed("1000"), "600"),
        ("high_utilisation", "verified", account("20000"), "120.00", 3, fixed("1000"), None),
        ("excessive_leverage", "spec_section_42", account("2500"), "105", 2, fixed("75"), None),
        ("risk_limit_exceeded", "verified", account("100000"), "120.00", 1, fixed("10"), "20"),
        (
            "margin_missing",
            "verified_no_margin",
            account("100000"),
            "120.00",
            3,
            fixed("1000"),
            None,
        ),
        (
            "margin_unverified",
            "margin_unverified",
            account("100000"),
            "120.00",
            3,
            fixed("1000"),
            None,
        ),
        (
            "multiplier_unverified",
            "fixture_multiplier",
            account("100000"),
            "120.00",
            3,
            fixed("1000"),
            None,
        ),
        ("deficit", "verified", account("2500", "2700"), "120.00", 1, fixed("1000"), None),
        ("zero_contracts", "verified", account("100000"), "120.00", 0, fixed("1000"), None),
        ("percentage_budget", "verified", account("100000"), "120.00", 2, percent("0.001"), "150"),
        (
            "tick_value_inconsistent",
            "tick_value_inconsistent",
            account("100000"),
            "120.00",
            1,
            fixed("1000"),
            None,
        ),
        ("inverse", "inverse", account("100000"), "120.00", 1, fixed("1000"), None),
    ]
    for name, key, acct, entry, qty, policy, risk_to_stop in rows:
        yield (
            f"margin/{name}",
            later(_margin, key, acct, entry, qty, policy, risk_to_stop),
        )


def pnl_cases() -> Iterator[tuple[str, Callable[[], Any]]]:
    complete = TradeCosts(commission=D("12.5"), fees=D("3"), slippage=D("0"))
    partial = TradeCosts(commission=D("12.5"))
    rows: list[tuple[str, str, Direction, str, str, int, TradeCosts | None]] = [
        ("long_win_no_costs", "verified", LONG, "120.00", "125.50", 3, None),
        ("long_loss_complete", "verified", LONG, "120.00", "118.25", 2, complete),
        ("short_win_partial", "verified", SHORT, "120.00", "117.75", 4, partial),
        ("short_loss_empty_costs", "verified", SHORT, "120.00", "121.00", 1, TradeCosts()),
        ("break_even", "verified", LONG, "120.00", "120.00", 5, complete),
        ("fractional_multiplier", "fractional_multiplier", SHORT, "50.00", "49.37", 7, None),
        ("unverified_multiplier", "fixture_multiplier", LONG, "120.00", "125.00", 1, None),
        ("zero_contracts", "verified", LONG, "120.00", "125.00", 0, None),
        ("neutral", "verified", NEUTRAL, "120.00", "125.00", 1, None),
        ("tick_value_inconsistent", "tick_value_inconsistent", LONG, "120.00", "125.00", 1, None),
        ("inverse", "inverse", LONG, "120.00", "125.00", 1, None),
    ]
    for name, key, direction, entry, exit_, qty, costs in rows:
        yield (
            f"pnl/{name}",
            later(_pnl, key, direction, entry, exit_, qty, costs),
        )


def whatif_cases() -> Iterator[tuple[str, Callable[[], Any]]]:
    c = CONTRACTS
    prices = (D("115"), D("118.00"), D("120.00"), D("121.75"), D("130"))
    yield (
        "whatif/long_with_risk",
        lambda: simulate_contract(
            c["verified"](), LONG, D("120.00"), 3, D("100000"), prices, D("60")
        ),
    )
    yield (
        "whatif/short_without_risk",
        lambda: simulate_contract(c["verified"](), SHORT, D("120.00"), 2, D("100000"), prices),
    )
    yield (
        "whatif/zero_equity",
        lambda: simulate_contract(c["verified"](), LONG, D("120.00"), 1, D("0"), prices, D("0")),
    )
    yield (
        "whatif/unverified",
        lambda: simulate_contract(c["unverified_all"](), LONG, D("120.00"), 1, D("1000"), prices),
    )
    yield (
        "whatif/inverse",
        lambda: simulate_contract(c["inverse"](), LONG, D("120.00"), 1, D("1000"), prices),
    )


def reward_cases() -> Iterator[tuple[str, Callable[[], Any]]]:
    rows = [
        ("long_three_to_one", LONG, "120", "118", "126"),
        ("short_two_to_one", SHORT, "120", "121.5", "117"),
        ("long_target_wrong_side", LONG, "120", "118", "119"),
        ("short_stop_wrong_side", SHORT, "120", "119", "110"),
        ("neutral", NEUTRAL, "120", "118", "126"),
    ]
    for name, direction, entry, stop, target in rows:
        yield (
            f"reward/{name}",
            later(_reward, direction, entry, stop, target),
        )


def contract_cases() -> Iterator[tuple[str, Callable[[], Any]]]:
    for key, build in CONTRACTS.items():
        yield f"issues/{key}", later(_issues, build)

    for key in ("verified", "unverified_all", "tick_value_consistent"):
        yield f"implied_tick_value/{key}", later(_implied, key)
    yield "implied_tick_value/inverse", lambda: implied_tick_value(CONTRACTS["inverse"]())

    for price, tick in (("120.00", "0.25"), ("120.10", "0.25"), ("105.037", "0.05"), ("1", "0")):
        yield (
            f"tick_grid/{price}@{tick}",
            later(_grid, price, tick),
        )

    expiring = ContractExpiry(expiry_date=fact(date(2026, 6, 30)))
    unverified_expiry = ContractExpiry(
        expiry_date=fact(date(2026, 6, 30), VerificationStatus.UNVERIFIED)
    )
    timed = ContractExpiry(
        expiry_date=fact(date(2026, 6, 30)),
        last_trading_time=fact(datetime(2026, 6, 30, 15, 0, tzinfo=UTC)),
    )
    moments = {
        "before": datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
        "on_day": datetime(2026, 6, 30, 12, 0, tzinfo=UTC),
        "after_close_on_day": datetime(2026, 6, 30, 16, 0, tzinfo=UTC),
        "after": datetime(2026, 7, 2, 12, 0, tzinfo=UTC),
    }
    for label, expiry in (
        ("dated", expiring),
        ("unverified", unverified_expiry),
        ("timed", timed),
        ("none", None),
    ):
        for moment_name, moment in moments.items():
            yield (
                f"state/{label}/{moment_name}",
                later(_state, expiry, moment),
            )


def domain_cases() -> Iterator[tuple[str, Callable[[], Any]]]:
    yield from sizing_cases()
    yield from margin_cases()
    yield from pnl_cases()
    yield from whatif_cases()
    yield from reward_cases()
    yield from contract_cases()


# ----------------------------------------------------------------------
# Phase 8 API cases
# ----------------------------------------------------------------------


def _strip(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            key: _strip(value)
            for key, value in node.items()
            if key not in PHASE_8_5_ADDITIVE_KEYS and key != "generated_at"
        }
    if isinstance(node, list):
        return [_strip(item) for item in node]
    return node


def response_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        _strip(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def response_extract(payload: dict[str, Any]) -> dict[str, Any]:
    """The risk-relevant blocks, readable, for a failure message."""
    stripped = _strip(payload)
    return {
        "status_code_ok": True,
        "risk": stripped.get("risk"),
        "suitability": stripped.get("suitability"),
        "missing": stripped.get("missing"),
        "synthesis_status": (stripped.get("synthesis") or {}).get("status"),
        "analysis_id": (stripped.get("identity") or {}).get("analysis_id"),
        "position_size_why": [
            item for item in stripped.get("why", []) if item.get("topic") == "POSITION_SIZE"
        ],
    }


def api_cases() -> Iterator[tuple[str, dict[str, Any], Callable[[], FuturesContract] | None]]:
    """(name, request body, contract the stub provider returns)."""
    from tests.unit.analysis_api.test_analysis_api import body

    def sized(
        entry: str, stop: str, *, risk: str = "1000", equity: str = "100000", **extra: Any
    ) -> dict[str, Any]:
        return body(
            symbol=SYMBOL,
            account={"equity": equity, "used_margin": "0", **extra.pop("account_extra", {})},
            risk={"mode": "FIXED", "fixed_risk": risk, **extra.pop("risk_extra", {})},
            entry_price=entry,
            stop_price=stop,
            **extra,
        )

    yield "api/no_contract_no_risk", body(symbol=SYMBOL), None
    yield "api/no_contract_with_risk", sized("120.00", "118.00"), None
    yield "api/verified_allowed", sized("120.00", "118.00"), CONTRACTS["verified"]
    yield (
        "api/verified_allowed_with_currency_and_cap",
        sized(
            "120.00", "118.00", account_extra={"currency": "TRY"}, risk_extra={"max_contracts": 4}
        ),
        CONTRACTS["verified"],
    )
    yield "api/verified_short", sized("118.00", "120.00"), CONTRACTS["verified"]
    yield "api/verified_margin_missing", sized("120.00", "118.00"), CONTRACTS["verified_no_margin"]
    yield "api/unverified_contract", sized("120.00", "118.00"), CONTRACTS["unverified_all"]
    yield "api/tick_unverified", sized("120.00", "118.00"), CONTRACTS["tick_unverified"]
    yield "api/off_grid", sized("120.10", "118.00"), CONTRACTS["verified"]
    yield (
        "api/section_42_not_permitted",
        sized("105", "104", risk="75", equity="2500"),
        CONTRACTS["spec_section_42"],
    )


def run_api_cases() -> dict[str, Any]:
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from app.api.routes.analysis import get_contract_metadata
    from app.core.config import Settings
    from app.main import create_app
    from tests.unit.analysis_api.test_contract_verification import StubContracts

    settings = Settings(
        app_env="test",
        app_version="0.0.0-test",
        postgres_host="localhost",
        postgres_port=5432,
        postgres_user="viop",
        postgres_password=SecretStr("fixture-password"),  # TEST_FIXTURE value
        postgres_db="viop_test",
    )
    results: dict[str, Any] = {}
    for name, request, build in api_cases():
        app = create_app(settings)
        provider = StubContracts(build() if build is not None else None)
        app.dependency_overrides[get_contract_metadata] = lambda p=provider: p
        with TestClient(app) as client:
            response = client.post("/api/analysis", json=request)
        payload = response.json()
        results[name] = {
            "status": response.status_code,
            "digest": response_digest(payload),
            "extract": response_extract(payload),
        }
    return results


def run_domain_cases() -> dict[str, Any]:
    return {name: outcome(call) for name, call in domain_cases()}


def capture() -> dict[str, Any]:
    return {
        "captured_from": "Phase 8 code at commit 1c3d555, before any Phase 8.5 change",
        "domain": run_domain_cases(),
        "api": run_api_cases(),
    }


if __name__ == "__main__":
    if "--write" not in sys.argv:
        raise SystemExit("refusing to run without --write; the baseline is recorded once")
    if GOLDEN_PATH.exists() and "--force" not in sys.argv:
        raise SystemExit(f"{GOLDEN_PATH.name} already exists; a golden file is never regenerated")
    GOLDEN_PATH.write_text(
        json.dumps(capture(), indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {GOLDEN_PATH}")

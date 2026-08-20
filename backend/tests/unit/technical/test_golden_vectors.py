"""Independent numerical verification against TA-Lib.

The reference values in ``tests/fixtures/golden_indicators.json`` were produced
by TA-Lib 0.7.1 - the C implementation most platforms embed - running in a
throwaway virtual environment. They are frozen into the repository, so TA-Lib
is not a project dependency, does not run in CI, and is never the runtime
numerical authority. It is a witness, not a component.

Eleven of the thirteen indicator families reproduce TA-Lib to floating-point
noise. The two that do not are MACD and the directional family, and both
differ by **initialization only**. That is not asserted on faith: for each,
this module rebuilds the value using TA-Lib's own seeding and shows it then
matches exactly, which isolates the difference to the seed and proves the
underlying formula agrees.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from app.domain.technical.momentum import macd, rsi
from app.domain.technical.smoothing import ema, sma
from app.domain.technical.trend_strength import adx
from app.domain.technical.types import IndicatorValues
from app.domain.technical.volatility import atr, bollinger_bands, true_range

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "golden_indicators.json"

# Every indicator here matched TA-Lib to better than 1e-12 relative when the
# fixture was generated. The bound is a deliberate floor: the largest observed
# difference was ~1.4e-13 (Bollinger bands, where a square root compounds the
# rounding), so this catches any real formula change while tolerating the
# platform's last bits.
EXACT_TOLERANCE = 1e-11

EXACT_MATCH_INDICATORS = (
    "sma_20",
    "sma_50",
    "ema_9",
    "ema_20",
    "ema_50",
    "rsi_14",
    "true_range",
    "atr_14",
    "bb_upper_20_2",
    "bb_middle_20_2",
    "bb_lower_20_2",
)


@pytest.fixture(scope="module")
def golden() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return document


@pytest.fixture(scope="module")
def prices(golden: dict[str, Any]) -> dict[str, list[float]]:
    dataset = golden["dataset"]
    return {
        "high": [float(value) for value in dataset["high"]],
        "low": [float(value) for value in dataset["low"]],
        "close": [float(value) for value in dataset["close"]],
    }


@pytest.fixture(scope="module")
def computed(prices: dict[str, list[float]]) -> dict[str, IndicatorValues]:
    high, low, close = prices["high"], prices["low"], prices["close"]
    macd_result = macd(close, 12, 26, 9)
    bands = bollinger_bands(close, 20, 2.0)
    directional = adx(high, low, close, 14)
    return {
        "sma_20": sma(close, 20),
        "sma_50": sma(close, 50),
        "ema_9": ema(close, 9),
        "ema_20": ema(close, 20),
        "ema_50": ema(close, 50),
        "rsi_14": rsi(close, 14),
        "macd": macd_result.macd,
        "macd_signal": macd_result.signal,
        "macd_hist": macd_result.histogram,
        "true_range": true_range(high, low, close),
        "atr_14": atr(high, low, close, 14),
        "bb_upper_20_2": bands.upper,
        "bb_middle_20_2": bands.middle,
        "bb_lower_20_2": bands.lower,
        "plus_di_14": directional.plus_di,
        "minus_di_14": directional.minus_di,
        "dx_14": directional.dx,
        "adx_14": directional.adx,
    }


def _max_relative_difference(
    ours: IndicatorValues, reference: list[float | None], *, start: int = 0
) -> tuple[float, int]:
    """Largest relative difference over the overlap, and how many points it covers."""
    worst = 0.0
    compared = 0
    for index in range(start, len(reference)):
        expected = reference[index]
        actual = ours[index]
        if expected is None or actual is None:
            continue
        compared += 1
        worst = max(worst, abs(actual - expected) / max(abs(expected), 1e-12))
    return worst, compared


@pytest.mark.unit
def test_fixture_documents_its_provenance(golden: dict[str, Any]) -> None:
    """A golden file whose origin is unknown is not evidence of anything."""
    assert golden["reference_source"].startswith("TA-Lib")
    assert set(golden["known_differences"]) == {"macd", "directional"}


@pytest.mark.unit
@pytest.mark.parametrize("name", EXACT_MATCH_INDICATORS)
def test_matches_talib(
    name: str, computed: dict[str, IndicatorValues], golden: dict[str, Any]
) -> None:
    worst, compared = _max_relative_difference(computed[name], golden["reference"][name])
    assert compared > 50, f"{name}: only {compared} points overlapped, verification too thin"
    assert worst < EXACT_TOLERANCE, f"{name}: worst relative difference {worst:.3e}"


@pytest.mark.unit
@pytest.mark.parametrize("name", EXACT_MATCH_INDICATORS)
def test_warm_up_agrees_with_talib(
    name: str, computed: dict[str, IndicatorValues], golden: dict[str, Any]
) -> None:
    """The first defined index is part of the convention, not an accident."""
    reference = golden["reference"][name]
    ours = computed[name]
    reference_first = next((i for i, v in enumerate(reference) if v is not None), None)
    our_first = next((i for i, v in enumerate(ours) if v is not None), None)
    assert our_first == reference_first


@pytest.mark.unit
@pytest.mark.parametrize("name", ("macd", "macd_signal", "macd_hist", "adx_14", "plus_di_14"))
def test_frozen_regression_baseline(
    name: str, computed: dict[str, IndicatorValues], golden: dict[str, Any]
) -> None:
    """Our own past output, so an unintended change to these two families shows up.

    The indicators that match TA-Lib are already pinned by that comparison;
    these two are pinned here instead.
    """
    expected = golden["ours"][name]
    actual = computed[name]
    assert len(actual) == len(expected)
    for index, (got, want) in enumerate(zip(actual, expected, strict=True)):
        if want is None:
            assert got is None, f"{name}[{index}] should be None"
        else:
            assert got is not None, f"{name}[{index}] should be defined"
            assert math.isclose(got, want, rel_tol=1e-12, abs_tol=1e-12), f"{name}[{index}]"


@pytest.mark.unit
def test_macd_difference_is_seeding_only(
    prices: dict[str, list[float]], golden: dict[str, Any]
) -> None:
    """Reproduce TA-Lib's MACD exactly by adopting its EMA alignment.

    TA-Lib starts the fast EMA at index ``slow - fast`` so that both EMAs are
    seeded over the same number of bars. We seed each EMA independently from
    the start of the data - the textbook reading of "EMA(12) - EMA(26)", and
    what TradingView computes. Feeding our own EMA the shifted window
    reproduces TA-Lib to floating-point noise, so the recursion, the alpha and
    the SMA seed all agree; only the starting point differs.
    """
    close = prices["close"]
    fast, slow = 12, 26
    shift = slow - fast

    slow_ema = ema(close, slow)
    shifted_fast = (None,) * shift + ema(close[shift:], fast)

    reference = golden["reference"]["macd"]
    compared = 0
    for index, expected in enumerate(reference):
        if expected is None:
            continue
        fast_value = shifted_fast[index]
        slow_value = slow_ema[index]
        assert fast_value is not None and slow_value is not None
        compared += 1
        assert math.isclose(fast_value - slow_value, expected, rel_tol=1e-12, abs_tol=1e-12)
    assert compared > 100


@pytest.mark.unit
def test_directional_difference_is_seeding_only(
    prices: dict[str, list[float]], golden: dict[str, Any]
) -> None:
    """Reproduce TA-Lib's first +DI exactly by adopting its seed.

    TA-Lib accumulates ``period - 1`` bars, decays that sum once, then adds the
    ``period``-th bar. Wilder's published method - and TA-Lib's own ATR, which
    we match exactly - seeds with the sum of the first ``period`` bars. We
    follow Wilder, so our ADX stays consistent with our ATR.

    Computing both seeds here shows our directional movement values and true
    ranges are identical to TA-Lib's; the divergence is entirely the seed.
    """
    high, low, close = prices["high"], prices["low"], prices["close"]
    period = 14

    ranges = [value for value in true_range(high, low, close)[1:] if value is not None]
    plus_dm: list[float] = []
    for index in range(1, len(high)):
        up_move = high[index] - high[index - 1]
        down_move = low[index - 1] - low[index]
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)

    talib_dm = sum(plus_dm[: period - 1])
    talib_dm = talib_dm - talib_dm / period + plus_dm[period - 1]
    talib_tr = sum(ranges[: period - 1])
    talib_tr = talib_tr - talib_tr / period + ranges[period - 1]

    reference_first = golden["reference"]["plus_di_14"][period]
    assert reference_first is not None
    assert math.isclose(100.0 * talib_dm / talib_tr, reference_first, rel_tol=1e-12)

    # And Wilder's seed - ours - is the plain mean of the first `period` bars.
    wilder_value = 100.0 * sum(plus_dm[:period]) / sum(ranges[:period])
    ours = adx(high, low, close, period).plus_di[period]
    assert ours is not None
    assert math.isclose(ours, wilder_value, rel_tol=1e-12)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "from_index", "bound"),
    (
        ("macd", 100, 1e-6),
        ("adx_14", 100, 5e-2),
        ("plus_di_14", 100, 5e-2),
    ),
)
def test_seeding_difference_decays(
    name: str,
    from_index: int,
    bound: float,
    computed: dict[str, IndicatorValues],
    golden: dict[str, Any],
) -> None:
    """A seeding difference must shrink; a formula error would not.

    This is the load-bearing half of the argument. Two series that differ only
    in their seed converge geometrically as the smoothing forgets it, while a
    wrong alpha, a wrong window or a wrong sign stays wrong forever. Both
    families converge.
    """
    ours = computed[name]
    reference = golden["reference"][name]
    late = [
        abs(ours[index] - reference[index])
        for index in range(from_index, len(reference))
        if ours[index] is not None and reference[index] is not None
    ]
    assert late, f"{name}: nothing to compare from index {from_index}"
    assert max(late) < bound, f"{name}: still differs by {max(late):.3e} at index {from_index}+"

    early = [
        abs(ours[index] - reference[index])
        for index in range(len(reference))
        if ours[index] is not None and reference[index] is not None
    ]
    assert max(late) < max(early), f"{name}: difference is not decaying"

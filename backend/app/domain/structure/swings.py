"""Swing (pivot) detection — the foundation every other Phase 2 engine sits on.

A swing high is a candle whose high stands above its neighbours; a swing low is
its mirror. That much is uncontroversial. The part that decides whether the
whole phase is honest is *when the swing becomes knowable*.

A pivot at index ``i`` needs ``right`` candles after it before anyone can say
it was a pivot. Until candle ``i + right`` closes, the market has not yet shown
that the high held. So a ``SwingPoint`` carries **two** positions:

``pivot_index`` / ``pivot_time``
    where the extreme actually is.

``confirmed_index`` / ``confirmed_time``
    the first moment the pivot could have been recognised in real time.

Every downstream engine - structure labels, BOS, CHOCH, zones, breakouts,
retests, divergence, regime - consumes swings filtered by ``confirmed_index``,
never by ``pivot_index``. Using the pivot index to decide what was known is the
single most common way a backtest quietly becomes fiction: the swing at candle
100 appears to have been tradeable at candle 100, when in truth nobody could
have seen it before candle 102.

``swings_known_at`` is the only sanctioned way to ask "what did we know then".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.market.series import ValidatedCandleSeries


@unique
class SwingType(StrEnum):
    """Which extreme a pivot marks."""

    HIGH = "HIGH"
    LOW = "LOW"


@dataclass(frozen=True, slots=True)
class SwingPoint:
    """One confirmed pivot, with both of its timestamps.

    ``price`` is a ``Decimal`` taken directly from the candle - an observed
    exchange price, not a derived estimate - so it stays exact for the level
    arithmetic that Phase 3 risk work will need.
    """

    swing_type: SwingType
    pivot_index: int
    pivot_time: datetime
    price: Decimal
    confirmed_index: int
    confirmed_time: datetime

    @property
    def confirmation_lag(self) -> int:
        """Candles between the pivot and the moment it could be recognised."""
        return self.confirmed_index - self.pivot_index

    def known_at(self, index: int) -> bool:
        """True when this swing was recognisable at ``index``."""
        return self.confirmed_index <= index


@dataclass(frozen=True, slots=True)
class SwingConfig:
    """Deterministic pivot rule.

    ``left`` and ``right`` are the confirmation windows. ``right`` is also the
    confirmation lag: a larger value produces fewer, more significant pivots
    and recognises each of them later. That trade-off is explicit rather than
    hidden inside a magic number.

    ``use_wicks`` selects the pivot price. With wicks (the default) a swing
    high is the highest *high*, which is where stops actually sit. Set it
    False to pivot on closes, which ignores intrabar spikes.
    """

    left: int = 2
    right: int = 2
    use_wicks: bool = True

    def __post_init__(self) -> None:
        if self.left < 1:
            raise ValueError(f"left must be >= 1, got {self.left}")
        if self.right < 1:
            raise ValueError(f"right must be >= 1, got {self.right}")

    @property
    def minimum_candles(self) -> int:
        """Shortest series that can contain a single pivot."""
        return self.left + self.right + 1


def detect_swings(
    series: ValidatedCandleSeries,
    config: SwingConfig | None = None,
) -> tuple[SwingPoint, ...]:
    """Find every pivot the series can confirm, in pivot order.

    **The rule, stated exactly.** Candle ``i`` is a swing high when:

    * ``i >= left`` and ``i + right <= last index`` - both windows fit;
    * ``high[i] > high[j]`` for every ``j`` in ``[i-left, i-1]`` - *strictly*
      above everything on the left;
    * ``high[i] >= high[j]`` for every ``j`` in ``[i+1, i+right]`` - *at least*
      everything on the right.

    Swing lows mirror it with ``<`` and ``<=``.

    **Equal highs, and why the sides are asymmetric.** The strict-left,
    non-strict-right pairing is the tie-break. On a plateau of equal highs, the
    *earliest* bar qualifies (its left neighbour is genuinely lower and its
    right neighbours merely equal) while every later bar of the same plateau is
    rejected, because its left neighbour is equal rather than lower. One
    plateau therefore yields exactly one pivot, at the first bar that reached
    the level - which is also the first moment the level existed.

    The symmetric alternative, strict on both sides, silently drops equal-high
    structures entirely: a textbook double top would produce no pivots at all.
    Being non-strict on both sides is worse still, marking every bar of a flat
    stretch as a pivot. Neither is acceptable, so the asymmetry is deliberate
    and is pinned by ``test_equal_highs_yield_one_pivot_at_the_first_bar``.

    A perfectly flat market produces no swings at all: no bar is strictly above
    its left neighbour. That is the honest answer - a flat tape has no
    structure - rather than an arbitrary pick.

    **Insufficient history.** A series shorter than ``left + right + 1``
    returns an empty tuple. Nothing is inferred from a partial window.

    A candle can be both a swing high and a swing low (an inside-bar plateau in
    one direction and an extreme in the other); both are emitted, in that
    order, and downstream engines treat them independently.
    """
    settings = config if config is not None else SwingConfig()
    total = len(series)
    if total < settings.minimum_candles:
        return ()

    highs = series.highs if settings.use_wicks else series.closes
    lows = series.lows if settings.use_wicks else series.closes
    times = series.open_times

    found: list[SwingPoint] = []
    for index in range(settings.left, total - settings.right):
        confirmed_index = index + settings.right
        if _is_pivot_high(highs, index, settings):
            found.append(
                SwingPoint(
                    swing_type=SwingType.HIGH,
                    pivot_index=index,
                    pivot_time=times[index],
                    price=highs[index],
                    confirmed_index=confirmed_index,
                    confirmed_time=times[confirmed_index],
                )
            )
        if _is_pivot_low(lows, index, settings):
            found.append(
                SwingPoint(
                    swing_type=SwingType.LOW,
                    pivot_index=index,
                    pivot_time=times[index],
                    price=lows[index],
                    confirmed_index=confirmed_index,
                    confirmed_time=times[confirmed_index],
                )
            )
    return tuple(found)


def _is_pivot_high(values: Sequence[Decimal], index: int, config: SwingConfig) -> bool:
    """Strictly above everything left, at least everything right. See ``detect_swings``."""
    candidate = values[index]
    strictly_above_left = all(
        candidate > values[index - offset] for offset in range(1, config.left + 1)
    )
    at_least_right = all(
        candidate >= values[index + offset] for offset in range(1, config.right + 1)
    )
    return strictly_above_left and at_least_right


def _is_pivot_low(values: Sequence[Decimal], index: int, config: SwingConfig) -> bool:
    """Mirror of ``_is_pivot_high``."""
    candidate = values[index]
    strictly_below_left = all(
        candidate < values[index - offset] for offset in range(1, config.left + 1)
    )
    at_most_right = all(
        candidate <= values[index + offset] for offset in range(1, config.right + 1)
    )
    return strictly_below_left and at_most_right


def swings_known_at(swings: Sequence[SwingPoint], index: int) -> tuple[SwingPoint, ...]:
    """The swings recognisable at ``index`` - the only honest "what did we know".

    Filtering by ``confirmed_index``, never by ``pivot_index``. Every engine in
    this package walks the candles forward and asks this question at each step,
    which is what makes the whole phase reproducible in live trading.
    """
    return tuple(swing for swing in swings if swing.confirmed_index <= index)


def last_swing(swings: Sequence[SwingPoint], swing_type: SwingType) -> SwingPoint | None:
    """Most recent swing of a type, by pivot position."""
    latest: SwingPoint | None = None
    for swing in swings:
        if swing.swing_type is swing_type and (
            latest is None or swing.pivot_index > latest.pivot_index
        ):
            latest = swing
    return latest

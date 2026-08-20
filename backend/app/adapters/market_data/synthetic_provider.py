"""Deterministic synthetic market data provider (master spec section 73).

The ``MockMarketDataProvider`` of section 73, implemented so that tests,
demonstrations and replay-parity checks have a candle source that needs no
file, no network and no external API.

Three properties make it useful rather than merely convenient:

*Deterministic by construction.* Prices come from a closed-form arithmetic
function of the candle index, not from ``random``. There is no seeded
generator whose stream could change between Python releases, no global state,
and no dependence on call order - candle 500 is the same number whether you
ask for one candle or a thousand.

The waveform is an exact triangle computed in ``Decimal`` rather than a sine.
``math.sin`` is free to differ by an ulp between platforms and libm versions,
which would make "deterministic provider" true only on one machine - and this
project has already paid for one Windows-versus-Linux discrepancy that local
tests could not see.

*Honest about what it is.* The generated data is ``MOCK_DATA`` under master
spec section 118. The default symbol is visibly synthetic, and nothing here
encodes a contract multiplier, tick size, margin, session hour or expiry.
No real Borsa Istanbul or VIOP fact is invented.

*Able to produce bad data on purpose.* ``defects`` makes it emit gaps,
duplicates, out-of-order candles or a forming final bar, so the Data Quality
Engine can be tested against a provider rather than only against
hand-assembled fixtures.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle

PRICE_EXPONENT = Decimal("0.01")
"""Generated prices are quantized to two decimal places.

A synthetic convenience for readable fixtures, **not** a tick size. The real
tick size is a section 118 exchange fact and arrives with
``ContractMetadataProvider`` in Phase 3.
"""


@unique
class SyntheticDefect(StrEnum):
    """A flaw the generator can inject on purpose, to exercise validation."""

    GAP = "GAP"
    """Omit one candle in the middle of the series."""

    DUPLICATE = "DUPLICATE"
    """Repeat one candle, timestamp and all."""

    OUT_OF_ORDER = "OUT_OF_ORDER"
    """Swap two adjacent candles."""

    FORMING_LAST = "FORMING_LAST"
    """Leave the final candle unclosed."""

    ZERO_VOLUME = "ZERO_VOLUME"
    """Give one candle no volume."""


@dataclass(frozen=True, slots=True)
class SyntheticMarketProfile:
    """Shape of the generated series.

    ``base_price`` and ``amplitude`` are arbitrary synthetic units. They are
    not quoted in Turkish lira and describe no real instrument.
    """

    base_price: Decimal = Decimal("100")
    trend_per_candle: Decimal = Decimal("0.05")
    amplitude: Decimal = Decimal("2.5")
    cycle_candles: int = 24
    base_volume: Decimal = Decimal("1000")
    volume_amplitude: Decimal = Decimal("400")


class SyntheticHistoricalMarketDataProvider:
    """Generates reproducible candles for a synthetic instrument.

    Implements ``HistoricalMarketDataProvider``. Candle ``n`` is measured from
    ``origin + n * interval``, so a window request returns exactly the candles
    a longer request would have contained at those same timestamps - the
    property a replay harness depends on.
    """

    def __init__(
        self,
        *,
        origin: datetime,
        symbol: str = "SYNTH-MOCK",
        profile: SyntheticMarketProfile | None = None,
        defects: frozenset[SyntheticDefect] = frozenset(),
    ) -> None:
        if origin.tzinfo is None:
            raise ValueError("origin must be timezone-aware")
        self._origin = origin
        self._symbol = symbol
        self._profile = profile if profile is not None else SyntheticMarketProfile()
        self._defects = defects

    @property
    def symbol(self) -> str:
        return self._symbol

    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Sequence[Candle]:
        """Return generated candles with ``start <= open_time < end``."""
        interval = timedelta(minutes=timeframe.minutes)
        if end <= start:
            return ()

        first = max(0, math.ceil((start - self._origin) / interval))
        last = math.ceil((end - self._origin) / interval)
        if last <= first:
            return ()
        return self.generate(symbol, timeframe, count=last - first, offset=first)

    def generate(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        count: int,
        offset: int = 0,
    ) -> tuple[Candle, ...]:
        """Generate ``count`` candles starting at index ``offset``."""
        if count < 0:
            raise ValueError(f"count must be non-negative, got {count}")
        interval = timedelta(minutes=timeframe.minutes)
        candles = [
            self._candle(symbol, timeframe, index=offset + position, interval=interval)
            for position in range(count)
        ]
        return self._apply_defects(tuple(candles))

    def _candle(
        self, symbol: str, timeframe: Timeframe, *, index: int, interval: timedelta
    ) -> Candle:
        profile = self._profile
        cycle = profile.cycle_candles

        drift = profile.trend_per_candle * index
        open_price = _quantize(
            profile.base_price + drift + profile.amplitude * _triangle(index, cycle)
        )

        # A phase-shifted wave gives the close a different shape from the open,
        # so a bar is never a doji by construction.
        close_price = _quantize(
            profile.base_price
            + drift
            + profile.amplitude * _triangle(index, cycle, shift=cycle // 3)
        )

        spread = _quantize(profile.amplitude / 4)
        high = _quantize(max(open_price, close_price) + spread)
        low = _quantize(min(open_price, close_price) - spread)

        volume_wave = profile.volume_amplitude * _triangle(index, cycle, shift=cycle // 2)
        volume = _quantize(max(profile.base_volume + volume_wave, Decimal("1")))

        return Candle(
            symbol=symbol,
            timeframe=timeframe,
            open_time=self._origin + index * interval,
            open=open_price,
            high=high,
            low=low,
            close=close_price,
            volume=volume,
            is_closed=True,
        )

    def _apply_defects(self, candles: tuple[Candle, ...]) -> tuple[Candle, ...]:
        if not self._defects or not candles:
            return candles

        working = list(candles)
        middle = len(working) // 2

        if SyntheticDefect.GAP in self._defects and len(working) > 2:
            del working[middle]
        if SyntheticDefect.DUPLICATE in self._defects and len(working) > 1:
            working.insert(middle, working[middle])
        if SyntheticDefect.OUT_OF_ORDER in self._defects and len(working) > 2:
            working[middle], working[middle - 1] = working[middle - 1], working[middle]
        if SyntheticDefect.ZERO_VOLUME in self._defects:
            working[middle] = _replace_volume(working[middle], Decimal(0))
        if SyntheticDefect.FORMING_LAST in self._defects:
            working[-1] = _as_forming(working[-1])

        return tuple(working)


def _triangle(index: int, cycle: int, shift: int = 0) -> Decimal:
    """An exact triangle wave over ``[-1, 1]``, period ``cycle``.

    Built from integer arithmetic and a single ``Decimal`` division that is
    immediately quantized, so the result does not depend on the ambient
    ``decimal`` context precision, on the platform, or on the libm in use.
    """
    if cycle < 2:
        raise ValueError(f"cycle_candles must be >= 2, got {cycle}")
    position = (index + shift) % cycle
    numerator = 4 * position - cycle if 2 * position < cycle else 3 * cycle - 4 * position
    return (Decimal(numerator) / Decimal(cycle)).quantize(Decimal("0.000001"))


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(PRICE_EXPONENT)


def _replace_volume(candle: Candle, volume: Decimal) -> Candle:
    return Candle(
        symbol=candle.symbol,
        timeframe=candle.timeframe,
        open_time=candle.open_time,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=volume,
        is_closed=candle.is_closed,
        open_interest=candle.open_interest,
    )


def _as_forming(candle: Candle) -> Candle:
    return Candle(
        symbol=candle.symbol,
        timeframe=candle.timeframe,
        open_time=candle.open_time,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
        is_closed=False,
        open_interest=candle.open_interest,
    )

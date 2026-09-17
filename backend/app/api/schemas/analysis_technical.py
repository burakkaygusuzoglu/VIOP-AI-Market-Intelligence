"""The technical panel and chart overlays (§10, §11).

Phase 8 surfaced one indicator — RSI — as a headline fact per timeframe, while
Phase 1 had already computed EMA and SMA families, MACD, ATR, true range,
Bollinger bands, the full directional-movement set, VWAP and three volume
measures. The engine was not the limitation; the projection was.

## Selection, not computation

Every value here is read out of a finished `TechnicalSnapshot`. Taking the last
element of an aligned series is selection; nothing is averaged, smoothed,
combined or re-derived, and there is no arithmetic in this module. A reading
whose warm-up has not elapsed is `None` in the series and is reported
**unavailable with the reason**, never as zero — a zero ATR would read as "no
volatility measured" rather than "not enough candles yet".

## Not every number, and the difference is stated

Phase 1 produces far more than a person can read. The panel therefore names a
`BEGINNER` subset and a `PRO` set, and the distinction is a property of each
reading rather than two separate payloads: Pro sees more of the same list, not
a different list. Deciding what to show is presentation; deciding what a number
*is* remains the engine's.

## Overlays (§11)

The chart draws the EMA family, because Phase 1 already produces a per-bar EMA
series aligned candle-for-candle with the candles the chart draws. That makes
an overlay a projection of authoritative values, not a reconstruction. It is
sliced to exactly the same display window as the candles, so a point can never
appear beside a bar that is not there.

Nothing else is overlaid. MACD, ADX and the volume measures live on their own
scales and would need a second axis, which is a chart feature rather than a
data question; Bollinger bands would fit, and are left out to keep the overlay
set to the one family a reader can hold in their head. The limitation is real
and stated rather than worked around.
"""

from __future__ import annotations

from app.api.schemas.analysis import (
    ChartOverlayResponse,
    TechnicalPanelResponse,
    TechnicalReadingResponse,
)
from app.application.analysis.orchestrator import TimeframeOutcome
from app.domain.technical.engine import TechnicalSnapshot
from app.domain.technical.types import IndicatorValues

BEGINNER_KEYS = ("rsi", "atr", "adx", "relative_volume")
"""What a beginner is shown.

Four readings that answer different questions - momentum, volatility, trend
strength, participation - rather than four views of the same one. Everything
else stays available in Pro.
"""


def _latest(values: IndicatorValues) -> float | None:
    """The most recent computable value, or ``None``.

    The last element specifically, not the last non-``None`` one. A series
    whose tail is ``None`` has not produced a current reading, and reaching
    backwards for an older one would present a stale number as the present.
    """
    if not values:
        return None
    return values[-1]


def _reading(
    key: str,
    label: str,
    values: IndicatorValues,
    unit: str,
    *,
    warmup_note: str,
) -> TechnicalReadingResponse:
    latest = _latest(values)
    if latest is None:
        return TechnicalReadingResponse(
            key=key,
            label=label,
            unit=unit,
            available=False,
            unavailable_reason=warmup_note,
            beginner=key in BEGINNER_KEYS,
        )
    return TechnicalReadingResponse(
        key=key,
        label=label,
        # `repr` keeps the exact float the engine produced; the client rounds
        # for display and keeps this for the audit view.
        raw=repr(latest),
        unit=unit,
        available=True,
        beginner=key in BEGINNER_KEYS,
    )


def build_panel(item: TimeframeOutcome) -> TechnicalPanelResponse | None:
    """Every Phase 1 reading this timeframe actually produced."""
    snapshot = item.technicals
    if snapshot is None or not item.usable:
        return None

    warmup = "Yeterli mum yok; gösterge henüz hesaplanamıyor."
    readings: list[TechnicalReadingResponse] = [
        _reading(
            "rsi",
            f"RSI ({snapshot.config.rsi_period})",
            snapshot.rsi,
            "indicator",
            warmup_note=warmup,
        ),
        _reading(
            "atr", f"ATR ({snapshot.config.atr_period})", snapshot.atr, "price", warmup_note=warmup
        ),
        _reading(
            "adx",
            f"ADX ({snapshot.config.adx_period})",
            snapshot.adx,
            "indicator",
            warmup_note=warmup,
        ),
        _reading("plus_di", "+DI", snapshot.directional.plus_di, "indicator", warmup_note=warmup),
        _reading("minus_di", "-DI", snapshot.directional.minus_di, "indicator", warmup_note=warmup),
        _reading("macd", "MACD", snapshot.macd.macd, "indicator", warmup_note=warmup),
        _reading(
            "macd_signal", "MACD sinyal", snapshot.macd.signal, "indicator", warmup_note=warmup
        ),
        _reading(
            "macd_histogram",
            "MACD histogram",
            snapshot.macd.histogram,
            "indicator",
            warmup_note=warmup,
        ),
        _reading(
            "bollinger_upper",
            "Bollinger üst",
            snapshot.bollinger.upper,
            "price",
            warmup_note=warmup,
        ),
        _reading(
            "bollinger_middle",
            "Bollinger orta",
            snapshot.bollinger.middle,
            "price",
            warmup_note=warmup,
        ),
        _reading(
            "bollinger_lower",
            "Bollinger alt",
            snapshot.bollinger.lower,
            "price",
            warmup_note=warmup,
        ),
        _reading("vwap", "VWAP", snapshot.vwap, "price", warmup_note=warmup),
        _reading("true_range", "Gerçek aralık", snapshot.true_range, "price", warmup_note=warmup),
        _reading(
            "volume_ma", "Hacim ortalaması", snapshot.volume_ma, "indicator", warmup_note=warmup
        ),
        _reading(
            "relative_volume", "Göreli hacim", snapshot.relative_volume, "ratio", warmup_note=warmup
        ),
        _reading(
            "volume_acceleration",
            "Hacim ivmesi",
            snapshot.volume_acceleration,
            "ratio",
            warmup_note=warmup,
        ),
    ]

    for period in sorted(snapshot.ema):
        readings.append(
            _reading(
                f"ema_{period}", f"EMA {period}", snapshot.ema[period], "price", warmup_note=warmup
            )
        )
    for period in sorted(snapshot.sma):
        readings.append(
            _reading(
                f"sma_{period}", f"SMA {period}", snapshot.sma[period], "price", warmup_note=warmup
            )
        )

    return TechnicalPanelResponse(
        timeframe=item.timeframe.value,
        role=item.role.value,
        readings=tuple(readings),
    )


def build_overlays(
    snapshot: TechnicalSnapshot | None, window: int
) -> tuple[ChartOverlayResponse, ...]:
    """The EMA family, sliced to the chart's display window.

    ``window`` is how many candles the chart received. The overlay is cut to
    exactly the same tail so every point lines up with a bar that is actually
    drawn; a longer series would put a value beside nothing.
    """
    if snapshot is None or window <= 0:
        return ()

    overlays: list[ChartOverlayResponse] = []
    for period in sorted(snapshot.ema):
        values = snapshot.ema[period][-window:]
        if not any(value is not None for value in values):
            # Entirely inside the warm-up. An overlay of nothing is not drawn,
            # and the panel already reports the reading as unavailable.
            continue
        overlays.append(
            ChartOverlayResponse(
                key=f"ema_{period}",
                label=f"EMA {period}",
                values=tuple(None if value is None else repr(value) for value in values),
            )
        )
    return tuple(overlays)

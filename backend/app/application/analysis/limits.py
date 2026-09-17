"""Bounds on untrusted analysis input (§5).

An OHLCV upload is attacker-controlled: it arrives over HTTP, it is parsed
before anything can judge it, and parsing is where a body large enough to
exhaust memory does its damage. Every bound here exists to be checked *before*
the expensive step it guards, not after.

The bounds are policy, not exchange facts, so they live in the application
layer and are configurable. They are deliberately generous enough for real
analysis — a year of daily candles is ~250 rows, a month of 5M candles is
~8 600 — and deliberately finite.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.common.enums import Timeframe


class InputTooLargeError(ValueError):
    """Input exceeded a bound. Carries no user content.

    The message names the limit and what was exceeded, never the offending
    bytes: echoing a malicious payload back into a response or a log is how a
    size check becomes a reflection vector (§5).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class InputLimits:
    """What one analysis request may contain."""

    max_csv_bytes: int = 8 * 1024 * 1024
    """Per timeframe. A 5M year is roughly 2 MB of CSV text; 8 MB leaves room
    without letting one field carry an arbitrary payload."""

    max_rows_per_timeframe: int = 2_500
    """**Measured, not guessed.**

    The first version of this file said 60 000, on the reasoning that it was
    "about seven months" of 5M candles. Profiling the real path showed why that
    was wrong: Phase 2's `analyse_structure` rebuilds zones once per historical
    step, so cost grows roughly with the square of the row count.

        rows      analysis time
         500      0.07 s
       1 000      0.28 s
       2 000      1.26 s
       4 000      6.89 s

    Extrapolated, 60 000 rows is on the order of twenty-five minutes of pure
    CPU for a ~3 MB upload - a denial of service that costs the attacker
    nothing. A byte limit does not bound compute.

    2 500 is comfortably above genuine need. A year of daily bars is 250; the
    longest indicator warm-up in Phase 1 is a small multiple of 200. Four
    timeframes at this cap analyse in a few seconds.

    The quadratic behaviour itself is recorded as technical debt: fixing it
    belongs to whichever phase owns the structure engine, not to a UI phase."""

    max_total_rows: int = 10_000
    """Exactly four timeframes at the per-timeframe cap, and no more.

    Bounded separately from the per-timeframe limit so the *sum* cannot be used
    to multiply the cost of one request - and set to the legitimate maximum
    rather than below it, so a full four-timeframe analysis at the cap is
    permitted instead of being refused by an accidental inconsistency."""

    max_symbol_length: int = 64
    """Long enough for any real instrument code, short enough that the symbol
    cannot become a payload carrier."""

    max_note_length: int = 500

    def check_csv_size(self, timeframe: Timeframe, size: int) -> None:
        if size > self.max_csv_bytes:
            raise InputTooLargeError(
                "CSV_TOO_LARGE",
                f"{timeframe.value} data exceeds the {self.max_csv_bytes} byte limit",
            )

    def check_rows(self, timeframe: Timeframe, rows: int) -> None:
        if rows > self.max_rows_per_timeframe:
            raise InputTooLargeError(
                "TOO_MANY_ROWS",
                f"{timeframe.value} has more than {self.max_rows_per_timeframe} rows",
            )

    def check_total_rows(self, rows: int) -> None:
        if rows > self.max_total_rows:
            raise InputTooLargeError(
                "TOO_MANY_ROWS_TOTAL",
                f"the request has more than {self.max_total_rows} candles in total",
            )

    def check_symbol(self, symbol: str) -> None:
        if len(symbol) > self.max_symbol_length:
            raise InputTooLargeError(
                "SYMBOL_TOO_LONG",
                f"symbol exceeds {self.max_symbol_length} characters",
            )

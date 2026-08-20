"""CSV historical market data provider (master spec section 73).

Implements ``HistoricalMarketDataProvider``. Its entire job is to turn bytes on
disk into ``Candle`` objects, faithfully and deterministically.

What it deliberately does **not** do:

* No indicator arithmetic. Calculation lives in the domain; an adapter that
  computed an average would put financial mathematics outside the layer that
  is tested and type-checked as the numerical authority.
* No repair. It does not sort, deduplicate, fill gaps, clamp negatives or
  infer a missing field. Every such decision belongs to the Data Quality
  Engine, which can *report* it; an adapter doing it silently would destroy
  the evidence that it happened.
* No validation verdict. Rows it genuinely cannot parse become
  ``DataQualityIssue`` records that travel with the candles, so an adapter
  problem lands in the same report as a domain one instead of being raised as
  an exception the caller has to remember to catch.

Prices are parsed straight from their source text into ``Decimal``. They never
pass through ``float``, which would round the input before it was ever stored.
"""

from __future__ import annotations

import asyncio
import csv
from collections.abc import Sequence
from datetime import UTC, datetime, tzinfo
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.application.ports.market_data import MarketDataFetch
from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle
from app.domain.market.quality import (
    DataQualityCode,
    DataQualityIssue,
    DataQualitySeverity,
)

REQUIRED_COLUMNS = ("open_time", "open", "high", "low", "close", "volume")
"""Minimum schema. ``open_interest`` and ``is_closed`` are optional columns."""


class CsvSchemaError(ValueError):
    """The file cannot be read as market data at all.

    Distinct from a malformed row. A missing ``close`` column is not a data
    quality finding to weigh up - there is no dataset to assess - so it is
    raised rather than reported.
    """


class CsvHistoricalMarketDataProvider:
    """Serves candles from one CSV file per symbol and timeframe.

    Expected layout, resolved as ``{root}/{symbol}/{timeframe}.csv``::

        open_time,open,high,low,close,volume[,open_interest][,is_closed]
        2026-01-02T10:00:00+00:00,100.10,100.90,99.80,100.40,1500

    ``open_time`` must be ISO-8601. If it carries no offset, ``default_tz``
    supplies one; when that is ``None`` the row is reported as a malformed
    timestamp rather than being guessed at as UTC. A wrong timezone silently
    shifts every candle in the file.

    ``is_closed`` defaults to ``true`` when the column is absent - a historical
    file records settled bars - but an explicit ``false`` is preserved, so a
    file that does contain a still-forming last bar is carried through to the
    Data Quality Engine, which blocks it.
    """

    def __init__(
        self,
        root: Path,
        *,
        default_tz: tzinfo | None = UTC,
    ) -> None:
        self._root = Path(root)
        self._default_tz = default_tz

    def path_for(self, symbol: str, timeframe: Timeframe) -> Path:
        return self._root / symbol / f"{timeframe.value}.csv"

    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Sequence[Candle]:
        """Return candles with ``start <= open_time < end``, in file order.

        Satisfies ``HistoricalMarketDataProvider``. Rows that failed to parse
        are absent here; use ``fetch`` when the parse issues themselves are
        needed, which is what the loading use case does.
        """
        result = await self.fetch(symbol, timeframe, start, end)
        return result.candles

    async def fetch(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> MarketDataFetch:
        """Parse the file, returning both candles and parse failures.

        Satisfies ``DiagnosticHistoricalMarketDataProvider``.
        """
        return await asyncio.to_thread(self._load_sync, symbol, timeframe, start, end)

    def _load_sync(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None,
        end: datetime | None,
    ) -> MarketDataFetch:
        path = self.path_for(symbol, timeframe)
        if not path.is_file():
            raise CsvSchemaError(f"no market data file at {path}")

        candles: list[Candle] = []
        issues: list[DataQualityIssue] = []

        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise CsvSchemaError(f"{path} is empty; it has no header row")
            missing = [name for name in REQUIRED_COLUMNS if name not in reader.fieldnames]
            if missing:
                raise CsvSchemaError(f"{path} is missing column(s): {', '.join(missing)}")

            # Header is line 1, so the first data row is line 2.
            for line_number, row in enumerate(reader, start=2):
                try:
                    candle = self._parse_row(row, symbol=symbol, timeframe=timeframe)
                except _RowError as error:
                    issues.append(
                        DataQualityIssue(
                            code=DataQualityCode.MALFORMED_ROW,
                            severity=DataQualitySeverity.BLOCK,
                            message=f"{path.name} line {line_number}: {error}",
                        )
                    )
                    continue

                if start is not None and candle.open_time < start:
                    continue
                if end is not None and candle.open_time >= end:
                    continue
                candles.append(candle)

        return MarketDataFetch(candles=tuple(candles), issues=tuple(issues))

    def _parse_row(
        self, row: dict[str, str | None], *, symbol: str, timeframe: Timeframe
    ) -> Candle:
        open_time = self._parse_timestamp(_required(row, "open_time"))
        return Candle(
            symbol=symbol,
            timeframe=timeframe,
            open_time=open_time,
            open=_parse_decimal(row, "open"),
            high=_parse_decimal(row, "high"),
            low=_parse_decimal(row, "low"),
            close=_parse_decimal(row, "close"),
            volume=_parse_decimal(row, "volume"),
            is_closed=_parse_bool(row.get("is_closed")),
            open_interest=_parse_optional_decimal(row, "open_interest"),
        )

    def _parse_timestamp(self, raw: str) -> datetime:
        try:
            moment = datetime.fromisoformat(raw.strip())
        except ValueError as error:
            raise _RowError(f"open_time {raw!r} is not ISO-8601") from error

        if moment.tzinfo is not None:
            return moment
        if self._default_tz is None:
            raise _RowError(
                f"open_time {raw!r} has no UTC offset and no default timezone is configured"
            )
        return moment.replace(tzinfo=self._default_tz)


class _RowError(ValueError):
    """One row is unusable. Reported as a data quality issue, never raised out."""


def _required(row: dict[str, str | None], column: str) -> str:
    value = row.get(column)
    if value is None or not value.strip():
        raise _RowError(f"{column} is empty")
    return value


def _parse_decimal(row: dict[str, str | None], column: str) -> Decimal:
    raw = _required(row, column)
    try:
        return Decimal(raw.strip())
    except InvalidOperation as error:
        raise _RowError(f"{column} {raw!r} is not a number") from error


def _parse_optional_decimal(row: dict[str, str | None], column: str) -> Decimal | None:
    value = row.get(column)
    if value is None or not value.strip():
        return None
    return _parse_decimal(row, column)


def _parse_bool(value: str | None) -> bool:
    """Absent means closed; anything else must say so unambiguously."""
    if value is None or not value.strip():
        return True
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise _RowError(f"is_closed {value!r} is not a boolean")

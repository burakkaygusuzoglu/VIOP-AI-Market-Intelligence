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
import io
from collections.abc import Sequence
from datetime import UTC, datetime, tzinfo
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.application.ports.market_data import (
    CandleParseError,
    CandleRowLimitError,
    MarketDataFetch,
)
from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle
from app.domain.market.quality import (
    DataQualityCode,
    DataQualityIssue,
    DataQualitySeverity,
)

REQUIRED_COLUMNS = ("open_time", "open", "high", "low", "close", "volume")
"""Minimum schema. ``open_interest`` and ``is_closed`` are optional columns."""


class CsvSchemaError(CandleParseError):
    """The file cannot be read as market data at all.

    Distinct from a malformed row. A missing ``close`` column is not a data
    quality finding to weigh up - there is no dataset to assess - so it is
    raised rather than reported.

    Subclasses the port's `CandleParseError` so the application layer can catch
    the failure without importing this adapter, while existing callers that
    catch `CsvSchemaError` keep working unchanged.
    """


class CsvRowLimitError(CsvSchemaError, CandleRowLimitError):
    """The file has more rows than the caller is willing to accept.

    Both a CSV schema error and the port's row-limit error, so it is catchable
    from either side of the boundary.
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

        return parse_candle_csv(
            path.read_text(encoding="utf-8"),
            symbol=symbol,
            timeframe=timeframe,
            source_name=path.name,
            default_tz=self._default_tz,
            start=start,
            end=end,
        )


def parse_candle_csv(
    text: str,
    *,
    symbol: str,
    timeframe: Timeframe,
    source_name: str,
    default_tz: tzinfo | None = UTC,
    start: datetime | None = None,
    end: datetime | None = None,
    max_rows: int | None = None,
) -> MarketDataFetch:
    """Parse CSV *text* into candles and parse failures.

    The single OHLCV parser in the project. It exists as a module function
    rather than a method so that a file on disk and an uploaded body go through
    exactly the same code: a second parser would drift, and the two would
    eventually disagree about what a malformed row is - which is a difference a
    user would experience as "the same file behaves differently depending on
    how I gave it to you".

    It keeps every rule this adapter always had, and adds none:

    * no sorting, no deduplication, no gap filling, no clamping. Those are
      findings for the Data Quality Engine to *report*, and repairing them here
      would destroy the evidence that they happened.
    * an unparseable row becomes a ``MALFORMED_ROW`` issue travelling with the
      data, not an exception.
    * a structurally impossible file - no header, a missing required column -
      raises ``CsvSchemaError``, because there is no dataset to assess.

    ``max_rows`` bounds the number of *data rows* accepted. Exceeding it raises
    ``CsvRowLimitError`` rather than returning a truncated dataset, because a
    silently truncated series is a wrong analysis rather than a refused one.
    """
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames is None:
        raise CsvSchemaError(f"{source_name} is empty; it has no header row")
    missing = [name for name in REQUIRED_COLUMNS if name not in reader.fieldnames]
    if missing:
        raise CsvSchemaError(f"{source_name} is missing column(s): {', '.join(missing)}")

    candles: list[Candle] = []
    issues: list[DataQualityIssue] = []
    rows_seen = 0

    # Header is line 1, so the first data row is line 2.
    for line_number, row in enumerate(reader, start=2):
        rows_seen += 1
        if max_rows is not None and rows_seen > max_rows:
            raise CsvRowLimitError(
                f"{source_name} has more than {max_rows} rows; refusing to analyse a "
                "truncated dataset"
            )
        try:
            candle = _parse_row(row, symbol=symbol, timeframe=timeframe, default_tz=default_tz)
        except _RowError as error:
            issues.append(
                DataQualityIssue(
                    code=DataQualityCode.MALFORMED_ROW,
                    severity=DataQualitySeverity.BLOCK,
                    message=f"{source_name} line {line_number}: {error}",
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
    row: dict[str, str | None],
    *,
    symbol: str,
    timeframe: Timeframe,
    default_tz: tzinfo | None,
) -> Candle:
    open_time = _parse_timestamp(_required(row, "open_time"), default_tz)
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


def _parse_timestamp(raw: str, default_tz: tzinfo | None) -> datetime:
    try:
        moment = datetime.fromisoformat(raw.strip())
    except ValueError as error:
        raise _RowError(f"open_time {raw!r} is not ISO-8601") from error

    if moment.tzinfo is not None:
        return moment
    if default_tz is None:
        raise _RowError(
            f"open_time {raw!r} has no UTC offset and no default timezone is configured"
        )
    return moment.replace(tzinfo=default_tz)


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


class CsvCandleTextParser:
    """`CandleTextParser` over CSV text.

    A thin object around `parse_candle_csv` so the application layer can depend
    on the port instead of on this module. It holds only the timezone policy;
    all parsing behaviour stays in the one shared function, which is also what
    the on-disk provider uses.
    """

    def __init__(self, *, default_tz: tzinfo | None = UTC) -> None:
        self._default_tz = default_tz

    def parse(
        self,
        text: str,
        *,
        symbol: str,
        timeframe: Timeframe,
        source_name: str,
        max_rows: int | None = None,
    ) -> MarketDataFetch:
        return parse_candle_csv(
            text,
            symbol=symbol,
            timeframe=timeframe,
            source_name=source_name,
            default_tz=self._default_tz,
            max_rows=max_rows,
        )

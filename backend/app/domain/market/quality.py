"""The Data Quality Engine (master spec section 40).

Data integrity is a product feature, not a helper function. Every dataset that
reaches a calculation passes through here first, and the engine's output is
evidence: a verdict plus the specific reasons behind it, suitable for the data
quality panel of section 41 and for the audit trail of section 67.

Design rules this module obeys:

*Nothing is silently repaired.* Corruption is reported and blocks the dataset.
The single normalization performed - converting timezone-aware timestamps to
UTC - preserves the exact instant, changes no value, and is recorded as an
explicit issue rather than done quietly.

*Absence of proof is not proof of absence.* A gap between candles is a warning,
never a block, because distinguishing missing data from a closed market
requires verified VIOP session hours. Those are mutable exchange facts and are
unavailable until they are obtained from an authoritative source (section 118).
Blocking on them would mean inventing a trading calendar.

*Deterministic.* No clock, no randomness, no environment. The same candles and
the same policy always produce the same report.

Deliberately **not** implemented here, with the phase that owns each:

``stale feed``
    Requires wall-clock comparison against a live feed's last tick (sections 50
    and 63). Historical validation must not depend on the current time.
    Phase 13.
``wrong contract`` / ``expired contract``
    Requires ``ContractMetadataProvider`` - expiry dates, symbol format and
    settlement rules - which are section 118 facts scheduled for Phase 3.

Neither is stubbed out or claimed as covered.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle
from app.domain.market.series import CandleSeries, ValidatedCandleSeries


@unique
class DataQualitySeverity(StrEnum):
    """How much weight an individual finding carries."""

    INFO = "INFO"
    """A deterministic, value-preserving normalization was applied."""

    WARNING = "WARNING"
    """Usable, but the analysis must disclose the caveat."""

    BLOCK = "BLOCK"
    """Integrity failure. No calculation may run on this dataset."""


@unique
class DataQualityVerdict(StrEnum):
    """The overall outcome for a dataset."""

    ACCEPTED = "ACCEPTED"
    ACCEPTED_WITH_WARNINGS = "ACCEPTED_WITH_WARNINGS"
    BLOCKED = "BLOCKED"

    @property
    def is_blocked(self) -> bool:
        return self is DataQualityVerdict.BLOCKED


@unique
class DataQualityCode(StrEnum):
    """Every condition this engine can detect.

    A stable code per condition, so the frontend, the audit log and the tests
    all refer to a finding by the same name rather than by message text.
    """

    # --- structural, always blocking ---------------------------------
    EMPTY_SERIES = "EMPTY_SERIES"
    NAIVE_TIMESTAMP = "NAIVE_TIMESTAMP"
    NON_FINITE_VALUE = "NON_FINITE_VALUE"
    NEGATIVE_PRICE = "NEGATIVE_PRICE"
    ZERO_PRICE = "ZERO_PRICE"
    INVALID_OHLC_RELATIONSHIP = "INVALID_OHLC_RELATIONSHIP"
    NEGATIVE_VOLUME = "NEGATIVE_VOLUME"
    FORMING_CANDLE = "FORMING_CANDLE"
    SYMBOL_MISMATCH = "SYMBOL_MISMATCH"
    TIMEFRAME_MISMATCH = "TIMEFRAME_MISMATCH"
    DUPLICATE_CANDLE = "DUPLICATE_CANDLE"
    CONFLICTING_DUPLICATE = "CONFLICTING_DUPLICATE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    IRREGULAR_INTERVAL = "IRREGULAR_INTERVAL"
    MALFORMED_ROW = "MALFORMED_ROW"

    # --- advisory ----------------------------------------------------
    MISSING_CANDLES = "MISSING_CANDLES"
    ZERO_VOLUME = "ZERO_VOLUME"
    EXTREME_MOVE = "EXTREME_MOVE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"

    # --- informational -----------------------------------------------
    TIMEZONE_NORMALIZED = "TIMEZONE_NORMALIZED"


@dataclass(frozen=True, slots=True)
class DataQualityIssue:
    """One finding, with enough context to explain and locate it."""

    code: DataQualityCode
    severity: DataQualitySeverity
    message: str
    candle_index: int | None = None
    open_time: datetime | None = None

    @property
    def is_blocking(self) -> bool:
        return self.severity is DataQualitySeverity.BLOCK


@dataclass(frozen=True, slots=True)
class DataQualityPolicy:
    """Tunable thresholds, stated explicitly rather than buried in the code.

    Every value here is a project decision, not an exchange fact. None of them
    describes VIOP, Borsa Istanbul or any contract specification, so none falls
    under the section 118 verification requirement.
    """

    minimum_candles: int = 2
    """Below this, nothing at all can be computed. Per-indicator sufficiency is
    not decided here: an indicator reports its own warm-up as ``None`` rather
    than the engine guessing how much history the caller needs."""

    extreme_move_ratio: Decimal = Decimal("0.20")
    """Absolute close-to-close change flagged as a suspicious spike. A project
    heuristic for surfacing likely bad ticks, not a market rule."""

    flag_zero_volume: bool = True
    """Zero-volume bars are legitimate in an illiquid contract, so they warn
    rather than block - but they are never hidden."""


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    """The verdict plus the evidence behind it."""

    verdict: DataQualityVerdict
    issues: tuple[DataQualityIssue, ...] = field(default=())
    candle_count: int = 0
    symbol: str | None = None
    timeframe: Timeframe | None = None

    @property
    def is_blocked(self) -> bool:
        return self.verdict.is_blocked

    @property
    def blocking_issues(self) -> tuple[DataQualityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.is_blocking)

    @property
    def warnings(self) -> tuple[DataQualityIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity is DataQualitySeverity.WARNING
        )

    @property
    def codes(self) -> frozenset[DataQualityCode]:
        return frozenset(issue.code for issue in self.issues)

    def has(self, code: DataQualityCode) -> bool:
        return code in self.codes


@dataclass(frozen=True, slots=True)
class DataQualityAssessment:
    """The engine's complete output.

    ``series`` is present exactly when the verdict is not ``BLOCKED``. Callers
    branch on it rather than on a boolean, so a blocked dataset has no usable
    series to accidentally pass along.
    """

    report: DataQualityReport
    series: ValidatedCandleSeries | None

    @property
    def is_usable(self) -> bool:
        return self.series is not None


def _is_non_finite(value: Decimal) -> bool:
    return value.is_nan() or value.is_infinite()


def _candle_payload(candle: Candle) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    return (candle.open, candle.high, candle.low, candle.close, candle.volume)


class DataQualityEngine:
    """Assesses a raw candle series against the section 40 rule set.

    Stateless and deterministic. Held as a class rather than a bare function so
    a policy can be bound once and reused by live, replay, backtest and shadow
    mode without re-passing it (master spec section 74).
    """

    def __init__(self, policy: DataQualityPolicy | None = None) -> None:
        self._policy = policy if policy is not None else DataQualityPolicy()

    @property
    def policy(self) -> DataQualityPolicy:
        return self._policy

    def assess(
        self,
        series: CandleSeries,
        *,
        extra_issues: Sequence[DataQualityIssue] = (),
    ) -> DataQualityAssessment:
        """Validate ``series`` and, if it survives, return a usable series.

        ``extra_issues`` carries findings a provider already made - malformed
        CSV rows, for instance - so adapter-level problems appear in the same
        report as domain-level ones instead of being reported separately or
        lost.
        """
        issues: list[DataQualityIssue] = list(extra_issues)

        if series.is_empty:
            issues.append(
                DataQualityIssue(
                    code=DataQualityCode.EMPTY_SERIES,
                    severity=DataQualitySeverity.BLOCK,
                    message="no candles were supplied",
                )
            )
            return self._blocked(issues, series, symbol=None, timeframe=None)

        first = series[0]
        symbol = first.symbol
        timeframe = first.timeframe

        issues.extend(self._check_candles(series, symbol=symbol, timeframe=timeframe))
        issues.extend(self._check_sequence(series, timeframe=timeframe))
        issues.extend(self._check_statistics(series))

        if len(series) < self._policy.minimum_candles:
            issues.append(
                DataQualityIssue(
                    code=DataQualityCode.INSUFFICIENT_HISTORY,
                    severity=DataQualitySeverity.WARNING,
                    message=(
                        f"{len(series)} candle(s) supplied, "
                        f"policy minimum is {self._policy.minimum_candles}"
                    ),
                )
            )

        if any(issue.is_blocking for issue in issues):
            return self._blocked(issues, series, symbol=symbol, timeframe=timeframe)

        normalized, normalization = self._normalize_timezone(series)
        issues.extend(normalization)

        verdict = (
            DataQualityVerdict.ACCEPTED_WITH_WARNINGS
            if any(issue.severity is DataQualitySeverity.WARNING for issue in issues)
            else DataQualityVerdict.ACCEPTED
        )
        report = DataQualityReport(
            verdict=verdict,
            issues=tuple(issues),
            candle_count=len(series),
            symbol=symbol,
            timeframe=timeframe,
        )
        return DataQualityAssessment(
            report=report,
            series=ValidatedCandleSeries(
                candles=normalized,
                symbol=symbol,
                timeframe=timeframe,
            ),
        )

    # ------------------------------------------------------------------
    # Per-candle rules
    # ------------------------------------------------------------------

    def _check_candles(
        self, series: CandleSeries, *, symbol: str, timeframe: Timeframe
    ) -> list[DataQualityIssue]:
        issues: list[DataQualityIssue] = []
        for index, candle in enumerate(series):
            at = candle.open_time if candle.open_time.tzinfo is not None else None

            def block(
                code: DataQualityCode,
                message: str,
                _index: int = index,
                _at: datetime | None = at,
            ) -> None:
                issues.append(
                    DataQualityIssue(
                        code=code,
                        severity=DataQualitySeverity.BLOCK,
                        message=message,
                        candle_index=_index,
                        open_time=_at,
                    )
                )

            if candle.open_time.tzinfo is None or candle.open_time.utcoffset() is None:
                block(
                    DataQualityCode.NAIVE_TIMESTAMP,
                    "open_time is timezone-naive; the instant it denotes is ambiguous",
                )

            if not candle.is_closed:
                block(
                    DataQualityCode.FORMING_CANDLE,
                    "candle is still forming and must not be used as a historical fact",
                )

            if candle.symbol != symbol:
                block(
                    DataQualityCode.SYMBOL_MISMATCH,
                    f"symbol {candle.symbol!r} differs from series symbol {symbol!r}",
                )

            if candle.timeframe is not timeframe:
                block(
                    DataQualityCode.TIMEFRAME_MISMATCH,
                    f"timeframe {candle.timeframe} differs from series timeframe {timeframe}",
                )

            issues.extend(self._check_numbers(candle, index=index, at=at))

        return issues

    def _check_numbers(
        self, candle: Candle, *, index: int, at: datetime | None
    ) -> list[DataQualityIssue]:
        issues: list[DataQualityIssue] = []

        def add(code: DataQualityCode, severity: DataQualitySeverity, message: str) -> None:
            issues.append(
                DataQualityIssue(
                    code=code,
                    severity=severity,
                    message=message,
                    candle_index=index,
                    open_time=at,
                )
            )

        numbers: dict[str, Decimal] = {
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
        }
        if candle.open_interest is not None:
            numbers["open_interest"] = candle.open_interest

        non_finite = [name for name, value in numbers.items() if _is_non_finite(value)]
        if non_finite:
            add(
                DataQualityCode.NON_FINITE_VALUE,
                DataQualitySeverity.BLOCK,
                f"{', '.join(non_finite)} is NaN or infinite",
            )
            # Every comparison below is meaningless against NaN.
            return issues

        prices = {name: numbers[name] for name in ("open", "high", "low", "close")}
        negative = [name for name, value in prices.items() if value < 0]
        if negative:
            add(
                DataQualityCode.NEGATIVE_PRICE,
                DataQualitySeverity.BLOCK,
                f"{', '.join(negative)} is negative",
            )
        zero = [name for name, value in prices.items() if value == 0]
        if zero:
            add(
                DataQualityCode.ZERO_PRICE,
                DataQualitySeverity.BLOCK,
                f"{', '.join(zero)} is zero, which is not a tradeable price",
            )

        if candle.high < candle.low:
            add(
                DataQualityCode.INVALID_OHLC_RELATIONSHIP,
                DataQualitySeverity.BLOCK,
                f"high {candle.high} is below low {candle.low}",
            )
        elif candle.high < max(candle.open, candle.close) or candle.low > min(
            candle.open, candle.close
        ):
            add(
                DataQualityCode.INVALID_OHLC_RELATIONSHIP,
                DataQualitySeverity.BLOCK,
                (
                    f"open {candle.open} / close {candle.close} fall outside the "
                    f"range low {candle.low} - high {candle.high}"
                ),
            )

        if candle.volume < 0:
            add(
                DataQualityCode.NEGATIVE_VOLUME,
                DataQualitySeverity.BLOCK,
                f"volume {candle.volume} is negative",
            )
        elif candle.volume == 0 and self._policy.flag_zero_volume:
            add(
                DataQualityCode.ZERO_VOLUME,
                DataQualitySeverity.WARNING,
                "no volume traded in this candle",
            )

        if candle.open_interest is not None and candle.open_interest < 0:
            add(
                DataQualityCode.NEGATIVE_VOLUME,
                DataQualitySeverity.BLOCK,
                f"open interest {candle.open_interest} is negative",
            )

        return issues

    # ------------------------------------------------------------------
    # Sequence rules
    # ------------------------------------------------------------------

    def _check_sequence(
        self, series: CandleSeries, *, timeframe: Timeframe
    ) -> list[DataQualityIssue]:
        issues: list[DataQualityIssue] = []
        interval = timedelta(minutes=timeframe.minutes)

        for index in range(1, len(series)):
            previous = series[index - 1]
            current = series[index]
            if previous.open_time.tzinfo is None or current.open_time.tzinfo is None:
                continue  # already blocked as NAIVE_TIMESTAMP

            delta = current.open_time - previous.open_time

            if delta == timedelta(0):
                identical = _candle_payload(previous) == _candle_payload(current)
                issues.append(
                    DataQualityIssue(
                        code=(
                            DataQualityCode.DUPLICATE_CANDLE
                            if identical
                            else DataQualityCode.CONFLICTING_DUPLICATE
                        ),
                        severity=DataQualitySeverity.BLOCK,
                        message=(
                            "duplicate candle for this timestamp"
                            if identical
                            else "two different candles share this timestamp"
                        ),
                        candle_index=index,
                        open_time=current.open_time,
                    )
                )
                continue

            if delta < timedelta(0):
                issues.append(
                    DataQualityIssue(
                        code=DataQualityCode.OUT_OF_ORDER,
                        severity=DataQualitySeverity.BLOCK,
                        message=(
                            f"open_time goes backwards from {previous.open_time.isoformat()}; "
                            "candles are not reordered, because reordering would hide "
                            "which source produced them out of sequence"
                        ),
                        candle_index=index,
                        open_time=current.open_time,
                    )
                )
                continue

            remainder = delta % interval
            if remainder != timedelta(0):
                issues.append(
                    DataQualityIssue(
                        code=DataQualityCode.IRREGULAR_INTERVAL,
                        severity=DataQualitySeverity.BLOCK,
                        message=(
                            f"spacing {delta} is not a whole multiple of the {timeframe} "
                            "interval; the data is mistimed or of a different timeframe"
                        ),
                        candle_index=index,
                        open_time=current.open_time,
                    )
                )
                continue

            missing = delta // interval - 1
            if missing > 0:
                issues.append(
                    DataQualityIssue(
                        code=DataQualityCode.MISSING_CANDLES,
                        severity=DataQualitySeverity.WARNING,
                        message=(
                            f"{missing} candle interval(s) absent before this candle; "
                            "a closed market and missing data cannot be told apart "
                            "without verified session hours"
                        ),
                        candle_index=index,
                        open_time=current.open_time,
                    )
                )

        return issues

    # ------------------------------------------------------------------
    # Statistical rules
    # ------------------------------------------------------------------

    def _check_statistics(self, series: CandleSeries) -> list[DataQualityIssue]:
        issues: list[DataQualityIssue] = []
        threshold = self._policy.extreme_move_ratio
        for index in range(1, len(series)):
            previous_close = series[index - 1].close
            current_close = series[index].close
            if _is_non_finite(previous_close) or _is_non_finite(current_close):
                continue
            if previous_close <= 0:
                continue
            change = abs(current_close - previous_close) / previous_close
            if change > threshold:
                issues.append(
                    DataQualityIssue(
                        code=DataQualityCode.EXTREME_MOVE,
                        severity=DataQualitySeverity.WARNING,
                        message=(
                            f"close moved {change:.2%} from the previous candle, "
                            f"above the {threshold:.2%} review threshold"
                        ),
                        candle_index=index,
                        open_time=(
                            series[index].open_time
                            if series[index].open_time.tzinfo is not None
                            else None
                        ),
                    )
                )
        return issues

    # ------------------------------------------------------------------
    # The one sanctioned normalization
    # ------------------------------------------------------------------

    def _normalize_timezone(
        self, series: CandleSeries
    ) -> tuple[tuple[Candle, ...], list[DataQualityIssue]]:
        """Express every timestamp in UTC.

        This is a change of representation, not of value: ``astimezone`` names
        the same instant. It happens so that downstream session logic - VWAP
        day anchoring, for one - has a single unambiguous reference, and it is
        recorded rather than done silently.
        """
        if all(candle.open_time.utcoffset() == timedelta(0) for candle in series):
            return tuple(series.candles), []

        converted = tuple(
            dataclasses.replace(candle, open_time=candle.open_time.astimezone(UTC))
            for candle in series
        )
        return converted, [
            DataQualityIssue(
                code=DataQualityCode.TIMEZONE_NORMALIZED,
                severity=DataQualitySeverity.INFO,
                message="timestamps converted to UTC; the instants they denote are unchanged",
            )
        ]

    def _blocked(
        self,
        issues: Sequence[DataQualityIssue],
        series: CandleSeries,
        *,
        symbol: str | None,
        timeframe: Timeframe | None,
    ) -> DataQualityAssessment:
        return DataQualityAssessment(
            report=DataQualityReport(
                verdict=DataQualityVerdict.BLOCKED,
                issues=tuple(issues),
                candle_count=len(series),
                symbol=symbol,
                timeframe=timeframe,
            ),
            series=None,
        )

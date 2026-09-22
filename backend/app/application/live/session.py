"""One live observation session: ingest, recover, and analyse on request.

## What this orchestrates

* the provider's stream, pulled one item at a time into the pure
  :class:`~app.domain.live.state.LiveMarketState`;
* backfill after a reconnect, when the provider can do it;
* the **existing** Phase 8 ``run_analysis`` pipeline, over the confirmed
  candles of the timeframes that are currently available.

It computes nothing itself: no indicator, no structure, no regime, no score.
An analysis here is the same analysis the upload path and replay run, given a
different prefix.

## Analysis runs only when asked

Not per event, and not on a timer. A request is served from a cache when the
confirmed prefix it would read has not changed since the last one - the key is
the set of available timeframes and each book's version - so asking twice
costs one analysis. Nothing here calls a language model.

## Receiving market data never trades

No path from this module creates a paper position, a replay position or a
backtest run, and none of those modules is imported. Enforced by an
import-linter contract, not by care.

## State is in memory

A session is ephemeral. After a process restart it does not exist, and nothing
claims otherwise: there is no persisted history for a new session to pretend
it continues.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.application.analysis.orchestrator import AnalysisInputError, AnalysisOutcome, run_analysis
from app.application.analysis.request import AnalysisRequest, TimeframeDataset
from app.application.analysis.serialisation import candles_to_csv
from app.application.live.ports import BackfillCapable, LiveMarketDataProvider, LiveSubscription
from app.application.live.records import RecordKind, StreamRecord
from app.application.ports.contract_metadata import ContractMetadataProvider
from app.application.ports.market_data import CandleTextParser
from app.application.ports.system import ClockPort
from app.domain.common.enums import Timeframe
from app.domain.live.alerts import AlertCandidate, alert_candidates
from app.domain.live.events import (
    MarketCurrency,
    ProviderSignal,
    RawCandleEvent,
    SignalKind,
    StreamKey,
    StreamProvenance,
    market_currency_of,
)
from app.domain.live.limits import LiveLimits
from app.domain.live.state import (
    ConnectionState,
    FreshnessPolicy,
    IllegalTransitionError,
    LiveMarketState,
    MarketStateSnapshot,
    TerminationReason,
)
from app.domain.live.validation import Rejection
from app.domain.risk.sizing import AccountState, RiskPolicy

__all__ = [
    "LiveAnalysis",
    "LiveAnalysisUnavailableError",
    "LiveSession",
]

_LOG = logging.getLogger(__name__)


class LiveAnalysisUnavailableError(RuntimeError):
    """Nothing available to analyse, with every reason why."""

    def __init__(self, code: str, reasons: Mapping[Timeframe, tuple[str, ...]]) -> None:
        self.code = code
        self.reasons = dict(reasons)
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class LiveAnalysis:
    """The existing analysis, over the confirmed prefix available at one moment."""

    outcome: AnalysisOutcome
    provenance: StreamProvenance
    market_currency: MarketCurrency
    """Follows from provenance and is checked against it. For the simulated
    stream always HISTORICAL: the analysis describes a past market moment,
    however recently its candles arrived."""

    market_as_of: datetime
    """The end of the latest confirmed candle analysed. The market moment the
    analysis describes - a historical moment, for a simulated stream."""

    requested_at: datetime
    """Receive-side time of the request. Never presented as a market time."""

    included: tuple[Timeframe, ...]
    excluded: Mapping[Timeframe, tuple[str, ...]]
    """Every subscribed timeframe that was left out, with the reasons."""

    fingerprint: tuple[object, ...]

    def __post_init__(self) -> None:
        if self.market_currency is not market_currency_of(self.provenance):
            raise ValueError("market currency must follow from provenance")


@dataclass(frozen=True, slots=True)
class _AsOf:
    """A clock that reports the market moment being analysed."""

    moment: datetime

    def now(self) -> datetime:
        return self.moment


class LiveSession:
    def __init__(
        self,
        *,
        provider: LiveMarketDataProvider,
        symbol: str,
        timeframes: tuple[Timeframe, ...],
        clock: ClockPort,
        parser: CandleTextParser,
        freshness: FreshnessPolicy,
        limits: LiveLimits | None = None,
        contracts: ContractMetadataProvider | None = None,
        backfill_limit: int = 500,
        observer: Callable[[StreamRecord], None] | None = None,
    ) -> None:
        self._provider = provider
        self._observer = observer
        self._clock = clock
        self._parser = parser
        self._contracts = contracts
        self._backfill_limit = backfill_limit
        self._state = LiveMarketState(
            symbol=symbol,
            timeframes=timeframes,
            provenance=provider.provenance,
            freshness=freshness,
            limits=limits or LiveLimits(),
        )
        self._subscription: LiveSubscription | None = None
        self._cached: LiveAnalysis | None = None
        self.analyses_run = 0

    # -- reading -----------------------------------------------------------

    @property
    def provenance(self) -> StreamProvenance:
        return self._state.provenance

    def snapshot(self) -> MarketStateSnapshot:
        return self._state.snapshot(self._clock.now())

    def alerts(self) -> tuple[AlertCandidate, ...]:
        return alert_candidates(self.snapshot())

    def state(self) -> LiveMarketState:
        """The pure state, for inspection. Tests read it; callers should not
        mutate it."""
        return self._state

    # -- ingestion ---------------------------------------------------------

    async def run(self) -> None:
        """Consume the stream until it ends, fails, or this task is cancelled."""
        try:
            self._subscription = await self._provider.subscribe(
                self._state.symbol, self._state.timeframes
            )
        except asyncio.CancelledError:
            # Cancelled before the stream opened: still an ended stream, not
            # one left looking as if it were about to start.
            self._state.terminate(TerminationReason.CANCELLED)
            raise
        except Exception as error:  # noqa: BLE001 - reported safely, below
            self._fail("subscribe", error)
            return
        try:
            async for item in self._subscription.items():
                if isinstance(item, ProviderSignal):
                    await self._on_signal(item)
                else:
                    self._apply(item, backfill=False)
                if self._state.connection is ConnectionState.TERMINATED:
                    break
        except asyncio.CancelledError:
            self._state.terminate(TerminationReason.CANCELLED)
            raise
        except IllegalTransitionError as error:
            self._fail("signal", error)
        except Exception as error:  # noqa: BLE001 - reported safely, below
            self._fail("stream", error)
        finally:
            await self._close()

    def _apply(self, event: RawCandleEvent, *, backfill: bool) -> None:
        received_at = self._clock.now()
        outcome = self._state.apply_event(event, received_at=received_at)
        if isinstance(outcome, Rejection):
            # A refused event's times are claims that failed validation; the
            # record carries the code and nothing the provider asserted.
            self._notify(
                StreamRecord(
                    kind=RecordKind.REJECTED,
                    outcome=outcome.code.value,
                    received_at=received_at,
                    backfill=backfill,
                )
            )
            return
        # Accepted, so every field below passed validation.
        self._notify(
            StreamRecord(
                kind=RecordKind.OBSERVATION,
                outcome=outcome.value,
                received_at=received_at,
                timeframe=Timeframe(str(event.timeframe)),
                open_time=event.open_time if isinstance(event.open_time, datetime) else None,
                event_time=event.event_time if isinstance(event.event_time, datetime) else None,
                closed=event.closed is True,
                sequence=event.sequence if isinstance(event.sequence, int) else None,
                backfill=backfill,
            )
        )

    def _notify(self, record: StreamRecord) -> None:
        """Tell the observer what just happened. It can never stop the stream.

        An observer is a reader - a timeline, a transport. If it fails, the
        market state it was reading is still correct, so the failure is logged
        by type and the stream goes on.
        """
        if self._observer is None:
            return
        try:
            self._observer(record)
        except Exception as error:  # noqa: BLE001 - reported safely, below
            _LOG.error(
                "live stream observer failed",
                extra={"symbol": self._state.symbol, "error_type": type(error).__name__},
            )

    async def _on_signal(self, signal: ProviderSignal) -> None:
        reconnecting = (
            signal.kind is SignalKind.CONNECTED
            and self._state.connection is ConnectionState.DISCONNECTED
        )
        self._state.apply_signal(signal)
        self._notify(
            StreamRecord(
                kind=RecordKind.SIGNAL,
                outcome=signal.kind.value,
                received_at=self._clock.now(),
            )
        )
        if reconnecting and self._state.connection is ConnectionState.RECOVERING:
            await self._backfill()

    async def _backfill(self) -> None:
        """Ask for what was missed, if the provider can say.

        Only sequenced books can be backfilled verifiably. Whatever comes back
        is validated like live data and placed under the same rule as live
        candles - sequence numbers checked against time. A provider that
        cannot backfill leaves the timeframes UNVERIFIED until the live stream
        proves or disproves continuity.
        """
        if not isinstance(self._provider, BackfillCapable):
            return
        for timeframe in self._state.timeframes:
            book = self._state.book(timeframe)
            last = book.status().last_sequence
            if not book.awaiting_continuity or last is None:
                continue
            events = await self._provider.backfill(
                StreamKey(self._state.symbol, timeframe),
                after_sequence=last,
                limit=self._backfill_limit,
            )
            for event in events[: self._backfill_limit]:
                self._apply(event, backfill=True)

    async def _close(self) -> None:
        if self._subscription is None:
            return
        try:
            await self._subscription.close()
        except Exception as error:  # noqa: BLE001 - closing must not mask the outcome
            _LOG.warning(
                "live subscription did not close cleanly",
                extra={"symbol": self._state.symbol, "error_type": type(error).__name__},
            )

    def _fail(self, stage: str, error: BaseException) -> None:
        """Terminate without letting the error's text anywhere.

        The Phase 12 boundary, kept: a provider exception can carry a URL with
        a key in it, a payload, or a path. What is logged is the stage and the
        exception's class, which is enough to find it and leaks nothing.
        """
        _LOG.error(
            "live stream stopped on a provider failure",
            extra={
                "symbol": self._state.symbol,
                "stage": stage,
                "error_type": type(error).__name__,
            },
        )
        self._state.terminate(TerminationReason.PROVIDER_ERROR)

    # -- analysis ----------------------------------------------------------

    async def confirmed_analysis(
        self,
        *,
        account: AccountState | None = None,
        risk_policy: RiskPolicy | None = None,
    ) -> LiveAnalysis:
        """The existing analysis over what is confirmed and available now.

        Timeframes that are stale, gapped, unverified or empty are left out
        and listed with their reasons; nothing is filled in for them. If none
        is available the request is refused rather than answered with a
        partial result presented as whole.
        """
        now = self._clock.now()
        snapshot = self._state.snapshot(now)
        included = snapshot.available
        excluded = {
            item.book.timeframe: item.reasons
            for item in snapshot.timeframes
            if item.book.timeframe not in included
        }
        if not included:
            raise LiveAnalysisUnavailableError("NO_TIMEFRAME_AVAILABLE", excluded)

        fingerprint: tuple[object, ...] = (
            tuple((tf.value, self._state.book(tf).status().version) for tf in included),
            account,
            risk_policy,
        )
        if self._cached is not None and self._cached.fingerprint == fingerprint:
            return LiveAnalysis(
                outcome=self._cached.outcome,
                provenance=self._cached.provenance,
                market_currency=self._cached.market_currency,
                market_as_of=self._cached.market_as_of,
                requested_at=now,
                included=included,
                excluded=excluded,
                fingerprint=fingerprint,
            )

        datasets: list[TimeframeDataset] = []
        market_as_of: datetime | None = None
        for timeframe in included:
            candles = [
                observation.candle for observation in self._state.book(timeframe).confirmed()
            ]
            end = candles[-1].open_time + timedelta(minutes=timeframe.minutes)
            market_as_of = end if market_as_of is None else max(market_as_of, end)
            datasets.append(
                TimeframeDataset(
                    timeframe=timeframe,
                    content=candles_to_csv(candles),
                    source_name=f"live {timeframe.value}",
                )
            )
        assert market_as_of is not None  # noqa: S101 - `included` is non-empty

        try:
            outcome = await run_analysis(
                AnalysisRequest(
                    symbol=self._state.symbol,
                    datasets=tuple(datasets),
                    account=account,
                    risk_policy=risk_policy,
                ),
                parser=self._parser,
                clock=_AsOf(market_as_of),
                contracts=self._contracts,
            )
        except AnalysisInputError as error:
            raise LiveAnalysisUnavailableError(error.code, excluded) from None
        except Exception as error:  # noqa: BLE001 - reported safely, below
            _LOG.error(
                "live analysis failed unexpectedly",
                extra={"symbol": self._state.symbol, "error_type": type(error).__name__},
            )
            raise LiveAnalysisUnavailableError("ANALYSIS_FAILED", excluded) from None

        self.analyses_run += 1
        result = LiveAnalysis(
            outcome=outcome,
            provenance=self._state.provenance,
            market_currency=market_currency_of(self._state.provenance),
            market_as_of=market_as_of,
            requested_at=now,
            included=included,
            excluded=excluded,
            fingerprint=fingerprint,
        )
        self._cached = result
        return result

    def last_analysis(self) -> tuple[LiveAnalysis, bool] | None:
        """The most recent analysis, and whether it still describes the stream.

        ``current`` is recomputed from the stream's state at every call, never
        stored: true only while the same timeframes are available *now* with
        the same confirmed candles the analysis read. A CONNECTED signal, a
        later receive time, a trim or a gap scrolling out cannot restore it on
        their own - each either leaves a timeframe unavailable or changes a
        book version. Nothing is re-analysed to answer.

        ``current`` is about this stream, not the market: the analysis's
        ``market_currency`` says whether the stream is the present market at
        all, and for the simulated stream it never is.
        """
        if self._cached is None:
            return None
        snapshot = self._state.snapshot(self._clock.now())
        still = tuple(
            (tf.value, self._state.book(tf).status().version) for tf in snapshot.available
        )
        current = (
            snapshot.connection in (ConnectionState.CONNECTED, ConnectionState.RECOVERING)
            and self._cached.fingerprint[0] == still
        )
        return self._cached, current

"""Paper-trading use cases (Phase 9).

## What the client may say, and what only the server decides

A client says: which symbol, which direction, how many units, the intended
entry, the stop, the targets, the decision time, the account and risk settings
the sizing should use, the simulation choices, and a note. It supplies closed
historical bars as CSV text - the same user-supplied data Phase 8 analyses.

A client never says: whether the trade is permitted, what the product is, what
a point is worth, where anything filled, what the P&L is, what state the
position is in, or what kind of event happened. The server resolves the
product from its own metadata, re-runs the Phase 3 risk engine against the
stated account, simulates every fill, and derives every number.

## Consistency

Every write happens inside one unit of work that locks the position row, rebuilds
the position by replaying its stored inputs, requires that replay to reproduce
the stored ledger exactly, applies the new input, and appends only the new
events. Two requests racing to close the same units serialise on the lock; the
second sees a closed position and is refused. A write that finds nothing new to
record commits nothing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum, unique

from app.application.paper.codec import StoredValueError, encode_policy
from app.application.ports.market_data import CandleParseError, CandleTextParser
from app.application.ports.paper import (
    ConcurrentModificationError,
    DuplicatePositionError,
    PaperStore,
    PaperUnitOfWork,
    PositionProjection,
    PositionSummary,
    ProductResolver,
    ProductSnapshotCodec,
    ProductSnapshotError,
    StoredEvent,
    StoredPosition,
)
from app.application.ports.system import ClockPort
from app.domain.common.enums import Direction, Timeframe
from app.domain.instrument.policy import ProductPolicy
from app.domain.market.candle import Candle
from app.domain.market.quality import DataQualityEngine
from app.domain.market.series import CandleSeries
from app.domain.paper import (
    PaperInputError,
    PaperPosition,
    PaperRefusalError,
    PositionSpec,
    RiskApproval,
    SimulationPolicy,
    SimulationPolicyError,
    TargetSpec,
    apply_observation,
    cancel,
    move_stop_to_breakeven,
    open_position,
    rebuild,
    request_close,
    unrealized_gross,
)
from app.domain.risk.sizing import (
    AccountState,
    PositionSizing,
    RiskInputError,
    RiskPolicy,
    size_for_product,
)

IDEMPOTENCY_KEY_LENGTH = (16, 128)


@dataclass(frozen=True, slots=True)
class PaperLimits:
    """Resource bounds. Project decisions, not market facts."""

    max_page_size: int = 50
    max_event_page_size: int = 200
    max_observation_rows: int = 500
    """Bars accepted in one upload. Applied atomically, so one request holds a
    bounded lock for a bounded amount of work."""

    max_observation_bytes: int = 256 * 1024
    max_bars_per_position: int = 5_000
    """Every write replays the whole ledger; this keeps that replay bounded."""


@unique
class PaperErrorKind(StrEnum):
    INVALID = "INVALID"
    REFUSED = "REFUSED"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    UNAVAILABLE = "UNAVAILABLE"
    TOO_LARGE = "TOO_LARGE"


class PaperServiceError(Exception):
    """A typed failure the API maps to a status code. Never a market opinion."""

    def __init__(self, kind: PaperErrorKind, code: str, detail: str) -> None:
        self.kind = kind
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class CreatePaperPosition:
    idempotency_key: str
    symbol: str
    direction: Direction
    quantity: int
    intended_entry: Decimal
    stop: Decimal
    targets: tuple[TargetSpec, ...]
    timeframe: Timeframe
    decision_time: datetime
    account: AccountState
    risk: RiskPolicy
    policy: SimulationPolicy
    note: str | None = None


@dataclass(frozen=True, slots=True)
class PositionView:
    """What a caller is told about one position."""

    stored: StoredPosition
    replayed: bool = False
    """True when a create request matched an existing idempotency key."""


@dataclass(frozen=True, slots=True)
class PositionPage:
    items: Sequence[PositionSummary]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class EventPage:
    items: Sequence[StoredEvent]
    total: int
    after_sequence: int
    limit: int


class PaperTradingService:
    def __init__(
        self,
        *,
        store: PaperStore,
        codec: ProductSnapshotCodec,
        resolver: ProductResolver | None,
        parser: CandleTextParser,
        clock: ClockPort,
        limits: PaperLimits | None = None,
    ) -> None:
        self._store = store
        self._codec = codec
        self._resolver = resolver
        self._parser = parser
        self._clock = clock
        self._limits = limits if limits is not None else PaperLimits()

    @property
    def limits(self) -> PaperLimits:
        return self._limits

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create(self, command: CreatePaperPosition) -> PositionView:
        key = _validated_key(command.idempotency_key)
        fingerprint = _fingerprint(command)
        position_id = "PP-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]

        async with self._store.unit_of_work() as work:
            existing = await work.find_by_idempotency_key(key)
            if existing is not None:
                return self._answer_retry(existing, fingerprint)

            now = self._clock.now()
            if command.decision_time > now:
                raise PaperServiceError(
                    PaperErrorKind.INVALID,
                    "DECISION_IN_FUTURE",
                    "the decision time is later than the server's current time",
                )

            product = await self._resolve(command.symbol)
            sizing = self._size(command, product)
            spec = self._spec(command, position_id)
            try:
                position = open_position(spec, RiskApproval.from_sizing(sizing), product)
            except PaperRefusalError as error:
                raise PaperServiceError(
                    PaperErrorKind.REFUSED, error.code.value, error.reason
                ) from error

            stored = StoredPosition(
                position_id=position_id,
                idempotency_key=key,
                request_fingerprint=fingerprint,
                spec=spec,
                approval=position.approval,
                product_snapshot=self._codec.snapshot(product),
                events=position.events,
                projection=_project(position, product),
                version=1,
                created_at=now,
                updated_at=now,
            )
            try:
                await work.insert(stored, now)
                await work.commit()
            except DuplicatePositionError:
                pass
            else:
                return PositionView(stored=stored)

        # Lost a race with an identical key: answer from whatever won.
        winner = await self._store_get_by_key(key)
        return self._answer_retry(winner, fingerprint)

    async def _store_get_by_key(self, key: str) -> StoredPosition:
        async with self._store.unit_of_work() as work:
            found = await work.find_by_idempotency_key(key)
        if found is None:  # pragma: no cover - the insert that beat us committed
            raise PaperServiceError(
                PaperErrorKind.CONFLICT, "CONCURRENT_CREATE", "a concurrent create did not persist"
            )
        return found

    def _answer_retry(self, existing: StoredPosition, fingerprint: str) -> PositionView:
        if existing.request_fingerprint != fingerprint:
            raise PaperServiceError(
                PaperErrorKind.CONFLICT,
                "IDEMPOTENCY_KEY_REUSED",
                "this idempotency key was already used for a different paper position request",
            )
        return PositionView(stored=existing, replayed=True)

    async def _resolve(self, symbol: str) -> ProductPolicy:
        if self._resolver is None:
            raise PaperServiceError(
                PaperErrorKind.REFUSED,
                "PRODUCT_METADATA_UNAVAILABLE",
                "no contract metadata provider is configured, so no product can be resolved; "
                "a paper position is never opened against assumed specifications",
            )
        product = await self._resolver.resolve(symbol)
        if product is None:
            raise PaperServiceError(
                PaperErrorKind.REFUSED,
                "PRODUCT_NOT_FOUND",
                f"no server-side product metadata exists for {symbol.strip()!r}",
            )
        return product

    def _size(self, command: CreatePaperPosition, product: ProductPolicy) -> PositionSizing:
        try:
            return size_for_product(
                command.direction,
                command.intended_entry,
                command.stop,
                product,
                command.account,
                command.risk,
            )
        except (RiskInputError, ValueError) as error:
            raise PaperServiceError(
                PaperErrorKind.REFUSED, "RISK_INPUT_INVALID", str(error)
            ) from error

    def _spec(self, command: CreatePaperPosition, position_id: str) -> PositionSpec:
        try:
            return PositionSpec(
                position_id=position_id,
                symbol=command.symbol.strip(),
                direction=command.direction,
                quantity=command.quantity,
                intended_entry=command.intended_entry,
                stop=command.stop,
                targets=command.targets,
                timeframe=command.timeframe,
                decision_time=command.decision_time,
                policy=command.policy,
                note=command.note,
            )
        except (PaperInputError, SimulationPolicyError) as error:
            raise PaperServiceError(
                PaperErrorKind.INVALID, "INVALID_POSITION", str(error)
            ) from error

    # ------------------------------------------------------------------
    # Inputs after opening
    # ------------------------------------------------------------------

    async def observe(self, position_id: str, content: str, source_name: str) -> PositionView:
        size = len(content.encode("utf-8"))
        if size > self._limits.max_observation_bytes:
            raise PaperServiceError(
                PaperErrorKind.TOO_LARGE,
                "OBSERVATIONS_TOO_LARGE",
                f"{size} bytes exceeds the {self._limits.max_observation_bytes} byte limit",
            )
        current = await self._store.get(position_id)
        if current is None:
            raise _not_found(position_id)
        bars = self._parse(current.spec, content, source_name)

        async with self._store.unit_of_work() as work:
            stored = await work.load_for_update(position_id)
            if stored is None:  # pragma: no cover - deleted between reads; nothing deletes
                raise _not_found(position_id)
            product, position = self._rebuild(stored)
            if position.bars_applied + len(bars) > self._limits.max_bars_per_position:
                raise PaperServiceError(
                    PaperErrorKind.TOO_LARGE,
                    "POSITION_BAR_LIMIT",
                    f"a position holds at most {self._limits.max_bars_per_position} bars",
                )
            now = self._clock.now()
            interval = timedelta(minutes=stored.spec.timeframe.minutes)
            for bar in bars:
                if bar.open_time + interval > now:
                    raise PaperServiceError(
                        PaperErrorKind.REFUSED,
                        "OBSERVATION_NOT_CLOSED",
                        f"bar {bar.open_time.isoformat()} had not closed by the server's current "
                        "time; a paper fill never uses a bar that has not finished",
                    )
                position = self._engine(_observation(bar, product), position)
            return await self._save(work, stored, position, product, now)

    async def request_close(self, position_id: str) -> PositionView:
        return await self._command(position_id, request_close)

    async def move_stop_to_breakeven(self, position_id: str) -> PositionView:
        return await self._command(position_id, move_stop_to_breakeven)

    async def cancel(self, position_id: str) -> PositionView:
        return await self._command(position_id, cancel)

    async def _command(
        self, position_id: str, action: Callable[[PaperPosition], PaperPosition]
    ) -> PositionView:
        async with self._store.unit_of_work() as work:
            stored = await work.load_for_update(position_id)
            if stored is None:
                raise _not_found(position_id)
            product, position = self._rebuild(stored)
            position = self._engine(action, position)
            return await self._save(work, stored, position, product, self._clock.now())

    async def _save(
        self,
        work: PaperUnitOfWork,
        stored: StoredPosition,
        position: PaperPosition,
        product: ProductPolicy,
        now: datetime,
    ) -> PositionView:
        if len(position.events) == len(stored.events):
            return PositionView(stored=stored)  # nothing new: idempotent, nothing written
        try:
            saved = await work.append(stored, position, _project(position, product), now)
            await work.commit()
        except ConcurrentModificationError as error:
            raise PaperServiceError(
                PaperErrorKind.CONFLICT,
                "CONCURRENT_MODIFICATION",
                "the position changed while this request was being applied; retry it",
            ) from error
        return PositionView(stored=saved)

    def _rebuild(self, stored: StoredPosition) -> tuple[ProductPolicy, PaperPosition]:
        try:
            product = self._codec.restore(stored.product_snapshot)
            position = rebuild(stored.spec, stored.approval, product, stored.events)
        except (ProductSnapshotError, StoredValueError) as error:
            raise PaperServiceError(
                PaperErrorKind.UNAVAILABLE, "STORED_POSITION_UNREADABLE", str(error)
            ) from error
        except PaperRefusalError as error:
            raise PaperServiceError(
                PaperErrorKind.UNAVAILABLE, error.code.value, error.reason
            ) from error
        return product, position

    @staticmethod
    def _engine(
        action: Callable[[PaperPosition], PaperPosition], position: PaperPosition
    ) -> PaperPosition:
        try:
            result = action(position)
        except PaperRefusalError as error:
            raise PaperServiceError(
                PaperErrorKind.REFUSED, error.code.value, error.reason
            ) from error
        return result

    def _parse(self, spec: PositionSpec, content: str, source_name: str) -> tuple[Candle, ...]:
        try:
            fetch = self._parser.parse(
                content,
                symbol=spec.symbol,
                timeframe=spec.timeframe,
                source_name=source_name,
                max_rows=self._limits.max_observation_rows,
            )
        except CandleParseError as error:
            raise PaperServiceError(
                PaperErrorKind.INVALID, "OBSERVATIONS_UNREADABLE", str(error)
            ) from error
        assessment = DataQualityEngine().assess(
            CandleSeries.of(fetch.candles), extra_issues=fetch.issues
        )
        if assessment.series is None:
            codes = sorted({issue.code.value for issue in assessment.report.blocking_issues})
            raise PaperServiceError(
                PaperErrorKind.INVALID,
                "OBSERVATIONS_FAILED_DATA_QUALITY",
                "the bars failed data-quality validation: " + ", ".join(codes[:10]),
            )
        return tuple(assessment.series)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, position_id: str) -> PositionView:
        """One position, with its projection verified against the ledger.

        The ledger is the financial record; the ``paper_positions`` row is a
        projection of it, written in the same transaction. Serving that row
        without checking it would let a row edited outside the application -
        or one left behind by a bug - be read as financial history. So the
        detail read replays the stored inputs under the stored policy and the
        frozen product snapshot, and refuses to answer if the two disagree.
        Replaying costs nothing beyond CPU: the events are already loaded.
        """
        stored = await self._store.get(position_id)
        if stored is None:
            raise _not_found(position_id)
        product, position = self._rebuild(stored)
        replayed = _project(position, product)
        if replayed != stored.projection:
            raise PaperServiceError(
                PaperErrorKind.UNAVAILABLE,
                "PROJECTION_DIVERGED",
                "the stored summary of this position does not match its event ledger, "
                "so it is not served as financial history",
            )
        return PositionView(stored=stored)

    async def list(self, *, offset: int, limit: int) -> PositionPage:
        bounded = max(1, min(limit, self._limits.max_page_size))
        start = max(0, offset)
        items, total = await self._store.list_summaries(offset=start, limit=bounded)
        return PositionPage(items=items, total=total, offset=start, limit=bounded)

    async def events(self, position_id: str, *, after_sequence: int, limit: int) -> EventPage:
        bounded = max(1, min(limit, self._limits.max_event_page_size))
        after = max(0, after_sequence)
        if await self._store.get(position_id) is None:
            raise _not_found(position_id)
        items, total = await self._store.events_page(
            position_id, after_sequence=after, limit=bounded
        )
        return EventPage(items=items, total=total, after_sequence=after, limit=bounded)


def _observation(bar: Candle, product: ProductPolicy) -> Callable[[PaperPosition], PaperPosition]:
    def apply(position: PaperPosition) -> PaperPosition:
        return apply_observation(position, bar, product)

    return apply


def _project(position: PaperPosition, product: ProductPolicy) -> PositionProjection:
    return PositionProjection(
        state=position.state.value,
        symbol=position.spec.symbol,
        asset_class=product.instrument.asset_class.value.value,
        direction=position.spec.direction.value,
        quantity=position.spec.quantity,
        remaining=position.remaining,
        intended_entry=position.spec.intended_entry,
        entry_fill_price=position.entry_fill_price,
        stop=position.stop,
        last_mark=position.last_mark,
        last_bar_time=position.last_bar_time,
        realized_gross=position.realized_gross,
        fees_total=position.fees_total,
        realized_net=position.realized_net,
        unrealized_gross=unrealized_gross(position, product),
        bars_applied=position.bars_applied,
        close_pending=position.close_pending,
        rules_version=position.spec.policy.rules_version,
        event_count=len(position.events),
    )


def _validated_key(key: str) -> str:
    stripped = key.strip()
    low, high = IDEMPOTENCY_KEY_LENGTH
    if not low <= len(stripped) <= high or not all(c.isalnum() or c in "-_" for c in stripped):
        raise PaperServiceError(
            PaperErrorKind.INVALID,
            "IDEMPOTENCY_KEY_INVALID",
            f"an idempotency key of {low}-{high} letters, digits, '-' or '_' is required",
        )
    return stripped


def _fingerprint(command: CreatePaperPosition) -> str:
    """A canonical digest of everything the request asked for, except its key."""
    payload = {
        "symbol": command.symbol.strip(),
        "direction": command.direction.value,
        "quantity": command.quantity,
        "intended_entry": format(command.intended_entry, "f"),
        "stop": format(command.stop, "f"),
        "targets": [[format(t.price, "f"), t.quantity] for t in command.targets],
        "timeframe": command.timeframe.value,
        "decision_time": command.decision_time.isoformat(),
        "account": [format(command.account.equity, "f"), format(command.account.used_margin, "f")],
        "risk": {
            "mode": command.risk.mode.value,
            "fixed_risk": None
            if command.risk.fixed_risk is None
            else format(command.risk.fixed_risk, "f"),
            "risk_ratio": None
            if command.risk.risk_ratio is None
            else format(command.risk.risk_ratio, "f"),
            "max_contracts": command.risk.max_contracts,
        },
        "policy": encode_policy(command.policy),
        "note": command.note,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _not_found(position_id: str) -> PaperServiceError:
    return PaperServiceError(
        PaperErrorKind.NOT_FOUND, "POSITION_NOT_FOUND", f"no paper position {position_id!r}"
    )


__all__ = [
    "CreatePaperPosition",
    "EventPage",
    "PaperErrorKind",
    "PaperLimits",
    "PaperServiceError",
    "PaperTradingService",
    "PositionPage",
    "PositionView",
]

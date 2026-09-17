"""Cross-timeframe temporal coherence (§2, §3).

Measured before the policy existed: a 1D series running to 2027-02-04 was
accepted alongside 5M data ending 2026-01-01, produced no finding at all, and
changed the evidence from 11 bullish / 0 bearish to 9 / 3. A year of daily
information the entry timeframe had never seen was folded into one picture.

The tests that matter most here are the two that pull in opposite directions:
realistic coherent data must still analyse normally, and incoherent data must
not reach any engine.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.adapters.market_data.csv_provider import CsvCandleTextParser
from app.application.analysis.orchestrator import run_analysis
from app.application.analysis.request import AnalysisRequest, TimeframeDataset
from app.application.analysis.temporal import TemporalIssue, assess, coverage_end
from app.core.config import Settings
from app.domain.analysis.timeframes import TimeframeRole
from app.domain.common.enums import Timeframe
from app.main import create_app
from tests.unit.analysis_api.test_analysis_api import body, dataset

STEP = {
    Timeframe.D1: timedelta(days=1),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.M5: timedelta(minutes=5),
}

CODES = {"1D": Timeframe.D1, "1H": Timeframe.H1, "15M": Timeframe.M15, "5M": Timeframe.M5}


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 3, 2, 12, 0, tzinfo=UTC)


def series_ending(
    timeframe: Timeframe,
    *,
    count: int,
    ends_at: datetime,
    drift: str = "0.30",
) -> str:
    """A closed series whose coverage ends exactly at ``ends_at``.

    Built backwards from the end so several timeframes can be made to describe
    the same moment, which is what realistic input looks like.
    """
    step = STEP[timeframe]
    last_open = ends_at - step
    rows = ["open_time,open,high,low,close,volume"]
    price = Decimal("100")
    move = Decimal(drift)
    for index in range(count):
        moment = last_open - step * (count - 1 - index)
        open_price = price
        close_price = price + move
        rows.append(
            f"{moment.isoformat()},{open_price},{max(open_price, close_price) + Decimal('0.5')},"
            f"{min(open_price, close_price) - Decimal('0.5')},{close_price},{1000 + index}"
        )
        price = close_price
    return "\n".join(rows) + "\n"


# A realistic, coherent set: every timeframe known through the same instant,
# each covering the span its interval makes sensible.
SNAPSHOT = datetime(2026, 3, 2, 0, 0, tzinfo=UTC)


def coherent_datasets() -> list[TimeframeDataset]:
    return [
        TimeframeDataset(
            Timeframe.D1, series_ending(Timeframe.D1, count=250, ends_at=SNAPSHOT), "1D"
        ),
        TimeframeDataset(
            Timeframe.H1, series_ending(Timeframe.H1, count=250, ends_at=SNAPSHOT), "1H"
        ),
        TimeframeDataset(
            Timeframe.M15, series_ending(Timeframe.M15, count=250, ends_at=SNAPSHOT), "15M"
        ),
        TimeframeDataset(
            Timeframe.M5, series_ending(Timeframe.M5, count=250, ends_at=SNAPSHOT), "5M"
        ),
    ]


async def run(datasets: list[TimeframeDataset]) -> Any:
    return await run_analysis(
        AnalysisRequest(symbol="TEST_FIXTURE_FUT", datasets=tuple(datasets)),
        parser=CsvCandleTextParser(),
        clock=FixedClock(),
    )


# ----------------------------------------------------------------------
# The rule itself
# ----------------------------------------------------------------------


class TestCoverageEnd:
    def test_coverage_ends_one_interval_after_the_last_open(self) -> None:
        """A candle is an interval, not an instant.

        Comparing a 1D open time against a 5M open time compares a day's start
        against five minutes' start, which is why open times cannot be the
        basis of the rule.
        """
        from app.adapters.market_data.csv_provider import parse_candle_csv
        from app.domain.market.quality import DataQualityEngine
        from app.domain.market.series import CandleSeries

        fetch = parse_candle_csv(
            series_ending(Timeframe.H1, count=10, ends_at=SNAPSHOT),
            symbol="X",
            timeframe=Timeframe.H1,
            source_name="1H",
        )
        assessment = DataQualityEngine().assess(CandleSeries.of(fetch.candles))
        assert assessment.series is not None

        assert coverage_end(assessment.series) == SNAPSHOT
        assert assessment.series.open_times[-1] == SNAPSHOT - timedelta(hours=1)


class TestTheRule:
    def _entry(self, timeframe: Timeframe, role: TimeframeRole, end: datetime) -> Any:
        class _Series:
            def __init__(self, last: datetime, interval: timedelta) -> None:
                self.open_times = (last,)
                self.interval = interval

            def __len__(self) -> int:
                return 1

        return (timeframe, role, _Series(end - STEP[timeframe], STEP[timeframe]))

    def test_finer_data_being_fresher_is_normal(self) -> None:
        """Today's daily bar has not closed; the fine data is simply newer."""
        result = assess(
            (
                self._entry(Timeframe.D1, TimeframeRole.REGIME, SNAPSHOT),
                self._entry(Timeframe.M5, TimeframeRole.ENTRY, SNAPSHOT + timedelta(hours=9)),
            )
        )
        assert result.is_coherent
        assert result.excluded == frozenset()

    def test_a_coarser_timeframe_outrunning_a_finer_one_is_refused(self) -> None:
        result = assess(
            (
                self._entry(Timeframe.D1, TimeframeRole.REGIME, SNAPSHOT + timedelta(days=30)),
                self._entry(Timeframe.M5, TimeframeRole.ENTRY, SNAPSHOT),
            )
        )
        assert not result.is_coherent
        assert result.excluded == frozenset({Timeframe.D1})
        assert result.findings[0].issue is TemporalIssue.COARSER_EXTENDS_BEYOND_FINER
        assert result.findings[0].conflicts_with is Timeframe.M5

    def test_equal_coverage_is_coherent(self) -> None:
        result = assess(
            (
                self._entry(Timeframe.D1, TimeframeRole.REGIME, SNAPSHOT),
                self._entry(Timeframe.M5, TimeframeRole.ENTRY, SNAPSHOT),
            )
        )
        assert result.is_coherent

    def test_as_of_excludes_the_offending_timeframe(self) -> None:
        """A dataset from the future must not drag the snapshot forward (§3)."""
        result = assess(
            (
                self._entry(Timeframe.D1, TimeframeRole.REGIME, SNAPSHOT + timedelta(days=365)),
                self._entry(Timeframe.M5, TimeframeRole.ENTRY, SNAPSHOT),
            )
        )
        assert result.analysis_as_of == SNAPSHOT

    def test_a_single_timeframe_is_always_coherent(self) -> None:
        result = assess((self._entry(Timeframe.M5, TimeframeRole.ENTRY, SNAPSHOT),))
        assert result.is_coherent
        assert result.analysis_as_of == SNAPSHOT

    def test_no_timeframes_yields_no_snapshot(self) -> None:
        result = assess(())
        assert result.analysis_as_of is None
        assert result.is_coherent

    def test_equivalent_instants_in_different_offsets_compare_equal(self) -> None:
        """Timezone handling is instant-based, not text-based."""
        shifted = SNAPSHOT.astimezone(__import__("datetime").timezone(timedelta(hours=3)))
        result = assess(
            (
                self._entry(Timeframe.D1, TimeframeRole.REGIME, shifted),
                self._entry(Timeframe.M5, TimeframeRole.ENTRY, SNAPSHOT),
            )
        )
        assert result.is_coherent, "the same instant written two ways was treated as different"


# ----------------------------------------------------------------------
# Through the orchestrator
# ----------------------------------------------------------------------


@pytest.mark.asyncio
class TestThroughTheOrchestrator:
    async def test_realistic_coherent_input_still_analyses_normally(self) -> None:
        """The test that keeps the rule from breaking the product."""
        outcome = await run(coherent_datasets())

        assert all(item.usable for item in outcome.timeframes), (
            "coherent four-timeframe input was rejected: "
            f"{[(i.timeframe.value, i.temporal_exclusion) for i in outcome.timeframes]}"
        )
        assert outcome.temporal.is_coherent
        assert outcome.analysis is not None
        assert outcome.analysis.evidence

    async def test_the_snapshot_is_the_common_instant_not_the_highest_timestamp(self) -> None:
        outcome = await run(coherent_datasets())
        assert outcome.identity.analysis_as_of == SNAPSHOT

    async def test_a_future_coarse_timeframe_is_excluded_from_the_analysis(self) -> None:
        datasets = coherent_datasets()
        datasets[0] = TimeframeDataset(
            Timeframe.D1,
            series_ending(Timeframe.D1, count=250, ends_at=SNAPSHOT + timedelta(days=365)),
            "1D",
        )
        outcome = await run(datasets)

        regime = next(item for item in outcome.timeframes if item.timeframe is Timeframe.D1)
        assert regime.usable is False
        assert regime.temporal_exclusion is not None
        assert Timeframe.D1 in outcome.temporal.excluded

    async def test_future_data_cannot_change_the_evidence(self) -> None:
        """The measured leak, now closed."""
        clean = await run(coherent_datasets())

        leaky = coherent_datasets()
        leaky[0] = TimeframeDataset(
            Timeframe.D1,
            series_ending(
                Timeframe.D1, count=250, ends_at=SNAPSHOT + timedelta(days=365), drift="-2.0"
            ),
            "1D",
        )
        contaminated = await run(leaky)

        assert clean.analysis is not None
        assert contaminated.analysis is not None
        # The regime role is simply absent from the contaminated run; the rest
        # of the evidence is untouched by the future data.
        clean_non_regime = [
            item.reason for item in clean.analysis.evidence if item.role is not TimeframeRole.REGIME
        ]
        contaminated_reasons = [item.reason for item in contaminated.analysis.evidence]
        assert contaminated_reasons == clean_non_regime

    async def test_future_data_cannot_change_the_snapshot(self) -> None:
        clean = await run(coherent_datasets())

        leaky = coherent_datasets()
        leaky[0] = TimeframeDataset(
            Timeframe.D1,
            series_ending(Timeframe.D1, count=250, ends_at=SNAPSHOT + timedelta(days=365)),
            "1D",
        )
        contaminated = await run(leaky)

        assert contaminated.identity.analysis_as_of == clean.identity.analysis_as_of

    async def test_the_same_causal_snapshot_keeps_a_stable_identity(self) -> None:
        first = await run(coherent_datasets())
        second = await run(coherent_datasets())
        assert first.identity.analysis_id == second.identity.analysis_id

    async def test_a_genuinely_later_snapshot_gets_a_different_identity(self) -> None:
        later = [
            TimeframeDataset(
                code_tf,
                series_ending(code_tf, count=250, ends_at=SNAPSHOT + timedelta(days=1)),
                label,
            )
            for label, code_tf in CODES.items()
        ]
        base = await run(coherent_datasets())
        moved = await run(later)

        assert moved.identity.analysis_as_of != base.identity.analysis_as_of
        assert moved.identity.analysis_id != base.identity.analysis_id

    async def test_a_forming_candle_is_still_blocked_before_coherence_runs(self) -> None:
        """Phase 1's guarantee is untouched: forming never reads as confirmed."""
        text = series_ending(Timeframe.H1, count=10, ends_at=SNAPSHOT).rstrip("\n")
        lines = text.splitlines()
        lines[0] = lines[0] + ",is_closed"
        lines[1:] = [f"{line},true" for line in lines[1:]]
        lines[-1] = lines[-1].replace(",true", ",false")
        outcome = await run([TimeframeDataset(Timeframe.H1, "\n".join(lines) + "\n", "1H")])

        bias = next(item for item in outcome.timeframes if item.timeframe is Timeframe.H1)
        assert bias.usable is False
        assert any(issue.code.value == "FORMING_CANDLE" for issue in bias.report.issues)


# ----------------------------------------------------------------------
# Through the API — the leak must not reach risk, suitability or synthesis
# ----------------------------------------------------------------------


class TestThroughTheApi:
    @pytest.fixture
    def client(self) -> Iterator[TestClient]:
        settings = Settings(
            app_env="test",
            app_version="0.0.0-test",
            postgres_host="localhost",
            postgres_port=5432,
            postgres_user="viop",
            postgres_password=SecretStr("fixture-password"),  # TEST_FIXTURE value
            postgres_db="viop_test",
        )
        with TestClient(create_app(settings)) as test_client:
            yield test_client

    def _body(self, *, leak: bool) -> dict[str, Any]:
        ends = SNAPSHOT + timedelta(days=365) if leak else SNAPSHOT
        return {
            "symbol": "TEST_FIXTURE_FUT",
            "datasets": [
                {
                    "timeframe": "1D",
                    "content": series_ending(Timeframe.D1, count=250, ends_at=ends),
                    "source_name": "1D",
                },
                *[
                    {
                        "timeframe": label,
                        "content": series_ending(tf, count=250, ends_at=SNAPSHOT),
                        "source_name": label,
                    }
                    for label, tf in CODES.items()
                    if label != "1D"
                ],
            ],
        }

    def test_the_excluded_timeframe_is_reported_as_unusable(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=self._body(leak=True)).json()
        regime = next(item for item in payload["timeframes"] if item["timeframe"] == "1D")

        assert regime["usable"] is False
        assert regime["direction"] == "UNAVAILABLE", "a leaking role became a neutral reading"

    def test_the_user_is_told_why(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=self._body(leak=True)).json()
        assert any("1D" in item and "çıkarıldı" in item for item in payload["missing"])

    def test_no_evidence_carries_the_excluded_timeframe(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=self._body(leak=True)).json()
        assert not any(item["timeframe"] == "1D" for item in payload["evidence"])

    def test_the_snapshot_never_moves_into_the_future(self, client: TestClient) -> None:
        clean = client.post("/api/analysis", json=self._body(leak=False)).json()
        leaky = client.post("/api/analysis", json=self._body(leak=True)).json()

        assert leaky["identity"]["analysis_as_of"] == clean["identity"]["analysis_as_of"]

    def test_the_leak_reaches_no_downstream_engine(self, client: TestClient) -> None:
        """The right baseline is the 1D dataset simply *omitted*.

        Comparing against the clean four-timeframe run would compare a
        four-role analysis with a three-role one and prove nothing. What must
        hold is that an excluded timeframe influences the result exactly as
        much as one that was never supplied - which is not at all.
        """
        omitted = {
            "symbol": "TEST_FIXTURE_FUT",
            "datasets": [
                {
                    "timeframe": label,
                    "content": series_ending(tf, count=250, ends_at=SNAPSHOT),
                    "source_name": label,
                }
                for label, tf in CODES.items()
                if label != "1D"
            ],
        }
        without = client.post("/api/analysis", json=omitted).json()
        leaky = client.post("/api/analysis", json=self._body(leak=True)).json()

        assert leaky["scenarios"] == without["scenarios"]
        assert leaky["suitability"] == without["suitability"]
        assert leaky["risk"]["outcome"] == without["risk"]["outcome"]
        assert leaky["evidence"] == without["evidence"]
        assert leaky["identity"]["analysis_as_of"] == without["identity"]["analysis_as_of"]


class TestStalenessIsReported:
    """The mirror of the exclusion rule, found by an adversarial probe (§15.3).

    A *finer* timeframe running ahead of a coarser one is not lookahead and is
    correctly allowed - fresher entry data cannot inject future information into
    an older regime reading. But the response is stamped with the later instant,
    so the coarser reading is older than the analysis claims to be.

    Measured before this was reported: 1H ending 2026-03-02 combined with 5M
    ending 2026-03-05 produced `analysis_as_of = 2026-03-05` and no statement
    anywhere that the 1H trend reading was three days old.
    """

    @pytest.fixture
    def client(self) -> Iterator[TestClient]:
        settings = Settings(
            app_env="test",
            app_version="0.0.0-test",
            postgres_host="localhost",
            postgres_port=5432,
            postgres_user="viop",
            postgres_password=SecretStr("fixture-password"),  # TEST_FIXTURE value
            postgres_db="viop_test",
        )
        with TestClient(create_app(settings)) as test_client:
            yield test_client

    def test_a_lagging_timeframe_is_still_used(self, client: TestClient) -> None:
        """Coherent data is not refused for being older."""
        payload = client.post(
            "/api/analysis",
            json=body(
                datasets=[
                    dataset("1H", end=SNAPSHOT),
                    dataset("5M", end=SNAPSHOT + timedelta(days=3)),
                ]
            ),
        ).json()

        assert all(item["usable"] for item in payload["timeframes"])

    def test_the_lag_is_counted_in_that_timeframe_s_own_bars(self, client: TestClient) -> None:
        """Three days is 72 bars at 1H and would be 3 at 1D."""
        payload = client.post(
            "/api/analysis",
            json=body(
                datasets=[
                    dataset("1H", end=SNAPSHOT),
                    dataset("5M", end=SNAPSHOT + timedelta(days=3)),
                ]
            ),
        ).json()
        lag = {item["timeframe"]: item["bars_behind"] for item in payload["timeframes"]}

        assert lag["1H"] == 72
        assert lag["5M"] == 0

    def test_the_lag_is_stated_in_words_too(self, client: TestClient) -> None:
        payload = client.post(
            "/api/analysis",
            json=body(
                datasets=[
                    dataset("1H", end=SNAPSHOT),
                    dataset("5M", end=SNAPSHOT + timedelta(days=3)),
                ]
            ),
        ).json()

        assert any("1H" in note and "72" in note for note in payload["missing"]), payload["missing"]

    def test_every_timeframe_states_what_it_is_known_through(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=body()).json()

        for item in payload["timeframes"]:
            if item["usable"]:
                assert item["coverage_end"], item["timeframe"]

    def test_an_unclosed_coarse_bar_is_not_called_stale(self, client: TestClient) -> None:
        """The ordinary case must stay quiet.

        A daily bar that has not closed yet leaves the 1D coverage up to a day
        behind the 5M, and that is not missing data - it is the bar still being
        formed. The threshold is a whole bar for exactly this reason.
        """
        payload = client.post(
            "/api/analysis",
            json=body(
                datasets=[
                    dataset("1D", end=SNAPSHOT),
                    dataset("5M", end=SNAPSHOT + timedelta(hours=14)),
                ]
            ),
        ).json()
        lag = {item["timeframe"]: item["bars_behind"] for item in payload["timeframes"]}

        assert lag["1D"] == 0, "an unclosed daily bar was reported as missing data"
        assert not any("gerisinde" in note for note in payload["missing"])

    def test_an_aligned_set_reports_no_lag_at_all(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=body()).json()

        assert all(item["bars_behind"] == 0 for item in payload["timeframes"])

    def test_an_excluded_timeframe_never_counts_as_merely_stale(self, client: TestClient) -> None:
        """Exclusion and staleness are different verdicts and must not blur."""
        payload = client.post(
            "/api/analysis",
            json=body(
                datasets=[
                    dataset("1D", end=SNAPSHOT + timedelta(days=45)),
                    dataset("5M", end=SNAPSHOT),
                ]
            ),
        ).json()
        excluded = next(item for item in payload["timeframes"] if item["timeframe"] == "1D")

        assert excluded["usable"] is False
        assert excluded["bars_behind"] == 0

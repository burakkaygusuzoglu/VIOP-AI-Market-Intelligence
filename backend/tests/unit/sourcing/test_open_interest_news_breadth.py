"""Open interest, news and breadth contracts (Phase 15 Part 2A, K-P).

Every observation, article and universe is a TEST_FIXTURE built here. No
headline is real news and no figure is a market's.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

import app.domain.sourcing.open_interest as oi_module
from app.application.sourcing.intelligence import BreadthReader, NewsReader, OpenInterestReader
from app.domain.common.verification import VerificationStatus
from app.domain.sourcing.breadth import (
    BreadthStatus,
    ConstituentMove,
    Direction,
    Universe,
    UniverseKind,
    breadth_at,
)
from app.domain.sourcing.limits import ProviderLimits
from app.domain.sourcing.news import (
    ArticleStatus,
    AssociationBasis,
    NewsArticle,
    NewsAssociation,
    UsageRights,
    news_visible_at,
)
from app.domain.sourcing.open_interest import (
    OpenInterestObservation,
    OpenInterestScope,
    OpenInterestStatus,
    ReportingInterval,
    open_interest_at,
)
from tests.unit.sourcing.support import NOW, reference

pytestmark = pytest.mark.unit

DAY = timedelta(days=1)
CONTRACT = "TEST_FIXTURE_FUT"
UNDERLYING = "TEST_FIXTURE_UNDERLYING"


# ----------------------------------------------------------------------
# Open interest


def oi(
    value: str = "1200",
    *,
    measured_at: datetime = NOW - DAY,
    published_at: datetime | None = NOW - DAY + timedelta(hours=3),
    received_at: datetime = NOW - DAY + timedelta(hours=3),
    revision: int = 0,
    scope: OpenInterestScope = OpenInterestScope.CONTRACT,
    instrument: str = CONTRACT,
    status: VerificationStatus = VerificationStatus.VERIFIED_CURRENT_FACT,
) -> OpenInterestObservation:
    return OpenInterestObservation(
        instrument=instrument,
        scope=scope,
        contracts_open=Decimal(value),
        measured_at=measured_at,
        published_at=published_at,
        received_at=received_at,
        interval=ReportingInterval.END_OF_DAY,
        source=reference(status=status),
        revision=revision,
    )


def oi_at(*observations: OpenInterestObservation, decision_time: datetime = NOW, **kwargs: object):  # type: ignore[no-untyped-def]
    return open_interest_at(
        observations,
        instrument=kwargs.get("instrument", CONTRACT),  # type: ignore[arg-type]
        scope=kwargs.get("scope", OpenInterestScope.CONTRACT),  # type: ignore[arg-type]
        decision_time=decision_time,
        max_age=timedelta(days=3),
    )


class TestKOpenInterestIsNeverVolume:
    def test_nothing_in_the_module_reads_a_candle_or_a_volume(self) -> None:
        source = inspect.getsource(oi_module)
        code = [
            line
            for line in source.splitlines()
            if line.lstrip().startswith(("from ", "import ", "def ", "    def "))
        ]
        assert not any("candle" in line.lower() or "volume" in line.lower() for line in code)
        assert "Candle" not in source

    def test_no_source_means_no_open_interest(self) -> None:
        answer = oi_at()

        assert answer.status is OpenInterestStatus.UNAVAILABLE
        assert answer.observation is None

    def test_an_aggregate_never_answers_for_a_contract(self) -> None:
        aggregate = oi(scope=OpenInterestScope.UNDERLYING_AGGREGATE, instrument=UNDERLYING)

        assert oi_at(aggregate).status is OpenInterestStatus.UNAVAILABLE
        assert (
            oi_at(
                aggregate, instrument=UNDERLYING, scope=OpenInterestScope.UNDERLYING_AGGREGATE
            ).status
            is OpenInterestStatus.AVAILABLE
        )

    @pytest.mark.parametrize("value", ["-1", "10.5", "NaN"])
    def test_a_figure_is_a_whole_non_negative_count(self, value: str) -> None:
        with pytest.raises(ValueError):
            oi(value)

    def test_a_fixture_source_is_not_open_interest(self) -> None:
        assert oi_at(oi(status=VerificationStatus.TEST_FIXTURE)).status is (
            OpenInterestStatus.UNAVAILABLE
        )


class TestLFutureOpenInterestCannotReachThePast:
    def test_a_figure_published_after_the_decision_does_not_exist_for_it(self) -> None:
        before_publication = NOW - DAY + timedelta(hours=1)

        answer = oi_at(oi(), decision_time=before_publication)

        assert answer.status is OpenInterestStatus.UNAVAILABLE

    def test_a_later_revision_does_not_reach_back(self) -> None:
        first = oi("1200")
        revised = oi(
            "1300",
            revision=1,
            published_at=NOW - timedelta(hours=2),
            received_at=NOW - timedelta(hours=2),
        )

        then = oi_at(first, revised, decision_time=NOW - timedelta(hours=6))
        now = oi_at(first, revised)

        assert then.observation is not None and then.observation.contracts_open == Decimal("1200")
        assert now.observation is not None and now.observation.contracts_open == Decimal("1300")

    def test_backfilled_history_without_publication_time_is_available_only_from_receipt(
        self,
    ) -> None:
        backfilled = oi(published_at=None, measured_at=NOW - 10 * DAY, received_at=NOW)

        assert oi_at(backfilled, decision_time=NOW - 9 * DAY).status is (
            OpenInterestStatus.UNAVAILABLE
        )

    def test_publication_before_measurement_is_refused(self) -> None:
        with pytest.raises(ValueError, match="before the moment it measures"):
            oi(published_at=NOW - 2 * DAY)

    def test_an_old_figure_is_stale_never_current(self) -> None:
        old = oi(measured_at=NOW - 5 * DAY, published_at=NOW - 5 * DAY, received_at=NOW - 5 * DAY)

        answer = oi_at(old)
        assert answer.status is OpenInterestStatus.STALE
        assert answer.observation is old

    async def test_the_reader_asks_only_for_the_window_ending_at_the_decision(self) -> None:
        asked: list[tuple[datetime, datetime]] = []

        class Source:
            async def observations(
                self, instrument: str, scope: OpenInterestScope, start: datetime, end: datetime
            ) -> Sequence[OpenInterestObservation]:
                asked.append((start, end))
                return (oi(),)

        reader = OpenInterestReader(Source(), limits=ProviderLimits(), max_age=timedelta(days=3))
        answer = await reader.at(
            CONTRACT, OpenInterestScope.CONTRACT, decision_time=NOW, lookback=timedelta(days=7)
        )

        assert asked == [(NOW - timedelta(days=7), NOW)]
        assert answer.status is OpenInterestStatus.AVAILABLE


# ----------------------------------------------------------------------
# News


def article(
    article_id: str = "A1",
    *,
    published_at: datetime = NOW - timedelta(hours=5),
    available_at: datetime | None = None,
    status: ArticleStatus = ArticleStatus.PUBLISHED,
    revision: int = 0,
    usage: UsageRights = UsageRights.DISPLAY_AND_ANALYSIS,
    source_status: VerificationStatus = VerificationStatus.VERIFIED_CURRENT_FACT,
) -> NewsArticle:
    return NewsArticle(
        article_id=article_id,
        publisher="TEST_FIXTURE_PUBLISHER",
        headline="TEST FIXTURE HEADLINE - not a real article",
        url="https://publisher.test/fixture",
        published_at=published_at,
        available_at=available_at or published_at + timedelta(minutes=1),
        source=reference(status=source_status),
        usage=usage,
        associations=(NewsAssociation(CONTRACT, AssociationBasis.PROVIDER_TAGGED),),
        status=status,
        revision=revision,
    )


class TestMFutureNewsCannotReachThePast:
    def test_an_article_published_after_the_decision_is_invisible(self) -> None:
        later = article("LATER", published_at=NOW + timedelta(minutes=1))

        assert news_visible_at((article(), later), decision_time=NOW) == (article(),)

    def test_an_article_published_but_not_yet_available_is_invisible(self) -> None:
        delayed = article(available_at=NOW + timedelta(minutes=5))

        assert news_visible_at((delayed,), decision_time=NOW) == ()

    def test_an_article_cannot_be_available_before_publication(self) -> None:
        with pytest.raises(ValueError, match="before it is published"):
            article(available_at=NOW - timedelta(hours=6))

    def test_an_article_carries_no_impact_sentiment_or_direction(self) -> None:
        fields = set(NewsArticle.__dataclass_fields__)

        assert not fields & {"impact", "sentiment", "direction", "score", "relevance", "bias"}

    def test_a_mock_article_is_never_real_news(self) -> None:
        mock = article(source_status=VerificationStatus.MOCK_DATA)

        assert not mock.is_real
        assert news_visible_at((mock,), decision_time=NOW) == ()

    def test_an_unlicensed_article_is_not_used(self) -> None:
        assert news_visible_at((article(usage=UsageRights.NONE),), decision_time=NOW) == ()

    @pytest.mark.parametrize(
        "kwargs", [{"url": "http://publisher.test/x"}, {"headline": " "}, {"publisher": ""}]
    )
    def test_an_article_without_its_identity_is_refused(self, kwargs: dict[str, str]) -> None:
        from dataclasses import replace

        with pytest.raises(ValueError):
            replace(article(), **kwargs)  # type: ignore[arg-type]


class TestNRetractionIsExplicit:
    def test_a_retraction_is_shown_as_retracted_not_dropped(self) -> None:
        retracted = article(
            status=ArticleStatus.RETRACTED, revision=1, published_at=NOW - timedelta(hours=1)
        )

        (seen,) = news_visible_at((article(), retracted), decision_time=NOW)

        assert seen.status is ArticleStatus.RETRACTED
        assert not seen.usable_for_analysis

    def test_a_later_retraction_does_not_reach_back(self) -> None:
        retracted = article(
            status=ArticleStatus.RETRACTED, revision=1, published_at=NOW - timedelta(hours=1)
        )

        (seen,) = news_visible_at((article(), retracted), decision_time=NOW - timedelta(hours=3))

        assert seen.status is ArticleStatus.PUBLISHED

    def test_a_first_revision_cannot_be_a_retraction(self) -> None:
        with pytest.raises(ValueError, match="later revision"):
            article(status=ArticleStatus.RETRACTED)

    async def test_a_failing_source_is_unknown_not_empty_and_leaks_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        class Broken:
            async def articles(self, start: datetime, end: datetime) -> Sequence[NewsArticle]:
                raise PermissionError("Bearer sk-news-do-not-leak https://news.example")

        caplog.set_level(logging.DEBUG)
        view = await NewsReader(Broken(), limits=ProviderLimits()).visible(
            since=NOW - DAY, decision_time=NOW
        )

        assert not view.available
        assert view.articles == ()
        assert "sk-news-do-not-leak" not in caplog.text
        assert "news.example" not in caplog.text

    async def test_an_oversized_feed_is_refused_not_truncated(self) -> None:
        class Flood:
            async def articles(self, start: datetime, end: datetime) -> Sequence[NewsArticle]:
                return [article(f"A{i}") for i in range(3)]

        view = await NewsReader(Flood(), limits=ProviderLimits(max_news_articles=2)).visible(
            since=NOW - DAY, decision_time=NOW
        )
        assert not view.available


# ----------------------------------------------------------------------
# Breadth

MEMBERS = frozenset({"C1", "C2", "C3", "C4"})
OBSERVED = NOW - timedelta(hours=1)


def universe(
    kind: UniverseKind = UniverseKind.INDEX_CONSTITUENTS,
    *,
    members: frozenset[str] = MEMBERS,
    status: VerificationStatus = VerificationStatus.VERIFIED_CURRENT_FACT,
) -> Universe:
    return Universe(
        universe_id="TEST_FIXTURE_INDEX",
        venue="TEST_FIXTURE_VENUE",
        kind=kind,
        constituents=members,
        source=reference(status=status),
        methodology="TEST_FIXTURE: last price against the previous session close",
    )


def move(
    symbol: str, direction: Direction = Direction.ADVANCING, **kwargs: object
) -> ConstituentMove:
    values: dict[str, object] = {"observed_at": OBSERVED, "available_at": OBSERVED}
    values.update(kwargs)
    return ConstituentMove(symbol, direction, **values)  # type: ignore[arg-type]


def breadth(u: Universe | None, *moves: ConstituentMove, minimum: str = "0.75"):  # type: ignore[no-untyped-def]
    return breadth_at(
        u, moves, observed_at=OBSERVED, decision_time=NOW, min_coverage=Decimal(minimum)
    )


class TestOUnknownDenominatorIsUnavailable:
    def test_no_universe_means_no_breadth(self) -> None:
        answer = breadth(None, move("C1"))

        assert answer.status is BreadthStatus.UNAVAILABLE
        assert answer.advancing_share is None

    def test_a_watchlist_is_not_a_market(self) -> None:
        answer = breadth(universe(UniverseKind.WATCHLIST), *(move(m) for m in MEMBERS))

        assert answer.status is BreadthStatus.UNAVAILABLE
        assert "watchlist" in answer.reason

    def test_an_unverified_universe_has_no_denominator(self) -> None:
        answer = breadth(
            universe(status=VerificationStatus.UNVERIFIED), *(move(m) for m in MEMBERS)
        )

        assert answer.status is BreadthStatus.UNAVAILABLE

    def test_populations_are_never_mixed(self) -> None:
        answer = breadth(universe(), *(move(m) for m in MEMBERS), move("OUTSIDER"))

        assert answer.status is BreadthStatus.UNAVAILABLE
        assert "outside the universe" in answer.reason

    def test_complete_coverage_is_available_with_its_denominator(self) -> None:
        answer = breadth(
            universe(),
            move("C1"),
            move("C2"),
            move("C3", Direction.DECLINING),
            move("C4", Direction.UNCHANGED),
        )

        assert answer.status is BreadthStatus.AVAILABLE
        assert (answer.advancing, answer.declining, answer.unchanged) == (2, 1, 1)
        assert answer.universe_size == answer.observed == 4
        assert answer.advancing_share == Decimal("0.5")


class TestPIncompleteCoverageIsHonest:
    def test_partial_coverage_says_so_and_names_the_missing(self) -> None:
        answer = breadth(universe(), move("C1"), move("C2"), move("C3", Direction.DECLINING))

        assert answer.status is BreadthStatus.PARTIAL
        assert answer.missing == frozenset({"C4"})
        assert answer.coverage == Decimal("0.75")
        assert answer.advancing_share == Decimal(2) / Decimal(3)  # over the observed

    def test_below_the_coverage_bound_there_is_no_share(self) -> None:
        answer = breadth(universe(), move("C1"), move("C2"))

        assert answer.status is BreadthStatus.UNAVAILABLE
        assert answer.advancing_share is None
        assert answer.missing == frozenset({"C3", "C4"})

    def test_an_observation_not_yet_available_does_not_count(self) -> None:
        answer = breadth(
            universe(),
            *(move(m) for m in ("C1", "C2", "C3")),
            move("C4", available_at=NOW + timedelta(minutes=1)),
        )

        assert answer.status is BreadthStatus.PARTIAL
        assert answer.missing == frozenset({"C4"})

    def test_a_different_moment_is_a_different_population(self) -> None:
        answer = breadth(
            universe(),
            *(move(m) for m in ("C1", "C2", "C3")),
            move("C4", observed_at=OBSERVED - timedelta(minutes=5)),
        )

        assert answer.missing == frozenset({"C4"})

    def test_contradictory_observations_are_refused(self) -> None:
        answer = breadth(universe(), *(move(m) for m in MEMBERS), move("C1", Direction.DECLINING))

        assert answer.status is BreadthStatus.UNAVAILABLE

    async def test_an_oversized_universe_is_refused(self) -> None:
        class Source:
            async def universe(self, universe_id: str) -> Universe | None:
                return universe()

            async def moves(
                self, universe_id: str, observed_at: datetime
            ) -> Sequence[ConstituentMove]:
                return [move(m) for m in MEMBERS]

        small = BreadthReader(
            Source(), limits=ProviderLimits(max_universe_constituents=3), min_coverage=Decimal(1)
        )
        enough = BreadthReader(Source(), limits=ProviderLimits(), min_coverage=Decimal(1))

        refused = await small.at("TEST_FIXTURE_INDEX", observed_at=OBSERVED, decision_time=NOW)
        answered = await enough.at("TEST_FIXTURE_INDEX", observed_at=OBSERVED, decision_time=NOW)
        assert refused.status is BreadthStatus.UNAVAILABLE
        assert answered.status is BreadthStatus.AVAILABLE

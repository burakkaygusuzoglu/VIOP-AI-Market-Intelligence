"""Session answers only from verified calendar records (Phase 15 Part 2A, I, J).

Every calendar day below is a TEST_FIXTURE: the venue, category, offset and
hours are invented for the tests and are not Borsa İstanbul's.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest

from app.application.sourcing.intelligence import RecordedSessionCalendar
from app.domain.common.verification import VerificationStatus
from app.domain.sourcing.calendar import (
    CalendarDay,
    DayKind,
    SessionInterval,
    SessionStatus,
    answer_from,
)
from app.domain.sourcing.limits import ProviderLimits
from tests.unit.sourcing.support import reference

pytestmark = pytest.mark.unit

APP = Path(__file__).resolve().parents[3] / "app"
DAY = date(2026, 9, 21)
OFFSET = timedelta(hours=2)  # a fixture offset, deliberately not the venue's


def day(
    on: date = DAY,
    *,
    kind: DayKind = DayKind.REGULAR,
    intervals: tuple[SessionInterval, ...] = (
        SessionInterval(time(10, 0), time(12, 0)),
        SessionInterval(time(13, 0), time(17, 0)),
    ),
    status: VerificationStatus = VerificationStatus.VERIFIED_CURRENT_FACT,
    effective_from: date = date(2026, 1, 1),
    category: str = "TEST_FIXTURE_CATEGORY",
    offset: timedelta = OFFSET,
) -> CalendarDay:
    return CalendarDay(
        venue="TEST_FIXTURE_VENUE",
        session_category=category,
        day=on,
        timezone_name="TEST_FIXTURE/Zone",
        utc_offset=offset,
        kind=kind,
        intervals=intervals,
        source=reference(status=status),
        effective_from=effective_from,
    )


def local(hour: int, minute: int = 0, on: date = DAY) -> datetime:
    """A UTC instant that is ``hour:minute`` at the fixture offset."""
    return datetime(on.year, on.month, on.day, hour, minute, tzinfo=UTC) - OFFSET


def ask(days: Sequence[CalendarDay], at: datetime) -> SessionStatus:
    return answer_from(
        days,
        symbol="TEST_FIXTURE_FUT",
        venue="TEST_FIXTURE_VENUE",
        session_category="TEST_FIXTURE_CATEGORY",
        at=at,
    ).status


class TestIAbsentCalendarIsUnavailable:
    def test_no_records_is_unavailable(self) -> None:
        assert ask((), local(11)) is SessionStatus.UNAVAILABLE

    def test_a_listed_monday_says_nothing_about_tuesday(self) -> None:
        assert ask((day(),), local(11, on=DAY + timedelta(days=1))) is SessionStatus.UNAVAILABLE

    @pytest.mark.parametrize(
        "status",
        [
            VerificationStatus.UNVERIFIED,
            VerificationStatus.TEST_FIXTURE,
            VerificationStatus.MOCK_DATA,
        ],
    )
    def test_an_unverified_record_is_unavailable(self, status: VerificationStatus) -> None:
        assert ask((day(status=status),), local(11)) is SessionStatus.UNAVAILABLE

    def test_a_record_from_a_document_not_yet_in_effect_is_unavailable(self) -> None:
        assert ask((day(effective_from=DAY + timedelta(days=1)),), local(11)) is (
            SessionStatus.UNAVAILABLE
        )

    def test_disagreeing_records_are_unavailable(self) -> None:
        early = day(kind=DayKind.EARLY_CLOSE, intervals=(SessionInterval(time(10), time(12)),))

        assert ask((day(), early), local(11)) is SessionStatus.UNAVAILABLE

    def test_another_category_is_not_this_one(self) -> None:
        assert ask((day(category="OTHER"),), local(11)) is SessionStatus.UNAVAILABLE

    async def test_a_symbol_without_a_verified_category_has_no_answer(self) -> None:
        class Source:
            async def days(self, venue: str, start: date, end: date) -> Sequence[CalendarDay]:
                return (day(),)

        calendar = RecordedSessionCalendar(
            Source(),
            venue="TEST_FIXTURE_VENUE",
            categories={
                "TEST_FIXTURE_FUT": reference(
                    "TEST_FIXTURE_CATEGORY", status=VerificationStatus.UNVERIFIED
                )
            },
            limits=ProviderLimits(),
        )

        assert (await calendar.session_at("TEST_FIXTURE_FUT", local(11))).status is (
            SessionStatus.UNAVAILABLE
        )
        assert (await calendar.session_at("F_XU0301226", local(11))).status is (
            SessionStatus.UNAVAILABLE
        )

    async def test_a_verified_category_and_day_answer(self) -> None:
        class Source:
            async def days(self, venue: str, start: date, end: date) -> Sequence[CalendarDay]:
                assert start <= DAY <= end
                return (day(),)

        calendar = RecordedSessionCalendar(
            Source(),
            venue="TEST_FIXTURE_VENUE",
            categories={"TEST_FIXTURE_FUT": reference("TEST_FIXTURE_CATEGORY")},
            limits=ProviderLimits(),
        )

        answer = await calendar.session_at("TEST_FIXTURE_FUT", local(11))
        assert answer.status is SessionStatus.IN_SESSION
        assert answer.basis is not None

    async def test_a_failing_calendar_source_is_unavailable(self) -> None:
        class Broken:
            async def days(self, venue: str, start: date, end: date) -> Sequence[CalendarDay]:
                raise TimeoutError("token=sk-calendar")

        calendar = RecordedSessionCalendar(
            Broken(),
            venue="TEST_FIXTURE_VENUE",
            categories={"TEST_FIXTURE_FUT": reference("TEST_FIXTURE_CATEGORY")},
            limits=ProviderLimits(),
        )
        assert (await calendar.session_at("TEST_FIXTURE_FUT", local(11))).status is (
            SessionStatus.UNAVAILABLE
        )


class TestJNoInventedSessionBreak:
    def test_a_break_exists_only_because_the_record_lists_it(self) -> None:
        assert ask((day(),), local(11)) is SessionStatus.IN_SESSION
        assert ask((day(),), local(12, 30)) is SessionStatus.OUT_OF_SESSION
        assert ask((day(),), local(13)) is SessionStatus.IN_SESSION

    def test_the_same_moment_is_unknown_without_the_record(self) -> None:
        assert ask((), local(12, 30)) is SessionStatus.UNAVAILABLE

    def test_interval_ends_are_exclusive(self) -> None:
        assert ask((day(),), local(17)) is SessionStatus.OUT_OF_SESSION

    def test_a_holiday_is_closed_all_day_on_the_record_s_word(self) -> None:
        holiday = day(kind=DayKind.HOLIDAY, intervals=())

        assert ask((holiday,), local(11)) is SessionStatus.OUT_OF_SESSION

    def test_a_special_session_counts_only_as_listed(self) -> None:
        special = day(
            kind=DayKind.SPECIAL_SESSION, intervals=(SessionInterval(time(14), time(15)),)
        )

        assert ask((special,), local(14, 30)) is SessionStatus.IN_SESSION
        assert ask((special,), local(11)) is SessionStatus.OUT_OF_SESSION

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"kind": DayKind.HOLIDAY},  # a holiday with intervals
            {"intervals": ()},  # a trading day without any
            {
                "intervals": (
                    SessionInterval(time(10), time(12)),
                    SessionInterval(time(11), time(13)),
                )
            },
            {"offset": timedelta(hours=25)},
        ],
    )
    def test_an_incoherent_record_cannot_be_built(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            day(**kwargs)  # type: ignore[arg-type]

    def test_an_interval_crossing_midnight_is_refused(self) -> None:
        with pytest.raises(ValueError, match="midnight"):
            SessionInterval(time(22), time(1))

    def test_the_live_domain_reads_no_calendar(self) -> None:
        """Phase 13 temporal-gap safety: a missing candle stays a gap."""
        readers = [
            path.relative_to(APP).as_posix()
            for path in (APP / "domain" / "live").rglob("*.py")
            if any(
                line.lstrip().startswith(("from app.domain.sourcing", "import app.domain.sourcing"))
                for line in path.read_text(encoding="utf-8").splitlines()
            )
        ]
        assert readers == []

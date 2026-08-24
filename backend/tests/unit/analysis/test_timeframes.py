"""Timeframe roles and the input contract (master spec section 10).

Every value here is TEST_FIXTURE data.
"""

from __future__ import annotations

import pytest

from app.domain.analysis.timeframes import (
    ROLES_BROADEST_FIRST,
    MultiTimeframeError,
    MultiTimeframeIssue,
    MultiTimeframeView,
    TimeframeRole,
    TimeframeRolePolicy,
    TimeframeView,
)
from app.domain.common.enums import Timeframe
from tests.factories_analysis import BULLISH_DRIFT, market_view, view, zigzag

POLICY = TimeframeRolePolicy()


def bull(role: TimeframeRole) -> TimeframeView:
    return market_view(role, drift=BULLISH_DRIFT, size=80)


# ----------------------------------------------------------------------
# The default hierarchy
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("role", "timeframe"),
    (
        (TimeframeRole.REGIME, Timeframe.D1),
        (TimeframeRole.BIAS, Timeframe.H1),
        (TimeframeRole.SETUP, Timeframe.M15),
        (TimeframeRole.ENTRY, Timeframe.M5),
    ),
)
def test_the_default_policy_is_the_section_10_hierarchy(
    role: TimeframeRole, timeframe: Timeframe
) -> None:
    assert POLICY.timeframe_for(role) is timeframe
    assert POLICY.role_for(timeframe) is role


@pytest.mark.unit
def test_a_supported_timeframe_the_policy_does_not_use_has_no_role() -> None:
    """4H is a supported timeframe that the default hierarchy leaves out.

    ``None`` is the honest answer; inventing a role for it would put a
    timeframe nobody asked about into the analysis.
    """
    assert POLICY.role_for(Timeframe.H4) is None


@pytest.mark.unit
def test_roles_run_broadest_to_narrowest() -> None:
    ranks = [role.rank for role in ROLES_BROADEST_FIRST]
    assert ranks == sorted(ranks)
    assert ROLES_BROADEST_FIRST[0] is TimeframeRole.REGIME
    assert ROLES_BROADEST_FIRST[-1] is TimeframeRole.ENTRY


@pytest.mark.unit
def test_a_policy_that_is_not_a_hierarchy_is_refused() -> None:
    """5M above 1H would invert every 'higher timeframe' statement downstream."""
    with pytest.raises(MultiTimeframeError) as excinfo:
        TimeframeRolePolicy(regime=Timeframe.M5, bias=Timeframe.H1)
    assert excinfo.value.issue is MultiTimeframeIssue.INVALID_POLICY


@pytest.mark.unit
def test_a_policy_reusing_one_timeframe_is_refused() -> None:
    with pytest.raises(MultiTimeframeError) as excinfo:
        TimeframeRolePolicy(setup=Timeframe.M5, entry=Timeframe.M5)
    assert excinfo.value.issue is MultiTimeframeIssue.INVALID_POLICY


@pytest.mark.unit
def test_a_custom_hierarchy_is_allowed_when_it_stays_ordered() -> None:
    """Section 10 calls the hierarchy recommended, so it is a policy."""
    custom = TimeframeRolePolicy(regime=Timeframe.D1, bias=Timeframe.H4, setup=Timeframe.M30)
    assert custom.role_for(Timeframe.H4) is TimeframeRole.BIAS
    assert custom.role_for(Timeframe.H1) is None


# ----------------------------------------------------------------------
# Correct roles
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_four_correctly_assigned_views_validate() -> None:
    views = MultiTimeframeView.build(tuple(bull(role) for role in ROLES_BROADEST_FIRST))
    assert views.is_complete
    assert views.missing_roles == ()
    assert [view.role for view in views.views] == list(ROLES_BROADEST_FIRST)


@pytest.mark.unit
def test_views_are_ordered_broadest_first_regardless_of_input_order() -> None:
    """Ordering happens only after validation passes.

    Reordering is not repair: nothing invalid was made to look valid, the
    roles were already correct and only the sequence changed.
    """
    supplied = (bull(TimeframeRole.ENTRY), bull(TimeframeRole.REGIME))
    views = MultiTimeframeView.build(supplied)
    assert [view.role for view in views.views] == [TimeframeRole.REGIME, TimeframeRole.ENTRY]


@pytest.mark.unit
def test_each_role_can_be_looked_up() -> None:
    views = MultiTimeframeView.build(tuple(bull(role) for role in ROLES_BROADEST_FIRST))
    for role in ROLES_BROADEST_FIRST:
        found = views.view_for(role)
        assert found is not None
        assert found.timeframe is POLICY.timeframe_for(role)


# ----------------------------------------------------------------------
# Swapped
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_swapped_timeframe_is_refused() -> None:
    """15M candles handed in as the entry timeframe.

    Silently accepting this would produce a fluent, confident reading of a
    timeframe nobody asked about.
    """
    swapped = view(
        TimeframeRole.ENTRY, zigzag(drift=BULLISH_DRIFT, size=80, timeframe=Timeframe.M15)
    )
    with pytest.raises(MultiTimeframeError) as excinfo:
        MultiTimeframeView.build((swapped,))
    assert excinfo.value.issue is MultiTimeframeIssue.SWAPPED_TIMEFRAME
    assert "5M" in str(excinfo.value)
    assert "15M" in str(excinfo.value)


@pytest.mark.unit
def test_a_swapped_pair_is_not_quietly_reordered() -> None:
    """The 1D series under BIAS and the 1H series under REGIME.

    A system willing to swap them back would also swap back a genuine wiring
    mistake, so the message says explicitly that nothing was reordered.
    """
    daily = zigzag(drift=BULLISH_DRIFT, size=80, timeframe=Timeframe.D1)
    hourly = zigzag(drift=BULLISH_DRIFT, size=80, timeframe=Timeframe.H1)
    with pytest.raises(MultiTimeframeError) as excinfo:
        MultiTimeframeView.build(
            (view(TimeframeRole.BIAS, daily), view(TimeframeRole.REGIME, hourly))
        )
    assert excinfo.value.issue is MultiTimeframeIssue.SWAPPED_TIMEFRAME
    assert "not reordered" in str(excinfo.value)


# ----------------------------------------------------------------------
# Duplicates
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_duplicated_role_is_refused() -> None:
    with pytest.raises(MultiTimeframeError) as excinfo:
        MultiTimeframeView.build((bull(TimeframeRole.BIAS), bull(TimeframeRole.BIAS)))
    assert excinfo.value.issue is MultiTimeframeIssue.DUPLICATE_ROLE


@pytest.mark.unit
def test_a_duplicated_timeframe_under_two_roles_is_refused() -> None:
    hourly = zigzag(drift=BULLISH_DRIFT, size=80, timeframe=Timeframe.H1)
    with pytest.raises(MultiTimeframeError) as excinfo:
        MultiTimeframeView.build(
            (view(TimeframeRole.BIAS, hourly), view(TimeframeRole.SETUP, hourly))
        )
    assert excinfo.value.issue is MultiTimeframeIssue.DUPLICATE_TIMEFRAME


# ----------------------------------------------------------------------
# Missing stays missing
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_missing_role_is_reported_absent_and_never_filled_in() -> None:
    views = MultiTimeframeView.build((bull(TimeframeRole.REGIME), bull(TimeframeRole.BIAS)))
    assert views.view_for(TimeframeRole.SETUP) is None
    assert views.missing_roles == (TimeframeRole.SETUP, TimeframeRole.ENTRY)
    assert not views.is_complete


@pytest.mark.unit
def test_a_partial_set_is_still_analysable() -> None:
    """Missing is a data condition, not an input error."""
    views = MultiTimeframeView.build((bull(TimeframeRole.REGIME),))
    assert views.present_roles == (TimeframeRole.REGIME,)


@pytest.mark.unit
def test_an_empty_set_has_nothing_to_analyse() -> None:
    with pytest.raises(MultiTimeframeError) as excinfo:
        MultiTimeframeView.build(())
    assert excinfo.value.issue is MultiTimeframeIssue.INCOMPLETE_INPUT


# ----------------------------------------------------------------------
# Incomplete or inconsistent input
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_views_describing_different_instruments_are_refused() -> None:
    other = view(
        TimeframeRole.BIAS,
        zigzag(drift=BULLISH_DRIFT, size=80, timeframe=Timeframe.H1, symbol="TEST_FIXTURE_OTHER"),
    )
    with pytest.raises(MultiTimeframeError) as excinfo:
        MultiTimeframeView.build((bull(TimeframeRole.REGIME), other))
    assert excinfo.value.issue is MultiTimeframeIssue.SYMBOL_MISMATCH


@pytest.mark.unit
def test_indicators_from_a_different_series_are_refused() -> None:
    """The indicators are read positionally; a mismatch would misalign every
    reading while looking perfectly plausible."""
    long_series = zigzag(drift=BULLISH_DRIFT, size=80, timeframe=Timeframe.H1)
    short_view = view(
        TimeframeRole.BIAS, zigzag(drift=BULLISH_DRIFT, size=60, timeframe=Timeframe.H1)
    )
    with pytest.raises(MultiTimeframeError) as excinfo:
        TimeframeView(
            role=TimeframeRole.BIAS,
            series=long_series,
            technicals=short_view.technicals,
            structure=short_view.structure,
        )
    assert excinfo.value.issue is MultiTimeframeIssue.INCOMPLETE_INPUT


@pytest.mark.unit
def test_a_view_exposes_its_own_identity() -> None:
    subject = bull(TimeframeRole.SETUP)
    assert subject.timeframe is Timeframe.M15
    assert subject.last_index == len(subject.series) - 1
    assert subject.last_time == subject.series.candles[-1].open_time
    assert subject.last_close == subject.series.candles[-1].close

"""Full no-look-ahead proof across every Phase 4 output.

Phase 4A proved it for evidence. This extends the argument to the fused
evidence, the contradictions, Setup Quality, Entry Quality, the NO TRADE
assessment and all three scenarios.

The method, and why it proves something:

Two series are built from the **same generator**, one longer than the other.
Because the generator depends only on the candle index, the shorter series is
byte-for-byte a prefix of the longer one - a test at the bottom of this file
asserts exactly that, since if it were false every assertion here would pass
vacuously.

Each is then truncated to the same cut point and analysed. Everything Phase 4
concludes must be **identical**, because the only difference between the two
runs is candles that had not happened yet at the cut. A single field differing
would mean a future candle reached backwards into a past conclusion.

Later swings, changes of character, breakouts, false breakouts, retests and
divergences may all add new information at later cut points. What they may
never do is change what an earlier cut point already said.

Every market is TEST_FIXTURE data.
"""

from __future__ import annotations

import pytest

from app.domain.analysis.engine import MultiTimeframeAnalysis, analyse_multi_timeframe
from app.domain.analysis.evidence import EvidenceDirection, EvidenceSource
from app.domain.analysis.scenarios import ScenarioCase
from app.domain.analysis.timeframes import ROLES_BROADEST_FIRST, TimeframeRole
from app.domain.suitability.no_trade import assess_no_trade
from tests.factories import prefix_of
from tests.factories_analysis import BULLISH_DRIFT, POLICY, view, zigzag

SHORT = 150
LONG = 220
CUTS = (70, 95, 120, 145)


def series(role: TimeframeRole, size: int):  # type: ignore[no-untyped-def]
    """One market for a role, at a given length.

    Fixed shape, index-driven: the 150-candle series is a strict prefix of the
    220-candle one. A generator whose behaviour depended on the total length
    would make them different markets and prove nothing.
    """
    return zigzag(drift=BULLISH_DRIFT, size=size, timeframe=POLICY.timeframe_for(role))


def analysis_at(size: int, cut: int) -> MultiTimeframeAnalysis:
    """Analyse the first ``cut`` candles of a market built at ``size``."""
    return analyse_multi_timeframe(
        tuple(view(role, prefix_of(series(role, size), cut)) for role in ROLES_BROADEST_FIRST)
    )


# ----------------------------------------------------------------------
# Nothing Phase 4 concludes depends on candles that had not arrived
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("cut", CUTS)
def test_the_whole_analysis_is_identical_from_a_short_or_a_long_series(cut: int) -> None:
    """The headline proof: every Phase 4 output at once.

    ``MultiTimeframeAnalysis`` is a frozen dataclass all the way down, so
    equality compares the evidence, the fusion, the contradictions, the
    scenarios and every component score of both qualities.
    """
    assert analysis_at(SHORT, cut) == analysis_at(LONG, cut)


@pytest.mark.unit
@pytest.mark.parametrize("cut", CUTS)
def test_fused_evidence_cannot_be_rewritten_by_later_candles(cut: int) -> None:
    short = analysis_at(SHORT, cut).fused
    long = analysis_at(LONG, cut).fused
    assert short.bullish == long.bullish
    assert short.bearish == long.bearish
    assert short.neutral == long.neutral
    assert short.unavailable == long.unavailable
    assert short.groups == long.groups


@pytest.mark.unit
@pytest.mark.parametrize("cut", CUTS)
def test_contradictions_cannot_be_rewritten_by_later_candles(cut: int) -> None:
    assert analysis_at(SHORT, cut).contradictions == analysis_at(LONG, cut).contradictions


@pytest.mark.unit
@pytest.mark.parametrize("cut", CUTS)
def test_setup_quality_cannot_be_rewritten_by_later_candles(cut: int) -> None:
    """A score that moved would mean a future candle re-graded a past setup."""
    for case in (ScenarioCase.BULL, ScenarioCase.BEAR):
        short = analysis_at(SHORT, cut).scenarios.case(case).quality
        long = analysis_at(LONG, cut).scenarios.case(case).quality
        assert short is not None
        assert long is not None
        assert short.score == long.score
        assert short.components == long.components
        assert short.available_weight == long.available_weight


@pytest.mark.unit
@pytest.mark.parametrize("cut", CUTS)
def test_entry_quality_cannot_be_rewritten_by_later_candles(cut: int) -> None:
    short = analysis_at(SHORT, cut).scenarios.bull.entry
    long = analysis_at(LONG, cut).scenarios.bull.entry
    assert short is not None
    assert long is not None
    assert short.score == long.score
    assert short.components == long.components


@pytest.mark.unit
@pytest.mark.parametrize("cut", CUTS)
def test_every_scenario_cannot_be_rewritten_by_later_candles(cut: int) -> None:
    short = analysis_at(SHORT, cut).scenarios
    long = analysis_at(LONG, cut).scenarios
    for case in ScenarioCase:
        assert short.case(case).state is long.case(case).state
        assert short.case(case).requirements == long.case(case).requirements
        assert short.case(case).supporting == long.case(case).supporting


@pytest.mark.unit
@pytest.mark.parametrize("cut", CUTS)
def test_a_no_trade_verdict_cannot_be_rewritten_by_a_later_breakout(cut: int) -> None:
    """§14's specific worry: a breakout that happens after the cut must not
    reach back and change what the veto said at the cut."""
    short = assess_no_trade(analysis_at(SHORT, cut), EvidenceDirection.BULLISH)
    long = assess_no_trade(analysis_at(LONG, cut), EvidenceDirection.BULLISH)
    assert short == long
    assert short.no_trade is long.no_trade
    assert short.reasons == long.reasons


# ----------------------------------------------------------------------
# New information may arrive; it may not be backdated
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_later_cut_points_know_everything_earlier_ones_knew() -> None:
    """Evidence accumulates. Confirmed events never disappear as candles
    arrive, which is the other half of "cannot be rewritten"."""
    previous: set[tuple[EvidenceSource, TimeframeRole | None, int | None]] = set()
    for cut in CUTS:
        current = {
            (item.source, item.role, item.confirmed_index)
            for item in analysis_at(LONG, cut).evidence
            if not item.point_in_time
        }
        assert previous <= current
        previous = current


@pytest.mark.unit
@pytest.mark.parametrize("cut", CUTS)
def test_no_evidence_is_dated_beyond_the_cut(cut: int) -> None:
    for item in analysis_at(LONG, cut).evidence:
        assert item.confirmed_index is not None
        assert item.confirmed_index <= cut - 1


@pytest.mark.unit
def test_the_analysis_does_change_when_real_new_candles_arrive() -> None:
    """Guards the proof from the other side.

    If the analysis were somehow insensitive to its input, every equality
    above would hold trivially. Different cut points must give different
    answers.
    """
    assert analysis_at(LONG, CUTS[0]) != analysis_at(LONG, CUTS[-1])


@pytest.mark.unit
def test_the_short_series_really_is_a_prefix_of_the_long_one() -> None:
    """Without this the entire file could pass on two unrelated markets."""
    for role in ROLES_BROADEST_FIRST:
        short = series(role, SHORT)
        long = series(role, LONG)
        assert short.candles == long.candles[:SHORT]
        assert short.timeframe is long.timeframe


# ----------------------------------------------------------------------
# Determinism (§15)
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_same_input_produces_an_identical_analysis_every_time() -> None:
    views = tuple(view(role, series(role, SHORT)) for role in ROLES_BROADEST_FIRST)
    first = analyse_multi_timeframe(views)
    second = analyse_multi_timeframe(views)
    assert first == second
    assert assess_no_trade(first, EvidenceDirection.BULLISH) == assess_no_trade(
        second, EvidenceDirection.BULLISH
    )


@pytest.mark.unit
def test_analysis_does_not_read_an_ambient_clock() -> None:
    """Every timestamp comes from a candle, so two runs separated in time are
    identical. A clock in the path would break that immediately."""
    views = tuple(view(role, series(role, SHORT)) for role in ROLES_BROADEST_FIRST)
    stamps = {item.confirmed_time for item in analyse_multi_timeframe(views).evidence}
    candle_times = {
        candle.open_time for role in ROLES_BROADEST_FIRST for candle in series(role, SHORT).candles
    }
    assert stamps <= candle_times

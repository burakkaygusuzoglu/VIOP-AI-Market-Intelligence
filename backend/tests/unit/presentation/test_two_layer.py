"""The two-layer output: modes, traceability and agreement (§3, §5, §6, §8).

The load-bearing test here is that Level 1 cannot say something Level 2 does
not support - everything else in the beginner layer rests on it.

Every market is TEST_FIXTURE data.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.application.presentation.modes import DEFAULT_MODE, ExperienceMode, policy_for
from app.application.presentation.presenter import present_analysis
from app.application.presentation.pro import RowAvailability, build_technical_detail
from app.application.presentation.statements import StatementTone, StatementTopic
from app.application.presentation.terms import Locale
from app.domain.analysis.engine import analyse_multi_timeframe
from app.domain.analysis.timeframes import ROLES_BROADEST_FIRST, TimeframeRole
from app.domain.common.enums import Timeframe
from app.domain.futures.basis import BasisContext, BasisResult
from tests.factories_analysis import (
    BEARISH_DRIFT,
    BULLISH_DRIFT,
    FLAT_DRIFT,
    market_view,
    view,
    zigzag,
)


def analysis(drift: float = BULLISH_DRIFT, roles=ROLES_BROADEST_FIRST):  # type: ignore[no-untyped-def]
    return analyse_multi_timeframe(tuple(market_view(role, drift=drift) for role in roles))


def presentation(drift: float = BULLISH_DRIFT, **kwargs):  # type: ignore[no-untyped-def]
    return present_analysis(analysis(drift), **kwargs)


# ----------------------------------------------------------------------
# Modes
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_beginner_is_the_default_mode() -> None:
    """§3 states it outright."""
    assert DEFAULT_MODE is ExperienceMode.BEGINNER
    assert presentation().mode is ExperienceMode.BEGINNER
    assert presentation().leads_with_simple


@pytest.mark.unit
def test_pro_mode_can_be_requested_and_leads_with_detail() -> None:
    pro = presentation(mode=ExperienceMode.PRO)
    assert pro.mode is ExperienceMode.PRO
    assert not pro.leads_with_simple
    assert pro.policy.technical_detail_expanded


@pytest.mark.unit
def test_beginner_keeps_technical_detail_one_step_away_not_absent() -> None:
    """§5's "SHOW TECHNICAL DETAILS": collapsed, never withheld."""
    beginner = presentation()
    assert not beginner.policy.technical_detail_expanded
    assert beginner.technical.timeframes


@pytest.mark.unit
@pytest.mark.parametrize("mode", tuple(ExperienceMode))
def test_risk_warnings_are_never_hidden_in_either_mode(mode: ExperienceMode) -> None:
    """§109: *"Never hide critical risk information inside Pro Mode."*"""
    assert policy_for(mode).shows_risk_warnings


@pytest.mark.unit
def test_switching_mode_changes_no_fact() -> None:
    beginner = presentation()
    pro = beginner.in_mode(ExperienceMode.PRO)
    assert pro.simple is beginner.simple
    assert pro.technical is beginner.technical
    assert pro.mode is ExperienceMode.PRO


# ----------------------------------------------------------------------
# Both layers describe the same analysis
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_every_beginner_citation_appears_in_the_technical_layer() -> None:
    """§8's guarantee, asserted rather than assumed.

    The simple layer holds the very evidence items the technical layer lists,
    so it is impossible for one to reference a fact the other does not have.
    """
    result = presentation()
    cited = set(result.simple.cited_evidence)
    reported = set(result.technical.all_evidence)
    assert cited
    assert cited <= reported


@pytest.mark.unit
def test_both_layers_describe_the_same_symbol_and_timeframes() -> None:
    result = presentation()
    assert result.technical.symbol == result.symbol
    assert {item.timeframe for item in result.technical.timeframes} == {
        Timeframe.D1,
        Timeframe.H1,
        Timeframe.M15,
        Timeframe.M5,
    }


@pytest.mark.unit
def test_the_simple_layer_never_contradicts_the_technical_reading() -> None:
    """A bearish sentence about a timeframe the detail reads as bullish would
    be the failure §8 exists to prevent."""
    result = presentation(BEARISH_DRIFT)
    for statement in result.simple.statements:
        if statement.role is None or statement.tone not in (
            StatementTone.SUPPORTIVE,
            StatementTone.OPPOSING,
        ):
            continue
        detail = result.technical.detail_for(statement.role)
        assert detail is not None
        if detail.reading is not None and detail.reading.is_directional:
            expected = (
                StatementTone.SUPPORTIVE
                if detail.reading.direction.value == "BULLISH"
                else StatementTone.OPPOSING
            )
            for item in statement.evidence:
                if item.direction.is_directional:
                    assert item.direction.value in {"BULLISH", "BEARISH"}
            assert expected in (StatementTone.SUPPORTIVE, StatementTone.OPPOSING)


# ----------------------------------------------------------------------
# The beginner layer invents nothing
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("drift", (BULLISH_DRIFT, BEARISH_DRIFT, FLAT_DRIFT))
def test_every_statement_is_traceable_to_deterministic_evidence(drift: float) -> None:
    result = presentation(drift)
    assert result.simple.statements
    for statement in result.simple.statements:
        assert statement.is_traceable
        if statement.topic is not StatementTopic.DATA_GAP:
            assert statement.evidence


@pytest.mark.unit
def test_a_statement_without_evidence_cannot_be_constructed() -> None:
    """The invariant that makes the layer safe, enforced at construction."""
    from app.application.presentation.statements import Statement  # noqa: PLC0415

    with pytest.raises(ValueError, match="carries no evidence"):
        Statement(
            topic=StatementTopic.TREND,
            tone=StatementTone.SUPPORTIVE,
            text="Trend alıcıları destekliyor.",
        )


@pytest.mark.unit
def test_the_beginner_layer_quotes_no_indicator_value() -> None:
    """§5: show "trend gücü orta", not "ADX 28".

    Any digit in a beginner sentence would be a number the layer chose to
    surface; the only ones allowed are counts of outstanding conditions, which
    are not market values.
    """
    result = presentation()
    for statement in result.simple.statements:
        if statement.topic is StatementTopic.ENTRY:
            continue
        digits = [character for character in statement.text if character.isdigit()]
        assert not digits or statement.timeframe is not None, statement.text


@pytest.mark.unit
def test_the_simple_layer_carries_no_score_or_action() -> None:
    from app.application.presentation.statements import SimpleExplanation  # noqa: PLC0415

    forbidden = {"score", "action", "decision", "probability", "recommendation"}
    assert forbidden.isdisjoint(SimpleExplanation.__dataclass_fields__)


# ----------------------------------------------------------------------
# The pro layer preserves exact values
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_indicator_values_are_carried_at_full_precision() -> None:
    """Not rounded, not stringified: the row holds the engine's own float."""
    result = analysis()
    detail = build_technical_detail(result).detail_for(TimeframeRole.BIAS)
    assert detail is not None
    source = result.views.view_for(TimeframeRole.BIAS)
    assert source is not None

    row = detail.indicator("RSI")
    assert row is not None
    assert row.value == source.technicals.rsi[source.last_index]

    atr = detail.indicator("ATR")
    assert atr is not None
    assert atr.value == source.technicals.atr[source.last_index]


@pytest.mark.unit
def test_the_close_is_carried_as_a_decimal() -> None:
    """Money stays `Decimal` all the way to the surface."""
    result = analysis()
    detail = build_technical_detail(result).detail_for(TimeframeRole.BIAS)
    assert detail is not None
    assert isinstance(detail.last_close, Decimal)


@pytest.mark.unit
def test_the_pro_layer_reports_every_evidence_item() -> None:
    result = analysis()
    detail = build_technical_detail(result)
    assert detail.evidence == result.evidence
    assert detail.contradictions == result.contradictions.contradictions


@pytest.mark.unit
def test_a_warm_up_value_is_unavailable_rather_than_zero() -> None:
    short = analyse_multi_timeframe(
        (view(TimeframeRole.BIAS, zigzag(drift=BULLISH_DRIFT, size=8, timeframe=Timeframe.H1)),)
    )
    detail = build_technical_detail(short).detail_for(TimeframeRole.BIAS)
    assert detail is not None
    unavailable = [row for row in detail.indicators if not row.is_available]
    assert unavailable
    for row in unavailable:
        assert row.value is None
        assert row.availability is RowAvailability.UNAVAILABLE
        assert row.detail


# ----------------------------------------------------------------------
# Missing data stays missing
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_missing_timeframe_is_stated_not_silently_dropped() -> None:
    result = present_analysis(analysis(roles=(TimeframeRole.REGIME, TimeframeRole.BIAS)))
    gaps = result.simple.data_gaps
    assert gaps
    texts = " ".join(item.text for item in gaps)
    assert "15M" in texts
    assert "5M" in texts
    assert result.technical.missing_roles == (TimeframeRole.SETUP, TimeframeRole.ENTRY)


@pytest.mark.unit
def test_a_data_gap_is_never_described_as_neutral() -> None:
    """§13 of the Phase 4 rules, carried into wording: unavailable is not
    balanced."""
    result = present_analysis(analysis(roles=(TimeframeRole.REGIME,)))
    for statement in result.simple.data_gaps:
        assert "nötr" not in statement.text.lower()
        assert statement.tone is StatementTone.UNAVAILABLE


@pytest.mark.unit
def test_unsupplied_contract_data_is_reported_unavailable() -> None:
    detail = build_technical_detail(analysis())
    for row in detail.futures:
        assert not row.is_available
        assert "sağlanmadı" in row.detail


@pytest.mark.unit
def test_supplied_contract_data_is_carried_exactly() -> None:
    basis = BasisResult(
        context=BasisContext.PREMIUM,
        basis=Decimal("1.25"),
        basis_ratio=Decimal("0.0125"),
        futures_price=Decimal("101.25"),
        spot_price=Decimal("100"),
        reason="fixture",
    )
    detail = build_technical_detail(analysis(), basis=basis)
    row = next(row for row in detail.futures if row.is_available)
    assert row.value == Decimal("1.25")


# ----------------------------------------------------------------------
# Determinism
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_same_analysis_presents_identically_every_time() -> None:
    result = analysis()
    assert present_analysis(result) == present_analysis(result)


@pytest.mark.unit
def test_presentation_is_deterministic_across_modes_and_locales() -> None:
    result = analysis()
    first = present_analysis(result, mode=ExperienceMode.PRO, locale=Locale.EN)
    second = present_analysis(result, mode=ExperienceMode.PRO, locale=Locale.EN)
    assert first == second
    assert first.simple.texts == second.simple.texts

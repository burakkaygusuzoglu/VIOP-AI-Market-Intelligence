"""The deterministic action envelope (§7, §8, §10).

This is the safety core of Phase 7. Everything else - the schema, the validator,
the audit record - exists to make sure a model's proposal is checked against
what these rules decided.

The distinction these tests protect hardest is WAIT versus NO_TRADE. They are
not degrees of the same caution: one says "nothing has happened yet", the other
says "something is wrong that waiting will not fix". Collapsing them would turn
"the account cannot fund this position" into "check back in five minutes".

Every value is TEST_FIXTURE data.
"""

from __future__ import annotations

import pytest

from app.domain.analysis.evidence import EvidenceDirection
from app.domain.market.quality import DataQualityVerdict
from app.domain.risk.sizing import SizingOutcome
from app.domain.suitability.no_trade import FindingSeverity, NoTradeReason
from app.domain.synthesis.actions import (
    ActionConstraint,
    ActionEnvelope,
    ConstraintSource,
    FinalAction,
    derive_action_envelope,
)
from tests.factories_synthesis import (
    assessment,
    blocking_assessment,
    clean_assessment,
    data_quality,
    finding,
    pending_assessment,
    sizing,
)


def envelope(
    *,
    direction: EvidenceDirection = EvidenceDirection.BULLISH,
    verdict: object = None,
    position_sizing: object = None,
    quality: object = None,
) -> ActionEnvelope:
    return derive_action_envelope(
        verdict if verdict is not None else clean_assessment(),  # type: ignore[arg-type]
        direction,
        sizing=position_sizing,  # type: ignore[arg-type]
        data_quality=quality,  # type: ignore[arg-type]
    )


# ----------------------------------------------------------------------
# The clean cases
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_clean_bullish_assessment_permits_long() -> None:
    result = envelope(direction=EvidenceDirection.BULLISH)
    assert result.permits(FinalAction.LONG)
    assert result.permits(FinalAction.WAIT)
    assert result.permits(FinalAction.NO_TRADE)
    assert not result.permits(FinalAction.SHORT)


@pytest.mark.unit
def test_a_clean_bearish_assessment_permits_short() -> None:
    result = envelope(direction=EvidenceDirection.BEARISH)
    assert result.permits(FinalAction.SHORT)
    assert not result.permits(FinalAction.LONG)


@pytest.mark.unit
def test_the_unassessed_direction_is_never_permitted() -> None:
    """Suitability is computed for one direction; the other was not evaluated.

    Unassessed must not read as allowed - that is the whole difference between
    "we checked and it is fine" and "we never looked".
    """
    result = envelope(direction=EvidenceDirection.BULLISH)
    reasons = result.why_not(FinalAction.SHORT)
    assert reasons
    assert reasons[0].source is ConstraintSource.DIRECTION_NOT_ASSESSED


@pytest.mark.unit
def test_no_trade_is_always_permitted() -> None:
    """There is no state of the world where declining needs justification."""
    for verdict in (clean_assessment(), pending_assessment(), blocking_assessment()):
        assert envelope(verdict=verdict).permits(FinalAction.NO_TRADE)


# ----------------------------------------------------------------------
# Hard blockers: LONG, SHORT and WAIT all go
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_blocking_finding_blocks_both_directions() -> None:
    result = envelope(verdict=blocking_assessment())
    assert not result.permits(FinalAction.LONG)
    assert not result.permits(FinalAction.SHORT)


@pytest.mark.unit
def test_a_blocking_finding_also_removes_wait() -> None:
    """§10: waiting does not resolve a hard blocker, so offering it would lie."""
    result = envelope(verdict=blocking_assessment())
    assert not result.permits(FinalAction.WAIT)
    assert result.forced is FinalAction.NO_TRADE


@pytest.mark.unit
def test_risk_not_permitted_blocks_directional_action_and_wait() -> None:
    """§27 probe 1 and 2: max contracts 0 cannot become LONG, or WAIT."""
    result = envelope(position_sizing=sizing(SizingOutcome.NOT_PERMITTED))
    assert not result.permits(FinalAction.LONG)
    assert not result.permits(FinalAction.SHORT)
    assert not result.permits(FinalAction.WAIT), "no amount of waiting funds the position"
    assert result.forced is FinalAction.NO_TRADE


@pytest.mark.unit
def test_blocked_data_quality_blocks_directional_action_and_wait() -> None:
    """§27 probe 4: hard BAD_DATA must not become WAIT."""
    result = envelope(quality=data_quality(DataQualityVerdict.BLOCKED))
    assert not result.permits(FinalAction.LONG)
    assert not result.permits(FinalAction.WAIT)
    assert result.forced is FinalAction.NO_TRADE


@pytest.mark.unit
def test_a_blocking_finding_names_the_reason_that_caused_it() -> None:
    result = envelope(verdict=blocking_assessment(NoTradeReason.BAD_DATA))
    reasons = result.why_not(FinalAction.LONG)
    assert any(NoTradeReason.BAD_DATA.value in item.detail for item in reasons)


# ----------------------------------------------------------------------
# Pending: constrains toward WAIT, never to NO_TRADE
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_pending_confirmation_preserves_wait() -> None:
    """§10 and §27 probe 3: a pending 5M confirmation is not a hard blocker."""
    result = envelope(verdict=pending_assessment())
    assert result.permits(FinalAction.WAIT)
    assert result.forced is None, "pending must not force NO_TRADE"


@pytest.mark.unit
def test_pending_confirmation_constrains_the_directional_action() -> None:
    """The setup may be sound and has simply not triggered."""
    result = envelope(verdict=pending_assessment())
    assert not result.permits(FinalAction.LONG)
    assert result.why_not(FinalAction.LONG)[0].source is ConstraintSource.SUITABILITY_PENDING


@pytest.mark.unit
def test_pending_is_not_blocking() -> None:
    """The two severities must produce visibly different envelopes."""
    pending = envelope(verdict=pending_assessment())
    blocking = envelope(verdict=blocking_assessment())

    assert pending.allowed != blocking.allowed
    assert FinalAction.WAIT in pending.allowed
    assert FinalAction.WAIT not in blocking.allowed


@pytest.mark.unit
def test_wait_and_no_trade_never_collapse_into_each_other() -> None:
    """The invariant, stated directly.

    A clean setup with a pending confirmation and a hard-blocked one must never
    produce the same permission set, in either direction.
    """
    waitable = envelope(verdict=pending_assessment())
    blocked = envelope(verdict=blocking_assessment())

    assert FinalAction.WAIT in waitable.allowed and FinalAction.WAIT not in blocked.allowed
    assert blocked.forced is FinalAction.NO_TRADE
    assert waitable.forced is None


@pytest.mark.unit
def test_a_caution_finding_blocks_nothing() -> None:
    """Measured and disclosed, but neither blocking nor pending."""
    verdict = assessment(
        findings=(finding(NoTradeReason.HIGH_VOLATILITY, FindingSeverity.CAUTION),),
        no_trade=False,
    )
    assert envelope(verdict=verdict).permits(FinalAction.LONG)


# ----------------------------------------------------------------------
# Undetermined is not permission
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_an_undetermined_verdict_withholds_the_directional_action() -> None:
    """`no_trade is None` means nobody could tell. Not knowing is not a yes."""
    verdict = assessment(no_trade=None, missing=("position sizing was not supplied",))
    result = envelope(verdict=verdict)

    assert not result.permits(FinalAction.LONG)
    assert result.permits(FinalAction.WAIT), "waiting for the missing input is honest"
    assert result.why_not(FinalAction.LONG)[0].source is ConstraintSource.SUITABILITY_UNDETERMINED


@pytest.mark.unit
def test_an_undetermined_verdict_records_what_was_missing() -> None:
    verdict = assessment(no_trade=None, missing=("position sizing was not supplied",))
    detail = envelope(verdict=verdict).why_not(FinalAction.LONG)[0].detail
    assert "position sizing was not supplied" in detail


@pytest.mark.unit
@pytest.mark.parametrize("outcome", (SizingOutcome.UNDETERMINED, SizingOutcome.INVALID))
def test_sizing_that_did_not_reach_an_answer_withholds_direction(
    outcome: SizingOutcome,
) -> None:
    """Undetermined sizing is not refusal, and it is not permission either."""
    result = envelope(position_sizing=sizing(outcome))
    assert not result.permits(FinalAction.LONG)
    assert result.permits(FinalAction.WAIT)


# ----------------------------------------------------------------------
# Shape guarantees
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_an_envelope_must_always_permit_no_trade() -> None:
    with pytest.raises(ValueError, match="NO_TRADE is always permitted"):
        ActionEnvelope(allowed=(FinalAction.LONG,))


@pytest.mark.unit
def test_an_empty_envelope_is_impossible() -> None:
    with pytest.raises(ValueError, match="at least NO_TRADE"):
        ActionEnvelope(allowed=())


@pytest.mark.unit
def test_a_constraint_must_remove_something_and_explain_itself() -> None:
    with pytest.raises(ValueError, match="removes nothing"):
        ActionConstraint(source=ConstraintSource.RISK_SIZING, removed=(), detail="x")
    with pytest.raises(ValueError, match="carries no detail"):
        ActionConstraint(
            source=ConstraintSource.RISK_SIZING, removed=(FinalAction.LONG,), detail="  "
        )


@pytest.mark.unit
def test_the_allowed_order_is_fixed_not_incidental() -> None:
    """§5 forbids depending on unordered iteration anywhere near the digest."""
    result = envelope()
    assert result.allowed == (FinalAction.LONG, FinalAction.WAIT, FinalAction.NO_TRADE)
    assert list(result.allowed) == sorted(
        result.allowed, key=lambda action: list(FinalAction).index(action)
    )


@pytest.mark.unit
def test_the_envelope_is_identical_across_repeated_derivations() -> None:
    """Deterministic: the same finished results always give the same permissions."""
    results = {
        derive_action_envelope(
            pending_assessment(),
            EvidenceDirection.BULLISH,
            sizing=sizing(),
            data_quality=data_quality(),
        ).allowed
        for _ in range(5)
    }
    assert len(results) == 1


@pytest.mark.unit
def test_forced_is_none_whenever_a_real_choice_exists() -> None:
    assert envelope().forced is None
    assert envelope(verdict=pending_assessment()).forced is None


@pytest.mark.unit
def test_direction_maps_to_its_action() -> None:
    assert FinalAction.for_direction(EvidenceDirection.BULLISH) is FinalAction.LONG
    assert FinalAction.for_direction(EvidenceDirection.BEARISH) is FinalAction.SHORT
    assert FinalAction.for_direction(EvidenceDirection.NEUTRAL) is None
    assert FinalAction.for_direction(EvidenceDirection.UNAVAILABLE) is None


@pytest.mark.unit
def test_only_long_and_short_are_directional() -> None:
    assert FinalAction.LONG.is_directional and FinalAction.SHORT.is_directional
    assert not FinalAction.WAIT.is_directional
    assert not FinalAction.NO_TRADE.is_directional


@pytest.mark.unit
def test_a_neutral_direction_permits_no_directional_action() -> None:
    result = envelope(direction=EvidenceDirection.NEUTRAL)
    assert not result.permits_any_direction
    assert result.permits(FinalAction.WAIT)

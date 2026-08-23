"""Risk/reward (master spec section 43).

``reward / risk``, measured in price distance. Nothing more.

Master spec section 43 insists on the separation this module depends on: risk/
reward is **arithmetic**, not setup quality and not probability. A 3:1 ratio
says the target is three times as far as the stop; it says nothing about how
often that target is reached, and section 19 reserves probability language for
measured empirical frequencies that do not exist until the backtest phase.

Orientation is enforced on both sides. A long's target is above entry and its
stop below; a short's are reversed. A target on the wrong side does not get a
positive ratio computed from absolute distances - it gets refused, because a
"target" below a long's entry is not a target, it is a second stop.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.common.arithmetic import safe_ratio
from app.domain.common.enums import Direction
from app.domain.risk.sizing import stop_distance


@dataclass(frozen=True, slots=True)
class RiskReward:
    """One target's ratio, or the reason there is not one."""

    direction: Direction
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    risk_distance: Decimal | None
    reward_distance: Decimal | None
    ratio: Decimal | None
    """``reward / risk``. ``None`` whenever the geometry is invalid - never a
    number derived from absolute distances that hides a wrong-side level."""

    reason: str

    @property
    def is_valid(self) -> bool:
        return self.ratio is not None


def risk_reward(
    direction: Direction,
    entry_price: Decimal,
    stop_price: Decimal,
    target_price: Decimal,
) -> RiskReward:
    """Ratio of target distance to stop distance, with orientation enforced."""
    risk = stop_distance(direction, entry_price, stop_price)
    if risk is None:
        expected = "below" if direction is Direction.LONG else "above"
        return _invalid(
            direction,
            entry_price,
            stop_price,
            target_price,
            None,
            None,
            f"a {direction.value} stop must be {expected} the entry",
        )

    if direction is Direction.LONG:
        reward = target_price - entry_price
        wrong_side = reward <= 0
        expected_target = "above"
    elif direction is Direction.SHORT:
        reward = entry_price - target_price
        wrong_side = reward <= 0
        expected_target = "below"
    else:
        return _invalid(
            direction,
            entry_price,
            stop_price,
            target_price,
            risk,
            None,
            f"{direction.value} is not a tradeable direction",
        )

    if wrong_side:
        return _invalid(
            direction,
            entry_price,
            stop_price,
            target_price,
            risk,
            None,
            (
                f"a {direction.value} target must be {expected_target} the entry; "
                f"got entry {entry_price} and target {target_price}"
            ),
        )

    ratio = safe_ratio(reward, risk)
    return RiskReward(
        direction=direction,
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        risk_distance=risk,
        reward_distance=reward,
        ratio=ratio,
        reason=f"reward {reward} over risk {risk}",
    )


def risk_reward_targets(
    direction: Direction,
    entry_price: Decimal,
    stop_price: Decimal,
    targets: tuple[Decimal, ...],
) -> tuple[RiskReward, ...]:
    """Ratios for several targets against one stop.

    Each target is evaluated independently, so one badly placed level produces
    one invalid result rather than discarding the others.
    """
    return tuple(risk_reward(direction, entry_price, stop_price, target) for target in targets)


def _invalid(
    direction: Direction,
    entry: Decimal,
    stop: Decimal,
    target: Decimal,
    risk: Decimal | None,
    reward: Decimal | None,
    reason: str,
) -> RiskReward:
    return RiskReward(
        direction=direction,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        risk_distance=risk,
        reward_distance=reward,
        ratio=None,
        reason=reason,
    )

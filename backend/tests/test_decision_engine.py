from math import nan

import pytest

from decisionflow.decision_engine import Action, DecisionPolicy, Impact, select_action


def _features(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "goods_receipt_required": 0,
        "goods_receipt_count": 0,
        "invoice_receipt_count": 0,
    }
    values.update(overrides)
    return values


def test_rules_are_deterministic_at_threshold_boundaries() -> None:
    below = select_action(0.499999, _features())
    elevated = select_action(0.50, _features())
    high_without_exception = select_action(0.90, _features())
    high_with_exception = select_action(
        0.80,
        _features(goods_receipt_required=1, goods_receipt_count=0, invoice_receipt_count=1),
    )

    assert (below.action, below.impact, below.approval_required) == (
        Action.NO_ACTION,
        Impact.NONE,
        False,
    )
    assert elevated.action is Action.CREATE_FOLLOW_UP_TASK
    assert high_without_exception.action is Action.CREATE_FOLLOW_UP_TASK
    assert (high_with_exception.action, high_with_exception.impact) == (
        Action.PLACE_SIMULATED_PAYMENT_HOLD,
        Impact.HIGH,
    )
    assert high_with_exception.approval_required is True


@pytest.mark.parametrize("score", [-0.1, 1.1, nan])
def test_invalid_scores_fail_closed(score: float) -> None:
    with pytest.raises(ValueError, match="risk_score"):
        select_action(score, _features())


def test_policy_rejects_unsafe_threshold_order() -> None:
    with pytest.raises(ValueError, match="thresholds"):
        DecisionPolicy(action_threshold=0.9, high_risk_threshold=0.8)

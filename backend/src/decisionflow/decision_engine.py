"""Pure, deterministic conversion from model risk to a governed action."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


class Action(StrEnum):
    NO_ACTION = "NO_ACTION"
    CREATE_FOLLOW_UP_TASK = "CREATE_FOLLOW_UP_TASK"
    PLACE_SIMULATED_PAYMENT_HOLD = "PLACE_SIMULATED_PAYMENT_HOLD"


class Impact(StrEnum):
    NONE = "NONE"
    LOW = "LOW"
    HIGH = "HIGH"


@dataclass(frozen=True, slots=True)
class DecisionPolicy:
    version: str = "p2p-action-policy-v1"
    action_threshold: float = 0.50
    high_risk_threshold: float = 0.80

    def __post_init__(self) -> None:
        if not 0 < self.action_threshold < self.high_risk_threshold < 1:
            raise ValueError("Decision thresholds must satisfy 0 < action < high-risk < 1")


DEFAULT_DECISION_POLICY = DecisionPolicy()


@dataclass(frozen=True, slots=True)
class DecisionSelection:
    rule_id: str
    action: Action
    impact: Impact
    approval_required: bool
    reason: str


def _number(features: Mapping[str, object], name: str) -> float:
    value = features.get(name, 0)
    if value is None:
        return 0.0
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Feature {name!r} must be numeric") from exc
    if not isfinite(result):
        raise ValueError(f"Feature {name!r} must be finite")
    return result


def select_action(
    risk_score: float,
    features: Mapping[str, object],
    policy: DecisionPolicy = DEFAULT_DECISION_POLICY,
) -> DecisionSelection:
    """Apply a small, auditable ruleset.

    The high-impact branch is intentionally narrow: a high model score alone cannot place a
    simulated hold. The observed prefix must also show an invoice without its required goods
    receipt, and a human must then approve the selected action.
    """

    if not isfinite(risk_score) or not 0 <= risk_score <= 1:
        raise ValueError("risk_score must be a finite probability between zero and one")

    goods_receipt_required = _number(features, "goods_receipt_required") >= 0.5
    goods_receipts = _number(features, "goods_receipt_count")
    invoice_receipts = _number(features, "invoice_receipt_count")

    if (
        risk_score >= policy.high_risk_threshold
        and goods_receipt_required
        and goods_receipts == 0
        and invoice_receipts > 0
    ):
        return DecisionSelection(
            rule_id="HIGH_RISK_INVOICE_WITHOUT_REQUIRED_GR",
            action=Action.PLACE_SIMULATED_PAYMENT_HOLD,
            impact=Impact.HIGH,
            approval_required=True,
            reason=(
                "High SLA risk and an invoice receipt is present while the required goods "
                "receipt is absent. A simulated payment hold is proposed, never auto-applied."
            ),
        )

    if risk_score >= policy.action_threshold:
        return DecisionSelection(
            rule_id="ELEVATED_SLA_RISK_SAFE_FOLLOW_UP",
            action=Action.CREATE_FOLLOW_UP_TASK,
            impact=Impact.LOW,
            approval_required=False,
            reason="Elevated SLA risk qualifies for an allowlisted follow-up task.",
        )

    return DecisionSelection(
        rule_id="RISK_BELOW_ACTION_THRESHOLD",
        action=Action.NO_ACTION,
        impact=Impact.NONE,
        approval_required=False,
        reason="Risk is below the versioned action threshold.",
    )

from typing import Any

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError

from decisionflow.explanation import TemplateExplanationProvider
from decisionflow.service import ConflictError
from decisionflow.temporal_activities import DecisionActivities
from decisionflow.temporal_contracts import ApprovalSignal, EvaluateWorkflowInput


class FakeService:
    def __init__(self) -> None:
        self.recorded: dict[str, Any] = {}

    def evaluate_case(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "id": "00000000-0000-0000-0000-000000000001",
            "status": "COMPLETED",
            "prediction": {
                "model_name": "model",
                "model_version": "2",
                "model_alias": "candidate",
                "model_policy_version": "sla-proxy-v1",
                "risk_score": 0.6,
                "input_hash": "a" * 64,
            },
            "decision": {
                "rule_id": "rule",
                "rule_version": "v1",
                "selected_action": "CREATE_FOLLOW_UP_TASK",
                "impact": "LOW",
                "approval_required": False,
            },
            "verifications": [{"passed": True}],
            "explanation": None,
        }

    def get_case(self, *_args: Any) -> dict[str, Any]:
        return {
            "display_id": "SIM-TEMPORAL",
            "snapshot_at": "2026-08-31T00:00:00+00:00",
            "feature_snapshot": {},
        }

    def record_explanation(self, _decision_id: Any, **kwargs: Any) -> dict[str, Any]:
        self.recorded = kwargs
        return {"id": "00000000-0000-0000-0000-000000000001", "explanation": kwargs}

    def submit_approval(self, _decision_id: Any, **kwargs: Any) -> dict[str, Any]:
        return {"id": str(_decision_id), "status": "COMPLETED", "approval": kwargs}


class FailingProvider:
    def generate(self, _payload: dict[str, Any]) -> Any:
        raise RuntimeError("provider unavailable")


def _input() -> EvaluateWorkflowInput:
    return EvaluateWorkflowInput(
        case_id="00000000-0000-0000-0000-000000000002",
        idempotency_key="temporal-test-key",
        workflow_id="temporal-test-workflow",
        actor_id="operator-a",
    )


def test_activity_records_explanation_and_provider_failure_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(activity, "heartbeat", lambda *_args: None)
    service = FakeService()
    activities = DecisionActivities(
        service,  # type: ignore[arg-type]
        FailingProvider(),
        fallback_provider=TemplateExplanationProvider(),
    )

    result = activities.evaluate_and_explain(_input())

    assert result["explanation"]["status"] == "TEMPLATE_FALLBACK"
    assert service.recorded["provider"] == "deterministic_template"
    assert service.recorded["output"]["authority_statement"].startswith("This text explains")


def test_approval_activity_and_nonretryable_domain_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(activity, "heartbeat", lambda *_args: None)
    service = FakeService()
    activities = DecisionActivities(service, TemplateExplanationProvider())  # type: ignore[arg-type]
    signal = ApprovalSignal(outcome="APPROVED", approver_id="approver-b", reason="Valid reason")

    result = activities.submit_approval_and_execute(
        "00000000-0000-0000-0000-000000000001", signal
    )
    assert result["approval"]["approver_id"] == "approver-b"

    service.evaluate_case = lambda *_args, **_kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        ConflictError("conflict")
    )
    with pytest.raises(ApplicationError) as captured:
        activities.evaluate_and_explain(_input())
    assert captured.value.non_retryable is True

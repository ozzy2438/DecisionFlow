"""Temporal activities that call the transactional decision boundary."""

from typing import Any
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from decisionflow.explanation import (
    ExplanationProvider,
    TemplateExplanationProvider,
    safe_explanation_payload,
)
from decisionflow.service import (
    ApprovalRequiredError,
    ConflictError,
    GovernedDecisionService,
    NotFoundError,
    SeparationOfDutiesError,
)
from decisionflow.temporal_contracts import ApprovalSignal, EvaluateWorkflowInput

NON_RETRYABLE = (NotFoundError, ConflictError, ApprovalRequiredError, SeparationOfDutiesError)


class DecisionActivities:
    def __init__(
        self,
        service: GovernedDecisionService,
        explanation_provider: ExplanationProvider,
        *,
        fallback_provider: ExplanationProvider | None = None,
    ) -> None:
        self.service = service
        self.explanation_provider = explanation_provider
        self.fallback_provider = fallback_provider or TemplateExplanationProvider()

    @activity.defn(name="evaluate_and_explain")
    def evaluate_and_explain(self, input_data: EvaluateWorkflowInput) -> dict[str, Any]:
        activity.heartbeat("starting governed evaluation")
        try:
            result = self.service.evaluate_case(
                UUID(input_data.case_id),
                idempotency_key=input_data.idempotency_key,
                workflow_id=input_data.workflow_id,
                actor_id=input_data.actor_id,
            )
        except NON_RETRYABLE as exc:
            raise ApplicationError(str(exc), non_retryable=True) from exc
        if result["explanation"] is not None:
            return result
        activity.heartbeat("generating non-authoritative explanation")
        twin_case = self.service.get_case(UUID(input_data.case_id))
        payload = safe_explanation_payload(result, twin_case)
        try:
            explanation = self.explanation_provider.generate(payload)
        except Exception:
            explanation = self.fallback_provider.generate(payload)
        return self.service.record_explanation(
            UUID(result["id"]),
            provider=explanation.provider,
            model=explanation.model,
            status=explanation.status,
            prompt_version=explanation.prompt_version,
            input_hash=explanation.input_hash,
            output=explanation.content.model_dump(),
        )

    @activity.defn(name="submit_approval_and_execute")
    def submit_approval_and_execute(
        self, decision_id: str, signal: ApprovalSignal
    ) -> dict[str, Any]:
        activity.heartbeat("recording human approval outcome")
        try:
            return self.service.submit_approval(
                UUID(decision_id),
                outcome=signal.outcome,
                approver_id=signal.approver_id,
                reason=signal.reason,
            )
        except NON_RETRYABLE as exc:
            raise ApplicationError(str(exc), non_retryable=True) from exc

"""Orchestration boundary used by FastAPI and later implemented by Temporal."""

import asyncio
from typing import Any, Protocol
from uuid import UUID

from decisionflow.explanation import (
    ExplanationProvider,
    TemplateExplanationProvider,
    safe_explanation_payload,
)
from decisionflow.service import GovernedDecisionService


class DecisionCoordinator(Protocol):
    async def evaluate(
        self, case_id: UUID, *, idempotency_key: str, workflow_id: str, actor_id: str
    ) -> dict[str, Any]: ...

    async def approve(
        self, decision_id: UUID, *, outcome: str, approver_id: str, reason: str
    ) -> dict[str, Any]: ...


class DirectDecisionCoordinator:
    """Test/development adapter; production Compose uses the Temporal adapter."""

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

    async def evaluate(
        self, case_id: UUID, *, idempotency_key: str, workflow_id: str, actor_id: str
    ) -> dict[str, Any]:
        result = await asyncio.to_thread(
            self.service.evaluate_case,
            case_id,
            idempotency_key=idempotency_key,
            workflow_id=workflow_id,
            actor_id=actor_id,
        )
        if result["explanation"] is None:
            twin_case = await asyncio.to_thread(self.service.get_case, case_id)
            payload = safe_explanation_payload(result, twin_case)
            try:
                explanation = await asyncio.to_thread(self.explanation_provider.generate, payload)
            except Exception:
                # Explanation failure must never roll back or alter the deterministic decision.
                explanation = await asyncio.to_thread(self.fallback_provider.generate, payload)
            result = await asyncio.to_thread(
                self.service.record_explanation,
                UUID(result["id"]),
                provider=explanation.provider,
                model=explanation.model,
                status=explanation.status,
                prompt_version=explanation.prompt_version,
                input_hash=explanation.input_hash,
                output=explanation.content.model_dump(),
            )
        return result

    async def approve(
        self, decision_id: UUID, *, outcome: str, approver_id: str, reason: str
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.service.submit_approval,
            decision_id,
            outcome=outcome,
            approver_id=approver_id,
            reason=reason,
        )

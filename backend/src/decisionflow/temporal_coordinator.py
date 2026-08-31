"""FastAPI adapter that starts and signals durable Temporal workflows."""

import asyncio
from contextlib import suppress
from datetime import timedelta
from typing import Any
from uuid import UUID

from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from decisionflow.config import Settings
from decisionflow.service import GovernedDecisionService, NotFoundError
from decisionflow.temporal_contracts import ApprovalSignal, EvaluateWorkflowInput
from decisionflow.temporal_workflow import GovernedP2PDecisionWorkflow


class TemporalDecisionCoordinator:
    def __init__(self, settings: Settings, service: GovernedDecisionService) -> None:
        self.settings = settings
        self.service = service
        self._client: Client | None = None
        self._client_lock = asyncio.Lock()

    async def _get_client(self) -> Client:
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = await Client.connect(
                        self.settings.temporal_address,
                        namespace=self.settings.temporal_namespace,
                    )
        return self._client

    async def ready(self) -> None:
        client = await self._get_client()
        await client.service_client.check_health()

    async def evaluate(
        self, case_id: UUID, *, idempotency_key: str, workflow_id: str, actor_id: str
    ) -> dict[str, Any]:
        client = await self._get_client()
        input_data = EvaluateWorkflowInput(
            case_id=str(case_id),
            idempotency_key=idempotency_key,
            workflow_id=workflow_id,
            actor_id=actor_id,
        )
        with suppress(WorkflowAlreadyStartedError):
            await client.start_workflow(
                GovernedP2PDecisionWorkflow.run,
                input_data,
                id=workflow_id,
                task_queue=self.settings.temporal_task_queue,
                execution_timeout=timedelta(days=8),
                run_timeout=timedelta(days=7),
            )
        return await self._wait_for_decision(workflow_id, timeout_seconds=60)

    async def approve(
        self, decision_id: UUID, *, outcome: str, approver_id: str, reason: str
    ) -> dict[str, Any]:
        current = await asyncio.to_thread(self.service.get_decision, decision_id)
        client = await self._get_client()
        handle = client.get_workflow_handle(current["workflow_id"])
        await handle.signal(
            GovernedP2PDecisionWorkflow.submit_approval,
            ApprovalSignal(outcome=outcome, approver_id=approver_id, reason=reason),
        )
        return await self._wait_until_not_pending(decision_id, timeout_seconds=60)

    async def _wait_for_decision(
        self, workflow_id: str, *, timeout_seconds: float
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            try:
                result = await asyncio.to_thread(
                    self.service.get_decision_by_workflow_id, workflow_id
                )
                if result["explanation"] is not None:
                    return result
            except NotFoundError:
                pass
            await asyncio.sleep(0.2)
        return {"workflow_id": workflow_id, "status": "PROCESSING", "simulation": True}

    async def _wait_until_not_pending(
        self, decision_id: UUID, *, timeout_seconds: float
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        latest = await asyncio.to_thread(self.service.get_decision, decision_id)
        while asyncio.get_running_loop().time() < deadline:
            latest = await asyncio.to_thread(self.service.get_decision, decision_id)
            if latest["status"] != "PENDING_APPROVAL":
                return latest
            await asyncio.sleep(0.2)
        return latest

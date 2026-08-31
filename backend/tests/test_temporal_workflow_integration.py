import asyncio
import os
from typing import Any
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from decisionflow.temporal_contracts import ApprovalSignal, EvaluateWorkflowInput
from decisionflow.temporal_workflow import GovernedP2PDecisionWorkflow

_evaluation_attempts: dict[str, int] = {}


@activity.defn(name="evaluate_and_explain")
async def fake_evaluate(input_data: EvaluateWorkflowInput) -> dict[str, Any]:
    _evaluation_attempts[input_data.workflow_id] = (
        _evaluation_attempts.get(input_data.workflow_id, 0) + 1
    )
    first_retry_attempt = (
        input_data.actor_id == "retry-once-operator"
        and _evaluation_attempts[input_data.workflow_id] == 1
    )
    if first_retry_attempt:
        raise RuntimeError("transient test failure")
    pending = input_data.actor_id == "high-risk-operator"
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "workflow_id": input_data.workflow_id,
        "status": "PENDING_APPROVAL" if pending else "COMPLETED",
    }


@activity.defn(name="submit_approval_and_execute")
async def fake_approve(decision_id: str, signal: ApprovalSignal) -> dict[str, Any]:
    return {
        "id": decision_id,
        "status": "COMPLETED" if signal.outcome == "APPROVED" else "REJECTED",
    }


@pytest.mark.integration
def test_real_temporal_server_runs_completion_and_signal_paths() -> None:
    address = os.getenv("TEMPORAL_TEST_ADDRESS")
    if not address:
        pytest.skip("TEMPORAL_TEST_ADDRESS is not configured")

    async def scenario() -> None:
        client = await Client.connect(address)
        task_queue = f"decisionflow-test-{uuid4()}"
        async with Worker(
            client,
            task_queue=task_queue,
            workflows=[GovernedP2PDecisionWorkflow],
            activities=[fake_evaluate, fake_approve],
        ):
            low_id = f"test-low-{uuid4()}"
            low = await client.execute_workflow(
                GovernedP2PDecisionWorkflow.run,
                EvaluateWorkflowInput(
                    case_id=str(uuid4()),
                    idempotency_key="low-key",
                    workflow_id=low_id,
                    actor_id="operator-a",
                ),
                id=low_id,
                task_queue=task_queue,
            )
            assert low["status"] == "COMPLETED"

            high_id = f"test-high-{uuid4()}"
            handle = await client.start_workflow(
                GovernedP2PDecisionWorkflow.run,
                EvaluateWorkflowInput(
                    case_id=str(uuid4()),
                    idempotency_key="high-key",
                    workflow_id=high_id,
                    actor_id="high-risk-operator",
                ),
                id=high_id,
                task_queue=task_queue,
            )
            for _ in range(50):
                state = await handle.query(GovernedP2PDecisionWorkflow.current_state)
                if state["status"] == "PENDING_APPROVAL":
                    break
                await asyncio.sleep(0.05)
            assert state["status"] == "PENDING_APPROVAL"
            await handle.signal(
                GovernedP2PDecisionWorkflow.submit_approval,
                ApprovalSignal(
                    outcome="APPROVED", approver_id="approver-b", reason="Test approval"
                ),
            )
            high = await handle.result()
            assert high["status"] == "COMPLETED"

            retry_id = f"test-retry-{uuid4()}"
            retried = await client.execute_workflow(
                GovernedP2PDecisionWorkflow.run,
                EvaluateWorkflowInput(
                    case_id=str(uuid4()),
                    idempotency_key="retry-key",
                    workflow_id=retry_id,
                    actor_id="retry-once-operator",
                ),
                id=retry_id,
                task_queue=task_queue,
            )
            assert retried["status"] == "COMPLETED"
            assert _evaluation_attempts[retry_id] == 2

    asyncio.run(scenario())

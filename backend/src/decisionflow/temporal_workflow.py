"""Deterministic durable workflow for one governed P2P decision."""

from datetime import timedelta
from typing import Any, cast

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from decisionflow.temporal_contracts import ApprovalSignal, EvaluateWorkflowInput


@workflow.defn(name="GovernedP2PDecisionWorkflow")
class GovernedP2PDecisionWorkflow:
    def __init__(self) -> None:
        self._approval: ApprovalSignal | None = None
        self._state: dict[str, Any] = {"status": "STARTING", "decision_id": None}

    @workflow.run
    async def run(self, input_data: EvaluateWorkflowInput) -> dict[str, Any]:
        self._state = {"status": "EVALUATING", "decision_id": None}
        result = cast(
            dict[str, Any],
            await workflow.execute_activity(
                "evaluate_and_explain",
                input_data,
                start_to_close_timeout=timedelta(minutes=3),
                heartbeat_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=20),
                    maximum_attempts=5,
                ),
            ),
        )
        self._state = {"status": result["status"], "decision_id": result["id"]}
        if result["status"] != "PENDING_APPROVAL":
            return result

        await workflow.wait_condition(lambda: self._approval is not None)
        approval = self._approval
        assert approval is not None
        self._state = {"status": "PROCESSING_APPROVAL", "decision_id": result["id"]}
        final = cast(
            dict[str, Any],
            await workflow.execute_activity(
                "submit_approval_and_execute",
                args=[result["id"], approval],
                start_to_close_timeout=timedelta(minutes=2),
                heartbeat_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    maximum_interval=timedelta(seconds=15),
                    maximum_attempts=5,
                ),
            ),
        )
        self._state = {"status": final["status"], "decision_id": final["id"]}
        return final

    @workflow.signal
    async def submit_approval(self, signal: ApprovalSignal) -> None:
        if self._approval is None:
            self._approval = signal

    @workflow.query
    def current_state(self) -> dict[str, Any]:
        return self._state

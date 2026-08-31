"""JSON-serializable contracts shared by Temporal workflows, activities, and API."""

from dataclasses import dataclass


@dataclass(frozen=True)
class EvaluateWorkflowInput:
    case_id: str
    idempotency_key: str
    workflow_id: str
    actor_id: str


@dataclass(frozen=True)
class ApprovalSignal:
    outcome: str
    approver_id: str
    reason: str

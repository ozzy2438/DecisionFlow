import asyncio
from typing import Any
from uuid import UUID

import decisionflow.temporal_coordinator as temporal_coordinator
from decisionflow.config import Settings
from decisionflow.service import NotFoundError
from decisionflow.temporal_coordinator import TemporalDecisionCoordinator


class FakeService:
    def __init__(self) -> None:
        self.workflow_reads = 0
        self.status = "PENDING_APPROVAL"

    def get_decision_by_workflow_id(self, workflow_id: str) -> dict[str, Any]:
        self.workflow_reads += 1
        if self.workflow_reads == 1:
            raise NotFoundError(workflow_id)
        return {
            "id": "00000000-0000-0000-0000-000000000001",
            "workflow_id": workflow_id,
            "status": self.status,
            "explanation": {"status": "TEMPLATE_FALLBACK"},
        }

    def get_decision(self, decision_id: UUID) -> dict[str, Any]:
        return {
            "id": str(decision_id),
            "workflow_id": "fake-workflow",
            "status": self.status,
        }


class FakeHandle:
    def __init__(self, service: FakeService) -> None:
        self.service = service
        self.signals: list[Any] = []

    async def signal(self, _method: Any, signal: Any) -> None:
        self.signals.append(signal)
        self.service.status = "COMPLETED"


class FakeServiceClient:
    async def check_health(self) -> None:
        return None


class FakeClient:
    instance: "FakeClient"

    def __init__(self, service: FakeService) -> None:
        self.service = service
        self.service_client = FakeServiceClient()
        self.started: list[dict[str, Any]] = []
        self.handle = FakeHandle(service)

    @classmethod
    async def connect(cls, *_args: Any, **_kwargs: Any) -> "FakeClient":
        return cls.instance

    async def start_workflow(self, *_args: Any, **kwargs: Any) -> None:
        self.started.append(kwargs)

    def get_workflow_handle(self, _workflow_id: str) -> FakeHandle:
        return self.handle


def test_temporal_coordinator_starts_waits_signals_and_checks_health(monkeypatch: Any) -> None:
    async def scenario() -> None:
        service = FakeService()
        client = FakeClient(service)
        FakeClient.instance = client
        monkeypatch.setattr(temporal_coordinator, "Client", FakeClient)
        settings = Settings(
            temporal_address="temporal:7233",
            temporal_task_queue="test-queue",
        )
        coordinator = TemporalDecisionCoordinator(settings, service)  # type: ignore[arg-type]
        await coordinator.ready()
        case_id = UUID("00000000-0000-0000-0000-000000000002")

        pending = await coordinator.evaluate(
            case_id,
            idempotency_key="test-idempotency",
            workflow_id="fake-workflow",
            actor_id="operator-a",
        )
        final = await coordinator.approve(
            UUID(pending["id"]),
            outcome="APPROVED",
            approver_id="approver-b",
            reason="Valid approval reason",
        )

        assert client.started[0]["id"] == "fake-workflow"
        assert pending["status"] == "PENDING_APPROVAL"
        assert final["status"] == "COMPLETED"
        assert client.handle.signals[0].approver_id == "approver-b"

    asyncio.run(scenario())

import asyncio
from typing import Any, ClassVar

import decisionflow.temporal_worker as temporal_worker
from decisionflow.config import Settings


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


class FakeClient:
    @classmethod
    async def connect(cls, *_args: Any, **_kwargs: Any) -> "FakeClient":
        return cls()


class FakeWorker:
    observed: ClassVar[dict[str, Any]] = {}

    def __init__(self, _client: Any, **kwargs: Any) -> None:
        self.observed = kwargs
        FakeWorker.observed = kwargs

    async def run(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


def test_worker_composition_root_starts_and_disposes(monkeypatch: Any) -> None:
    engine = FakeEngine()
    settings = Settings(
        database_url="postgresql+psycopg://example/test",
        mlflow_tracking_uri="http://mlflow:5000",
        temporal_address="temporal:7233",
        llm_provider="template",
    )
    monkeypatch.setattr(temporal_worker, "get_settings", lambda: settings)
    monkeypatch.setattr(temporal_worker, "create_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(temporal_worker, "MlflowRiskScorer", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        temporal_worker, "GovernedDecisionService", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(temporal_worker, "Client", FakeClient)
    monkeypatch.setattr(temporal_worker, "Worker", FakeWorker)

    asyncio.run(temporal_worker.run_worker())

    assert engine.disposed is True
    assert FakeWorker.observed["task_queue"] == "decisionflow-p2p"
    assert len(FakeWorker.observed["workflows"]) == 1
    assert len(FakeWorker.observed["activities"]) == 2


def test_worker_runtime_gracefully_handles_shutdown_event() -> None:
    class BlockingWorker:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.stopped = asyncio.Event()
            self.shutdown_calls = 0

        async def run(self) -> None:
            self.started.set()
            await self.stopped.wait()

        async def shutdown(self) -> None:
            self.shutdown_calls += 1
            self.stopped.set()

    async def scenario() -> None:
        worker = BlockingWorker()
        stop_event = asyncio.Event()
        task = asyncio.create_task(
            temporal_worker.run_until_shutdown(worker, stop_event=stop_event)
        )
        await worker.started.wait()
        stop_event.set()
        await task
        assert worker.shutdown_calls == 1

    asyncio.run(scenario())

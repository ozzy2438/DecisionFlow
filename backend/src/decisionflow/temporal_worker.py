"""DecisionFlow Temporal worker process."""

import asyncio
import concurrent.futures
import signal
from contextlib import suppress
from typing import Protocol

from sqlalchemy import create_engine
from temporalio.client import Client
from temporalio.worker import Worker

from decisionflow.config import get_settings
from decisionflow.explanation import (
    ExplanationProvider,
    OpenAIExplanationProvider,
    TemplateExplanationProvider,
)
from decisionflow.scoring import MlflowRiskScorer
from decisionflow.service import GovernedDecisionService
from decisionflow.temporal_activities import DecisionActivities
from decisionflow.temporal_workflow import GovernedP2PDecisionWorkflow


class WorkerRuntime(Protocol):
    async def run(self) -> None: ...

    async def shutdown(self) -> None: ...


async def run_until_shutdown(
    worker: WorkerRuntime, *, stop_event: asyncio.Event | None = None
) -> None:
    """Run a worker and translate SIGINT/SIGTERM into a graceful Temporal shutdown."""

    loop = asyncio.get_running_loop()
    event = stop_event or asyncio.Event()
    installed_signals: list[signal.Signals] = []
    if stop_event is None:
        for handled_signal in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(handled_signal, event.set)
                installed_signals.append(handled_signal)
            except (NotImplementedError, RuntimeError):
                pass

    worker_task = asyncio.create_task(worker.run())
    stop_task = asyncio.create_task(event.wait())
    try:
        done, _pending = await asyncio.wait(
            {worker_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if stop_task in done and not worker_task.done():
            await worker.shutdown()
        await worker_task
    finally:
        stop_task.cancel()
        with suppress(asyncio.CancelledError):
            await stop_task
        for handled_signal in installed_signals:
            loop.remove_signal_handler(handled_signal)


async def run_worker() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=8, max_overflow=8)
    scorer = MlflowRiskScorer(
        settings.mlflow_tracking_uri,
        settings.mlflow_model_name,
        alias=settings.mlflow_model_alias,
    )
    service = GovernedDecisionService(engine, scorer)
    provider: ExplanationProvider
    if settings.llm_provider == "openai":
        if settings.openai_api_key is None:
            raise ValueError("DECISIONFLOW_OPENAI_API_KEY is required for the OpenAI provider")
        provider = OpenAIExplanationProvider(
            api_key=settings.openai_api_key.get_secret_value(), model=settings.llm_model
        )
    elif settings.llm_provider == "template":
        provider = TemplateExplanationProvider()
    else:
        raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")
    activities = DecisionActivities(service, provider)
    client = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        worker = Worker(
            client,
            task_queue=settings.temporal_task_queue,
            workflows=[GovernedP2PDecisionWorkflow],
            activities=[
                activities.evaluate_and_explain,
                activities.submit_approval_and_execute,
            ],
            activity_executor=executor,
            max_concurrent_activities=8,
        )
        try:
            await run_until_shutdown(worker)
        finally:
            engine.dispose()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()

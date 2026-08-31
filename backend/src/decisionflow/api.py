"""FastAPI surface for the governed P2P decision twin."""

import hashlib
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import create_engine, text

from decisionflow.auth import Principal, TokenAuthenticator, principal_dependency, require_roles
from decisionflow.config import Settings, get_settings
from decisionflow.coordinator import DecisionCoordinator, DirectDecisionCoordinator
from decisionflow.explanation import (
    ExplanationProvider,
    OpenAIExplanationProvider,
    TemplateExplanationProvider,
)
from decisionflow.features import CATEGORICAL_FEATURES, NUMERIC_FEATURES
from decisionflow.scoring import MlflowRiskScorer
from decisionflow.service import (
    ApprovalRequiredError,
    ConflictError,
    GovernedDecisionService,
    NotFoundError,
    SeparationOfDutiesError,
    VerificationError,
)
from decisionflow.temporal_coordinator import TemporalDecisionCoordinator

REQUESTS = Counter(
    "decisionflow_http_requests_total", "HTTP requests", ["method", "route", "status"]
)
LATENCY = Histogram(
    "decisionflow_http_request_duration_seconds", "HTTP request duration", ["method", "route"]
)


class FeatureSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gr_based_invoice_verification: float | None = Field(default=None, ge=0, le=1)
    goods_receipt_required: float | None = Field(default=None, ge=0, le=1)
    prefix_event_count: float | None = Field(default=None, ge=0, le=100_000)
    unique_activity_count: float | None = Field(default=None, ge=0, le=1_000)
    goods_receipt_count: float | None = Field(default=None, ge=0, le=100_000)
    invoice_receipt_count: float | None = Field(default=None, ge=0, le=100_000)
    vendor_invoice_count: float | None = Field(default=None, ge=0, le=100_000)
    change_count: float | None = Field(default=None, ge=0, le=100_000)
    cancellation_count: float | None = Field(default=None, ge=0, le=100_000)
    payment_block_count: float | None = Field(default=None, ge=0, le=100_000)
    days_since_last_event: float | None = Field(default=None, ge=0, le=10_000)
    days_to_first_goods_receipt: float | None = Field(default=None, ge=0, le=10_000)
    days_to_first_invoice_receipt: float | None = Field(default=None, ge=0, le=10_000)
    item_type: str | None = Field(default=None, max_length=500)
    source_system: str | None = Field(default=None, max_length=500)
    company: str | None = Field(default=None, max_length=500)
    spend_classification: str | None = Field(default=None, max_length=500)
    spend_area: str | None = Field(default=None, max_length=500)
    sub_spend_area: str | None = Field(default=None, max_length=500)
    vendor: str | None = Field(default=None, max_length=500)
    document_type: str | None = Field(default=None, max_length=500)
    item_category: str | None = Field(default=None, max_length=500)
    last_activity: str | None = Field(default=None, max_length=500)

    def complete_dict(self) -> dict[str, Any]:
        values = self.model_dump()
        assert set(values) == set(NUMERIC_FEATURES + CATEGORICAL_FEATURES)
        return values


class TwinCaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_id: str | None = Field(
        default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$"
    )
    source_case_id: str | None = Field(default=None, max_length=160)
    snapshot_at: datetime | None = None
    case_context: dict[str, Any] = Field(default_factory=dict)
    features: FeatureSnapshot

    @field_validator("case_context")
    @classmethod
    def context_size_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(value, ensure_ascii=False)
        if len(encoded.encode()) > 10_000:
            raise ValueError("case_context exceeds the 10 KB limit")
        return value


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["APPROVED", "REJECTED"]
    reason: str = Field(min_length=3, max_length=1000)


def _build_runtime(settings: Settings) -> tuple[GovernedDecisionService, DecisionCoordinator]:
    engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=20)
    scorer = MlflowRiskScorer(
        settings.mlflow_tracking_uri,
        settings.mlflow_model_name,
        alias=settings.mlflow_model_alias,
    )
    service = GovernedDecisionService(engine, scorer)
    explanation_provider: ExplanationProvider
    if settings.llm_provider == "openai":
        if settings.openai_api_key is None:
            raise ValueError("DECISIONFLOW_OPENAI_API_KEY is required when LLM provider is openai")
        explanation_provider = OpenAIExplanationProvider(
            api_key=settings.openai_api_key.get_secret_value(), model=settings.llm_model
        )
    elif settings.llm_provider == "template":
        explanation_provider = TemplateExplanationProvider()
    else:
        raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")
    if settings.orchestration_mode == "temporal":
        coordinator: DecisionCoordinator = TemporalDecisionCoordinator(settings, service)
    elif settings.orchestration_mode == "direct":
        coordinator = DirectDecisionCoordinator(service, explanation_provider)
    else:
        raise ValueError(f"Unsupported orchestration mode: {settings.orchestration_mode}")
    return service, coordinator


def create_app(
    *,
    settings: Settings | None = None,
    service: GovernedDecisionService | None = None,
    coordinator: DecisionCoordinator | None = None,
    authenticator: TokenAuthenticator | None = None,
) -> FastAPI:
    runtime_settings = settings or get_settings()
    if service is None or coordinator is None:
        service, coordinator = _build_runtime(runtime_settings)
    auth = authenticator or TokenAuthenticator(
        runtime_settings.auth_tokens_json, environment=runtime_settings.environment
    )
    principal = principal_dependency(auth)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> Any:
        yield
        service.engine.dispose()

    app = FastAPI(
        title="DecisionFlow API",
        version="0.1.0",
        description=(
            "Governed Purchase-to-Pay Decision Twin. All operational actions are simulated."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[item.strip() for item in runtime_settings.cors_origins.split(",")],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
    )

    @app.middleware("http")
    async def observability_middleware(request: Request, call_next: Any) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid4()))[:100]
        started = time.monotonic()
        try:
            response = cast(Response, await call_next(request))
        except Exception:
            REQUESTS.labels(request.method, request.url.path, "500").inc()
            raise
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        elapsed = time.monotonic() - started
        REQUESTS.labels(request.method, route_path, str(response.status_code)).inc()
        LATENCY.labels(request.method, route_path).observe(elapsed)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(NotFoundError)
    async def not_found(_request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    async def conflict(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    for conflict_type in (ConflictError, ApprovalRequiredError, SeparationOfDutiesError):
        app.add_exception_handler(conflict_type, conflict)

    @app.exception_handler(VerificationError)
    async def verification_failure(_request: Request, _exc: VerificationError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={"detail": "Digital Twin read-back verification failed"},
        )

    @app.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready() -> dict[str, str]:
        try:
            with service.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Database is unavailable") from exc
        ready_method = getattr(coordinator, "ready", None)
        if ready_method is not None:
            try:
                await ready_method()
            except Exception as exc:
                raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc
        return {"status": "ready"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    def authenticated(credentials: Annotated[Principal, Depends(principal)]) -> Principal:
        return credentials

    def operator(credentials: Annotated[Principal, Depends(authenticated)]) -> Principal:
        return require_roles("operator")(credentials)

    def approver(credentials: Annotated[Principal, Depends(authenticated)]) -> Principal:
        return require_roles("approver")(credentials)

    def reader(credentials: Annotated[Principal, Depends(authenticated)]) -> Principal:
        return require_roles("operator", "approver", "auditor")(credentials)

    @app.get("/api/v1/cases")
    async def list_cases(
        _actor: Annotated[Principal, Depends(reader)],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        return service.list_cases(limit=limit)

    @app.post("/api/v1/cases", status_code=status.HTTP_201_CREATED)
    async def create_case(
        body: TwinCaseCreate, _actor: Annotated[Principal, Depends(operator)]
    ) -> dict[str, Any]:
        return service.create_twin_case(
            features=body.features.complete_dict(),
            case_context=body.case_context,
            display_id=body.display_id,
            source_case_id=body.source_case_id,
            snapshot_at=body.snapshot_at,
        )

    @app.get("/api/v1/cases/{case_id}")
    async def get_case(
        case_id: UUID, _actor: Annotated[Principal, Depends(reader)]
    ) -> dict[str, Any]:
        return service.get_case(case_id)

    @app.post("/api/v1/cases/{case_id}/decisions", status_code=status.HTTP_201_CREATED)
    async def evaluate_case(
        case_id: UUID,
        actor: Annotated[Principal, Depends(operator)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=8, max_length=200)
        ],
    ) -> dict[str, Any]:
        workflow_suffix = hashlib.sha256(idempotency_key.encode()).hexdigest()[:20]
        workflow_id = f"decisionflow-{case_id}-{workflow_suffix}"
        return await coordinator.evaluate(
            case_id,
            idempotency_key=idempotency_key,
            workflow_id=workflow_id,
            actor_id=actor.actor_id,
        )

    @app.get("/api/v1/decisions/{decision_id}")
    async def get_decision(
        decision_id: UUID, _actor: Annotated[Principal, Depends(reader)]
    ) -> dict[str, Any]:
        return service.get_decision(decision_id)

    @app.post("/api/v1/decisions/{decision_id}/approval")
    async def approve_decision(
        decision_id: UUID,
        body: ApprovalRequest,
        actor: Annotated[Principal, Depends(approver)],
    ) -> dict[str, Any]:
        return await coordinator.approve(
            decision_id,
            outcome=body.outcome,
            approver_id=actor.actor_id,
            reason=body.reason,
        )

    return app


app = create_app()

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from alembic import command
from decisionflow.api import create_app
from decisionflow.auth import TokenAuthenticator
from decisionflow.config import Settings
from decisionflow.coordinator import DirectDecisionCoordinator
from decisionflow.explanation import TemplateExplanationProvider
from decisionflow.scoring import RiskPrediction
from decisionflow.service import GovernedDecisionService

TOKENS = {
    "operator-token-0000001": {"actor_id": "operator-a", "roles": ["operator"]},
    "approver-token-0000001": {"actor_id": "approver-b", "roles": ["approver"]},
    "auditor-token-00000001": {"actor_id": "auditor-c", "roles": ["auditor"]},
}


class StubScorer:
    def __init__(self, score: float) -> None:
        self.score_value = score

    def score(self, _features: dict[str, Any]) -> RiskPrediction:
        return RiskPrediction(
            score=self.score_value,
            model_name="test-model",
            model_version="2",
            model_alias="candidate",
            model_policy_version="sla-proxy-v1",
        )


def _database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    if not urlparse(url).path.removeprefix("/").endswith("_test"):
        pytest.skip("Refusing destructive setup outside a *_test database")
    return url


@pytest.fixture
def api_engine() -> Any:
    url = _database_url()
    backend_root = Path(__file__).parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                TRUNCATE decision_explanations, decision_ledger, readback_verifications,
                         tool_executions, approval_records, twin_follow_up_tasks,
                         twin_payment_holds, decision_records, twin_cases
                RESTART IDENTITY CASCADE
                """
            )
        )
    yield engine
    engine.dispose()


def _client(engine: Any, score: float) -> TestClient:
    settings = Settings(
        environment="test",
        database_url=str(engine.url),
        auth_tokens_json=json.dumps(TOKENS),
        llm_provider="template",
    )
    service = GovernedDecisionService(engine, StubScorer(score))
    coordinator = DirectDecisionCoordinator(service, TemplateExplanationProvider())
    authenticator = TokenAuthenticator(settings.auth_tokens_json, environment=settings.environment)
    return TestClient(
        create_app(
            settings=settings,
            service=service,
            coordinator=coordinator,
            authenticator=authenticator,
        )
    )


def _auth(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


@pytest.mark.integration
def test_api_low_risk_flow_is_authenticated_and_idempotent(api_engine: Any) -> None:
    with _client(api_engine, 0.65) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/v1/cases").status_code == 401
        assert (
            client.post(
                "/api/v1/cases",
                headers=_auth("auditor-token-00000001"),
                json={"display_id": "SIM-DENIED", "features": {}},
            ).status_code
            == 403
        )
        invalid = client.post(
            "/api/v1/cases",
            headers=_auth("operator-token-0000001"),
            json={"display_id": "SIM-BAD", "is_simulated": False, "features": {}},
        )
        assert invalid.status_code == 422

        created = client.post(
            "/api/v1/cases",
            headers=_auth("operator-token-0000001"),
            json={"display_id": "SIM-API-LOW", "features": {}},
        )
        assert created.status_code == 201
        assert created.json()["is_simulated"] is True
        case_id = created.json()["id"]
        headers = _auth("operator-token-0000001", **{"Idempotency-Key": "api-low-key-0001"})
        evaluated = client.post(f"/api/v1/cases/{case_id}/decisions", headers=headers)
        replayed = client.post(f"/api/v1/cases/{case_id}/decisions", headers=headers)

        assert evaluated.status_code == 201
        assert evaluated.json()["id"] == replayed.json()["id"]
        assert evaluated.json()["status"] == "COMPLETED"
        assert evaluated.json()["explanation"]["status"] == "TEMPLATE_FALLBACK"
        assert evaluated.json()["ledger_verification"]["valid"] is True
        assert evaluated.headers["X-Content-Type-Options"] == "nosniff"
        assert client.get("/metrics").status_code == 200


@pytest.mark.integration
def test_api_high_impact_flow_enforces_approver_role(api_engine: Any) -> None:
    with _client(api_engine, 0.95) as client:
        created = client.post(
            "/api/v1/cases",
            headers=_auth("operator-token-0000001"),
            json={
                "display_id": "SIM-API-HIGH",
                "features": {
                    "goods_receipt_required": 1,
                    "goods_receipt_count": 0,
                    "invoice_receipt_count": 1,
                },
            },
        ).json()
        pending = client.post(
            f"/api/v1/cases/{created['id']}/decisions",
            headers=_auth(
                "operator-token-0000001", **{"Idempotency-Key": "api-high-key-0001"}
            ),
        )
        assert pending.status_code == 201
        assert pending.json()["status"] == "PENDING_APPROVAL"
        decision_id = pending.json()["id"]
        body = {"outcome": "APPROVED", "reason": "Required evidence supports the twin hold."}

        denied = client.post(
            f"/api/v1/decisions/{decision_id}/approval",
            headers=_auth("operator-token-0000001"),
            json=body,
        )
        approved = client.post(
            f"/api/v1/decisions/{decision_id}/approval",
            headers=_auth("approver-token-0000001"),
            json=body,
        )

        assert denied.status_code == 403
        assert approved.status_code == 200
        assert approved.json()["status"] == "COMPLETED"
        assert approved.json()["approval"]["approver_id"] == "approver-b"
        assert approved.json()["verifications"][0]["passed"] is True


def test_auth_configuration_fails_closed() -> None:
    with pytest.raises(ValueError, match="require configured"):
        TokenAuthenticator("{}", environment="production")
    with pytest.raises(ValueError, match="valid JSON"):
        TokenAuthenticator("not-json", environment="test")

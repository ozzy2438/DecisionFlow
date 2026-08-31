import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from decisionflow.explanation import TemplateExplanationProvider, safe_explanation_payload
from decisionflow.features import CATEGORICAL_FEATURES, NUMERIC_FEATURES
from decisionflow.scoring import RiskPrediction
from decisionflow.service import (
    ApprovalRequiredError,
    GovernedDecisionService,
    SeparationOfDutiesError,
    VerificationError,
)


class StubScorer:
    def __init__(self, score: float) -> None:
        self.risk_score = score

    def score(self, features: dict[str, Any]) -> RiskPrediction:
        return RiskPrediction(
            score=self.risk_score,
            model_name="test-model",
            model_version="7",
            model_alias="candidate",
            model_policy_version="sla-proxy-v1",
        )


def _test_database_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL", "")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    if not urlparse(database_url).path.removeprefix("/").endswith("_test"):
        pytest.skip("Refusing destructive setup outside a *_test database")
    return database_url


def _features(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {name: 0 for name in NUMERIC_FEATURES}
    values.update({name: "__missing__" for name in CATEGORICAL_FEATURES})
    values.update(overrides)
    return values


@pytest.fixture
def integration_engine() -> Any:
    database_url = _test_database_url()
    backend_root = Path(__file__).parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                TRUNCATE decision_ledger, readback_verifications, tool_executions,
                         approval_records, twin_follow_up_tasks, twin_payment_holds,
                         decision_records, twin_cases RESTART IDENTITY CASCADE
                """
            )
        )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.integration
def test_low_risk_action_is_applied_verified_and_idempotent(integration_engine: Any) -> None:
    service = GovernedDecisionService(integration_engine, StubScorer(0.65))
    twin_case = service.create_twin_case(features=_features(), display_id="SIM-LOW")
    case_id = UUID(twin_case["id"])

    result = service.evaluate_case(
        case_id,
        idempotency_key="low-evaluation-1",
        workflow_id="workflow-low-1",
        actor_id="operator-a",
    )
    replay = service.evaluate_case(
        case_id,
        idempotency_key="low-evaluation-1",
        workflow_id="workflow-low-1",
        actor_id="operator-a",
    )

    assert result["id"] == replay["id"]
    assert result["status"] == "COMPLETED"
    assert result["decision"]["selected_action"] == "CREATE_FOLLOW_UP_TASK"
    assert result["decision"]["approval_required"] is False
    assert len(result["executions"]) == 1
    assert result["verifications"][0]["passed"] is True
    assert result["ledger_verification"]["valid"] is True
    assert [entry["event_type"] for entry in result["ledger"]] == [
        "DECISION_RECORDED",
        "TOOL_EXECUTION_SUCCEEDED",
        "READBACK_VERIFICATION_PASSED",
        "WORKFLOW_COMPLETED",
    ]
    assert service.get_case(case_id)["twin_state"]["open_follow_up_tasks"] == 1

    generated = TemplateExplanationProvider().generate(
        safe_explanation_payload(result, service.get_case(case_id))
    )
    with_explanation = service.record_explanation(
        UUID(result["id"]),
        provider=generated.provider,
        model=generated.model,
        status=generated.status,
        prompt_version=generated.prompt_version,
        input_hash=generated.input_hash,
        output=generated.content.model_dump(),
    )
    replayed_explanation = service.record_explanation(
        UUID(result["id"]),
        provider=generated.provider,
        model=generated.model,
        status=generated.status,
        prompt_version=generated.prompt_version,
        input_hash=generated.input_hash,
        output=generated.content.model_dump(),
    )

    assert with_explanation["explanation"]["status"] == "TEMPLATE_FALLBACK"
    assert replayed_explanation["explanation"] == with_explanation["explanation"]
    assert with_explanation["ledger"][-1]["event_type"] == "EXPLANATION_RECORDED"
    assert with_explanation["ledger_verification"]["valid"] is True


@pytest.mark.integration
def test_high_impact_action_requires_separate_human_approval(integration_engine: Any) -> None:
    service = GovernedDecisionService(integration_engine, StubScorer(0.93))
    twin_case = service.create_twin_case(
        features=_features(
            goods_receipt_required=1,
            goods_receipt_count=0,
            invoice_receipt_count=1,
        ),
        display_id="SIM-HIGH",
    )
    result = service.evaluate_case(
        UUID(twin_case["id"]),
        idempotency_key="high-evaluation-1",
        workflow_id="workflow-high-1",
        actor_id="operator-a",
    )
    decision_id = UUID(result["id"])

    assert result["status"] == "PENDING_APPROVAL"
    with pytest.raises(ApprovalRequiredError):
        service.execute_decision(decision_id)
    with pytest.raises(SeparationOfDutiesError):
        service.submit_approval(
            decision_id,
            outcome="APPROVED",
            approver_id="operator-a",
            reason="Same-person approval must be denied",
        )

    approved = service.submit_approval(
        decision_id,
        outcome="APPROVED",
        approver_id="approver-b",
        reason="Required goods receipt is absent; approve the simulated hold.",
    )

    assert approved["status"] == "COMPLETED"
    assert approved["approval"]["outcome"] == "APPROVED"
    assert approved["executions"][0]["tool_name"] == "digital_twin.place_payment_hold"
    assert approved["verifications"][0]["passed"] is True
    assert approved["ledger_verification"]["valid"] is True


@pytest.mark.integration
def test_rejection_is_final_and_governance_tables_are_append_only(integration_engine: Any) -> None:
    service = GovernedDecisionService(integration_engine, StubScorer(0.99))
    twin_case = service.create_twin_case(
        features=_features(
            goods_receipt_required=1,
            goods_receipt_count=0,
            invoice_receipt_count=1,
        ),
        display_id="SIM-REJECT",
    )
    pending = service.evaluate_case(
        UUID(twin_case["id"]),
        idempotency_key="reject-evaluation-1",  # gitleaks:allow
        workflow_id="workflow-reject-1",
        actor_id="operator-a",
    )
    decision_id = UUID(pending["id"])
    rejected = service.submit_approval(
        decision_id,
        outcome="REJECTED",
        approver_id="approver-b",
        reason="The operational evidence is insufficient.",
    )

    assert rejected["status"] == "REJECTED"
    assert rejected["executions"] == []
    connection = integration_engine.connect()
    transaction = connection.begin()
    try:
        with pytest.raises(DBAPIError, match="append-only"):
            connection.execute(
                text("UPDATE decision_records SET risk_score = 0 WHERE id = :id"),
                {"id": decision_id},
            )
    finally:
        transaction.rollback()
        connection.close()


@pytest.mark.integration
def test_readback_mismatch_fails_closed_and_is_audited(
    integration_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = GovernedDecisionService(integration_engine, StubScorer(0.65))
    twin_case = service.create_twin_case(features=_features(), display_id="SIM-MISMATCH")
    case_id = UUID(twin_case["id"])
    apply_tool = service._apply_allowlisted_tool

    def mismatched_response(*args: Any, **kwargs: Any) -> dict[str, Any]:
        response = apply_tool(*args, **kwargs)
        return {**response, "queue": "UNEXPECTED_QUEUE"}

    monkeypatch.setattr(service, "_apply_allowlisted_tool", mismatched_response)

    with pytest.raises(VerificationError, match="did not match"):
        service.evaluate_case(
            case_id,
            idempotency_key="readback-mismatch-1",
            workflow_id="workflow-mismatch-1",
            actor_id="operator-a",
        )

    failed = service.get_decision(UUID(service.get_case(case_id)["decision_ids"][0]))
    assert failed["status"] == "FAILED"
    assert failed["verifications"][0]["passed"] is False
    assert failed["ledger_verification"]["valid"] is True
    assert [entry["event_type"] for entry in failed["ledger"]][-2:] == [
        "READBACK_VERIFICATION_FAILED",
        "WORKFLOW_FAILED",
    ]

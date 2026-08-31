from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from decisionflow.explanation import (
    AUTHORITY_STATEMENT,
    ExplanationContent,
    OpenAIExplanationProvider,
    TemplateExplanationProvider,
    safe_explanation_payload,
)


def _decision() -> dict[str, Any]:
    return {
        "prediction": {
            "model_name": "decisionflow-sla-risk",
            "model_version": "2",
            "model_alias": "candidate",
            "model_policy_version": "sla-proxy-v1",
            "risk_score": 0.72,
            "input_hash": "a" * 64,
        },
        "decision": {
            "rule_id": "ELEVATED_SLA_RISK_SAFE_FOLLOW_UP",
            "rule_version": "p2p-action-policy-v1",
            "selected_action": "CREATE_FOLLOW_UP_TASK",
            "impact": "LOW",
            "approval_required": False,
        },
        "status": "COMPLETED",
        "verifications": [{"passed": True}],
    }


def _case() -> dict[str, Any]:
    return {
        "display_id": "SIM-1234",
        "snapshot_at": "2026-08-31T00:00:00+00:00",
        "feature_snapshot": {
            "prefix_event_count": 5,
            "unique_activity_count": 4,
            "goods_receipt_required": 1,
            "goods_receipt_count": 1,
            "invoice_receipt_count": 1,
            "vendor_invoice_count": 1,
            "payment_block_count": 0,
            "days_since_last_event": 2.0,
            "last_activity": "Record Invoice Receipt",
            "vendor": "must-not-leave-boundary",
        },
    }


def test_safe_payload_and_template_fallback_are_explicit() -> None:
    payload = safe_explanation_payload(_decision(), _case())
    result = TemplateExplanationProvider().generate(payload)

    assert "vendor" not in payload["case"]["features"]
    assert payload["data_classification"] == "SIMULATED_OPERATIONAL_TWIN"
    assert result.status == "TEMPLATE_FALLBACK"
    assert result.provider == "deterministic_template"
    assert result.content.authority_statement == AUTHORITY_STATEMENT
    assert len(result.input_hash) == 64


class _FakeResponses:
    def __init__(self, output: ExplanationContent | None) -> None:
        self.output = output
        self.kwargs: dict[str, Any] = {}

    def parse(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return SimpleNamespace(output_parsed=self.output)


def test_openai_provider_has_no_tool_or_decision_callback() -> None:
    content = ExplanationContent(
        summary="A concise summary.",
        risk_explanation="The registered model and deterministic rule produced this result.",
        message_draft="Please review the simulated case.",
        limitations=["The effect is simulated."],
        authority_statement=AUTHORITY_STATEMENT,
    )
    responses = _FakeResponses(content)
    client = SimpleNamespace(responses=responses)
    provider = OpenAIExplanationProvider(api_key="test-only", model="test-model", client=client)

    result = provider.generate(safe_explanation_payload(_decision(), _case()))

    assert result.status == "GENERATED"
    assert result.content == content
    assert "tools" not in responses.kwargs
    assert responses.kwargs["text_format"] is ExplanationContent
    assert "Do not choose or change" in responses.kwargs["input"][0]["content"]


def test_openai_provider_fails_closed_on_missing_structured_output() -> None:
    client = SimpleNamespace(responses=_FakeResponses(None))
    provider = OpenAIExplanationProvider(api_key="test-only", client=client)
    with pytest.raises(RuntimeError, match="validated explanation"):
        provider.generate(safe_explanation_payload(_decision(), _case()))

    with pytest.raises(ValueError, match="API key"):
        OpenAIExplanationProvider(api_key="")


def test_explanation_schema_rejects_changed_authority_statement() -> None:
    with pytest.raises(ValidationError):
        ExplanationContent(
            summary="Summary",
            risk_explanation="Risk",
            message_draft="Message",
            limitations=["Limit"],
            authority_statement="I decide and execute actions.",  # type: ignore[arg-type]
        )

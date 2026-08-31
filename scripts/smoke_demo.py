"""Exercise the complete governed low-impact and HITL API paths."""

import json
import os
import time
from typing import Any
from urllib.request import Request, urlopen

BASE_URL = os.getenv("DECISIONFLOW_DEMO_URL", "http://127.0.0.1:18765")
OPERATOR_TOKEN = os.getenv("DECISIONFLOW_DEMO_OPERATOR_TOKEN", "operator-local-demo-key")
APPROVER_TOKEN = os.getenv("DECISIONFLOW_DEMO_APPROVER_TOKEN", "approver-local-demo-key")


def call(
    method: str,
    path: str,
    *,
    token: str,
    body: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    request = Request(
        BASE_URL + path,
        method=method,
        headers=headers,
        data=json.dumps(body or {}).encode(),
    )
    with urlopen(request, timeout=90) as response:
        return dict(json.load(response))


def create_case(profile: str, features: dict[str, Any]) -> dict[str, Any]:
    suffix = str(time.time_ns())[-12:]
    return call(
        "POST",
        "/api/v1/cases",
        token=OPERATOR_TOKEN,
        body={
            "display_id": f"SIM-SMOKE-{profile}-{suffix}",
            "case_context": {"scenario": profile, "provenance": "synthetic smoke profile"},
            "features": features,
        },
    )


def assert_completed_and_verified(decision: dict[str, Any]) -> None:
    assert decision["status"] == "COMPLETED", decision
    assert decision["simulation"] is True, decision
    assert decision["verifications"][-1]["passed"] is True, decision
    assert decision["ledger_verification"]["valid"] is True, decision
    assert decision["explanation"]["non_authoritative"] is True, decision


def main() -> None:
    low_case = create_case(
        "SAFE",
        {
            "goods_receipt_required": 1,
            "prefix_event_count": 3,
            "unique_activity_count": 3,
            "goods_receipt_count": 1,
            "invoice_receipt_count": 1,
            "vendor_invoice_count": 2,
            "days_since_last_event": 0,
            "vendor": "vendorID_0197",
            "sub_spend_area": "Color Collateral",
            "last_activity": "Record Invoice Receipt",
        },
    )
    low = call(
        "POST",
        f"/api/v1/cases/{low_case['id']}/decisions",
        token=OPERATOR_TOKEN,
        idempotency_key=f"smoke-safe-{low_case['id']}",
    )
    assert low["decision"]["selected_action"] == "CREATE_FOLLOW_UP_TASK", low
    assert low["decision"]["approval_required"] is False, low
    assert_completed_and_verified(low)

    high_case = create_case(
        "HITL",
        {
            "goods_receipt_required": 1,
            "prefix_event_count": 10,
            "unique_activity_count": 5,
            "goods_receipt_count": 0,
            "invoice_receipt_count": 1,
            "vendor_invoice_count": 0,
            "days_since_last_event": 7,
            "vendor": "vendorID_0182",
            "sub_spend_area": "Color Collateral",
            "last_activity": "Record Invoice Receipt",
        },
    )
    pending = call(
        "POST",
        f"/api/v1/cases/{high_case['id']}/decisions",
        token=OPERATOR_TOKEN,
        idempotency_key=f"smoke-hitl-{high_case['id']}",
    )
    assert pending["status"] == "PENDING_APPROVAL", pending
    assert pending["decision"]["selected_action"] == "PLACE_SIMULATED_PAYMENT_HOLD", pending
    assert pending["executions"] == [], pending
    approved = call(
        "POST",
        f"/api/v1/decisions/{pending['id']}/approval",
        token=APPROVER_TOKEN,
        body={
            "outcome": "APPROVED",
            "reason": "Smoke test approves this simulated-only hold after human review.",
        },
    )
    assert approved["approval"]["approver_id"] == "approver-demo", approved
    assert_completed_and_verified(approved)

    print(
        json.dumps(
            {
                "status": "PASSED",
                "safe_decision_id": low["id"],
                "hitl_decision_id": approved["id"],
                "model_version": low["prediction"]["model_version"],
                "ledger_chains_valid": True,
                "readback_verifications_passed": True,
                "simulation_only": True,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

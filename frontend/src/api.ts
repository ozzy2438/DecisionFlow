import type { CaseRecord, DecisionRecord, Scenario } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(
  path: string,
  token: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      ...(options.headers ?? {}),
    },
  });
  const body = (await response.json().catch(() => ({}))) as { detail?: string };
  if (!response.ok) {
    throw new ApiError(body.detail ?? `Request failed (${response.status})`, response.status);
  }
  return body as T;
}

export const api = {
  listCases(token: string): Promise<CaseRecord[]> {
    return request("/api/v1/cases?limit=100", token);
  },

  getCase(token: string, id: string): Promise<CaseRecord> {
    return request(`/api/v1/cases/${id}`, token);
  },

  getDecision(token: string, id: string): Promise<DecisionRecord> {
    return request(`/api/v1/decisions/${id}`, token);
  },

  async createScenario(token: string, scenario: Scenario): Promise<CaseRecord> {
    const suffix = `${Date.now().toString(36)}-${crypto.randomUUID().slice(0, 6)}`.toUpperCase();
    const isHigh = scenario === "HIGH";
    return request("/api/v1/cases", token, {
      method: "POST",
      body: JSON.stringify({
        display_id: `SIM-UI-${scenario}-${suffix}`,
        case_context: {
          scenario: isHigh ? "HITL approval demonstration" : "safe auto-action demonstration",
          provenance: "synthetic UI profile",
        },
        features: isHigh
          ? {
              goods_receipt_required: 1,
              prefix_event_count: 10,
              unique_activity_count: 5,
              goods_receipt_count: 0,
              invoice_receipt_count: 1,
              vendor_invoice_count: 0,
              days_since_last_event: 7,
              vendor: "vendorID_0182",
              sub_spend_area: "Color Collateral",
              last_activity: "Record Invoice Receipt",
            }
          : {
              goods_receipt_required: 1,
              prefix_event_count: 3,
              unique_activity_count: 3,
              goods_receipt_count: 1,
              invoice_receipt_count: 1,
              vendor_invoice_count: 2,
              days_since_last_event: 0,
              vendor: "vendorID_0197",
              sub_spend_area: "Color Collateral",
              last_activity: "Record Invoice Receipt",
            },
      }),
    });
  },

  evaluate(token: string, caseId: string): Promise<DecisionRecord> {
    return request(`/api/v1/cases/${caseId}/decisions`, token, {
      method: "POST",
      headers: { "Idempotency-Key": `ui-${caseId}-${crypto.randomUUID()}` },
      body: "{}",
    });
  },

  approve(
    token: string,
    decisionId: string,
    outcome: "APPROVED" | "REJECTED",
    reason: string,
  ): Promise<DecisionRecord> {
    return request(`/api/v1/decisions/${decisionId}/approval`, token, {
      method: "POST",
      body: JSON.stringify({ outcome, reason }),
    });
  },
};

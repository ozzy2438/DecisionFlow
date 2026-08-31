export type CaseRecord = {
  id: string;
  display_id: string;
  is_simulated: boolean;
  status: string;
  snapshot_at: string;
  created_at: string;
  case_context: Record<string, unknown>;
  feature_snapshot: Record<string, unknown>;
  decision_ids: string[];
  twin_state: { open_follow_up_tasks: number; active_payment_holds: number };
};

export type LedgerEntry = {
  sequence: number;
  event_type: string;
  actor_type: string;
  actor_id: string;
  occurred_at: string;
  payload: Record<string, unknown>;
  previous_hash: string;
  entry_hash: string;
};

export type DecisionRecord = {
  id: string;
  twin_case_id: string;
  workflow_id: string;
  status: string;
  simulation: true;
  prediction: {
    model_name: string;
    model_version: string;
    model_alias: string;
    model_policy_version: string;
    risk_score: number;
    input_hash: string;
  };
  decision: {
    rule_id: string;
    rule_version: string;
    selected_action: string;
    impact: "NONE" | "LOW" | "HIGH";
    approval_required: boolean;
  };
  approval: null | {
    outcome: string;
    approver_id: string;
    reason: string;
    created_at: string;
  };
  executions: Array<{
    id: string;
    tool_name: string;
    status: string;
    response: Record<string, unknown>;
  }>;
  verifications: Array<{ id: string; passed: boolean }>;
  explanation: null | {
    provider: string;
    model: string;
    status: string;
    non_authoritative: true;
    output: {
      summary: string;
      risk_explanation: string;
      message_draft: string;
      limitations: string[];
      authority_statement: string;
    };
  };
  ledger: LedgerEntry[];
  ledger_verification: { valid: boolean; entries: number; head_hash: string; errors: string[] };
};

export type Scenario = "LOW" | "HIGH";

import type { DecisionRecord } from "./types";

export type RiskTone = "low" | "elevated" | "high";

export function riskTone(score: number): RiskTone {
  if (score >= 0.8) return "high";
  if (score >= 0.5) return "elevated";
  return "low";
}

export function shortHash(value: string): string {
  if (!value) return "—";
  return `${value.slice(0, 8)}…${value.slice(-6)}`;
}

export function pipelineStates(decision: DecisionRecord | null): Array<{
  label: string;
  state: "idle" | "done" | "waiting";
}> {
  if (!decision) {
    return ["Event", "Predict", "Decide", "Approve", "Act", "Verify", "Audit"].map(
      (label) => ({ label, state: "idle" as const }),
    );
  }
  const pending = decision.status === "PENDING_APPROVAL";
  const needsApproval = decision.decision.approval_required;
  return [
    { label: "Event", state: "done" },
    { label: "Predict", state: "done" },
    { label: "Decide", state: "done" },
    { label: "Approve", state: pending ? "waiting" : needsApproval ? "done" : "idle" },
    { label: "Act", state: pending ? "idle" : "done" },
    { label: "Verify", state: decision.verifications.length ? "done" : pending ? "idle" : "done" },
    { label: "Audit", state: decision.ledger_verification.valid ? "done" : "waiting" },
  ] as Array<{ label: string; state: "idle" | "done" | "waiting" }>;
}

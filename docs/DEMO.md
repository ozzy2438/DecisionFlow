# DecisionFlow — 2–3 Minute Demo

## Demo goal

Show that DecisionFlow does more than predict risk. It converts a model signal into a governed,
durable, simulated, independently verified action with a complete audit record.

The story is:

> **Detect → Decide → Approve → Act → Verify → Audit**

This demo uses only the Digital Twin. No ERP, supplier, banking, or payment system is contacted.

## Before the demo

Start the stack:

```bash
make up
```

Open [http://localhost:18766](http://localhost:18766).

Use the local demo identities:

- operator: `operator-local-demo-key`
- approver: `approver-local-demo-key`

The tokens remain in browser memory and are not written to browser storage.

## Scenario B — Governed high-impact action

### 0:00–0:20 — Frame the decision

**Do**

Enter the operator token. Point to the banner: “Simulation only · no ERP connection.”

**Say**

“This system answers one bounded Purchase-to-Pay question: is this open case likely to miss its
SLA, and which action is permitted now? The model can estimate risk, but it cannot authorize an
operational action.”

### 0:20–0:45 — Detect

**Do**

Click **+ HITL demo**.

**Say**

“DecisionFlow sends the simulated case through a durable Temporal workflow. MLflow-pinned
LightGBM candidate version 2 scores the case at approximately 0.99898 risk. The score, model
version, policy version, and feature hash are recorded.”

### 0:45–1:10 — Decide

**Do**

Point to `PLACE_SIMULATED_PAYMENT_HOLD` and the rule identifier.

**Say**

“The LLM did not choose this action. A versioned deterministic rule selected it because risk is
above 0.80, an invoice receipt exists, and the required goods receipt is absent.”

### 1:10–1:35 — Approve

**Do**

Show `PENDING APPROVAL`, `Read-back: WAITING`, and “No tool execution yet.” Enter the separate
approver token.

**Say**

“This is a high-impact branch, so Temporal pauses. There are zero tool executions until a separate
approver records an outcome. The initiating operator is not allowed to self-approve.”

Click **Approve simulated hold**.

### 1:35–2:05 — Act and verify

**Do**

Point to the completed status and `Read-back: PASSED`.

**Say**

“The allowlisted tool writes one payment-hold record to the Digital Twin. A separate transaction
then reads it back and compares the observed state with the expected state. The workflow reports
success only when they match.”

### 2:05–2:35 — Audit

**Do**

Scroll to the Decision Ledger.

**Say**

“Every stage is now evidence: model and rule, approval, tool request and response, read-back, and
final result. Each append-only entry commits the previous SHA-256 hash. PostgreSQL rejects updates
and deletes, and the chain is recomputed whenever the decision is read.”

### 2:35–2:50 — Close

**Say**

“The interesting engineering problem was not predicting risk. It was turning that prediction into
a governed, durable, idempotent, and verifiable business action—without giving the LLM decision
authority.”

## Optional 20-second comparison

Click **+ Safe-action demo**.

Explain that risk is approximately `0.6346`, below the high-impact threshold. The deterministic
policy creates one automatic follow-up task, read-back passes, and the five-entry ledger validates
without human approval.

## Terminal acceptance proof

For a concise, repeatable demonstration of both paths:

```bash
make smoke
```

Expected summary:

```text
status: PASSED
model_version: 2
ledger_chains_valid: true
readback_verifications_passed: true
simulation_only: true
```

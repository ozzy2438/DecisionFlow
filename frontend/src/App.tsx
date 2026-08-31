import { useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";

import { ApiError, api } from "./api";
import { pipelineStates, riskTone, shortHash } from "./presentation";
import type { CaseRecord, DecisionRecord, Scenario } from "./types";

function App() {
  const [operatorToken, setOperatorToken] = useState("");
  const [approverToken, setApproverToken] = useState("");
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [selectedCase, setSelectedCase] = useState<CaseRecord | null>(null);
  const [decision, setDecision] = useState<DecisionRecord | null>(null);
  const [approvalReason, setApprovalReason] = useState(
    "Required goods receipt is absent; approve this simulated-only hold.",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const connected = operatorToken.length >= 16;

  const run = useCallback(async <T,>(work: () => Promise<T>): Promise<T | undefined> => {
    setBusy(true);
    setError("");
    try {
      return await work();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unexpected request failure");
      return undefined;
    } finally {
      setBusy(false);
    }
  }, []);

  const refreshCases = useCallback(async () => {
    if (!connected) return;
    const loaded = await run(() => api.listCases(operatorToken));
    if (loaded) setCases(loaded);
  }, [connected, operatorToken, run]);

  useEffect(() => {
    if (connected) void refreshCases();
  }, [connected, refreshCases]);

  async function createAndEvaluate(scenario: Scenario) {
    const created = await run(() => api.createScenario(operatorToken, scenario));
    if (!created) return;
    setSelectedCase(created);
    const evaluated = await run(() => api.evaluate(operatorToken, created.id));
    if (evaluated) setDecision(evaluated);
    await refreshCases();
  }

  async function selectCase(item: CaseRecord) {
    setSelectedCase(item);
    if (!item.decision_ids.length) {
      setDecision(null);
      return;
    }
    const loaded = await run(() => api.getDecision(operatorToken, item.decision_ids[0]));
    if (loaded) setDecision(loaded);
  }

  async function submitApproval(outcome: "APPROVED" | "REJECTED") {
    if (!decision || !approverToken) {
      setError("Enter the separate approver token before submitting an outcome.");
      return;
    }
    const updated = await run(() =>
      api.approve(approverToken, decision.id, outcome, approvalReason),
    );
    if (updated) setDecision(updated);
    if (selectedCase) {
      const refreshed = await run(() => api.getCase(operatorToken, selectedCase.id));
      if (refreshed) setSelectedCase(refreshed);
    }
    await refreshCases();
  }

  const steps = useMemo(() => pipelineStates(decision), [decision]);
  const tone = decision ? riskTone(decision.prediction.risk_score) : "low";

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">DF</div>
          <div>
            <p className="eyebrow">Governed P2P decision twin</p>
            <h1>DecisionFlow</h1>
          </div>
        </div>
        <div className="simulation-chip"><span /> Simulation only · no ERP connection</div>
      </header>

      <section className="pipeline" aria-label="Decision pipeline">
        {steps.map((step, index) => (
          <div className={`pipeline-step ${step.state}`} key={step.label}>
            <span className="step-index">{String(index + 1).padStart(2, "0")}</span>
            <strong>{step.label}</strong>
          </div>
        ))}
      </section>

      <main>
        <section className="credential-bar card">
          <div>
            <p className="eyebrow">Local access</p>
            <h2>Connect with role-scoped tokens</h2>
            <p>Tokens stay in memory and are never written to browser storage.</p>
          </div>
          <label>
            Operator token
            <input
              type="password"
              value={operatorToken}
              onChange={(event) => setOperatorToken(event.target.value)}
              placeholder="operator-local-demo-key"
              autoComplete="off"
            />
          </label>
          <label>
            Approver token
            <input
              type="password"
              value={approverToken}
              onChange={(event) => setApproverToken(event.target.value)}
              placeholder="Needed only for HITL"
              autoComplete="off"
            />
          </label>
          <button className="button secondary" onClick={() => void refreshCases()} disabled={!connected || busy}>
            {connected ? "Refresh" : "Enter operator token"}
          </button>
        </section>

        {error && <div className="error-banner" role="alert">{error}</div>}

        <section className="workspace-grid">
          <aside className="case-rail card">
            <div className="section-heading">
              <div><p className="eyebrow">Twin inbox</p><h2>Open cases</h2></div>
              <span className="count-badge">{cases.length}</span>
            </div>
            <div className="scenario-actions">
              <button className="button primary" disabled={!connected || busy} onClick={() => void createAndEvaluate("LOW")}>
                + Safe-action demo
              </button>
              <button className="button warning" disabled={!connected || busy} onClick={() => void createAndEvaluate("HIGH")}>
                + HITL demo
              </button>
            </div>
            <div className="case-list">
              {cases.map((item) => (
                <button
                  className={`case-row ${selectedCase?.id === item.id ? "selected" : ""}`}
                  key={item.id}
                  onClick={() => void selectCase(item)}
                >
                  <span><strong>{item.display_id}</strong><small>{new Date(item.created_at).toLocaleString()}</small></span>
                  <span className="case-state">{item.decision_ids.length ? "Evaluated" : "New"}</span>
                </button>
              ))}
              {!cases.length && <p className="empty-state">Connect, then create a bounded demo case.</p>}
            </div>
          </aside>

          <section className="decision-column">
            {!decision ? (
              <div className="card hero-empty">
                <div className="empty-orbit">01</div>
                <p className="eyebrow">Single bounded decision</p>
                <h2>Will this open case miss its SLA—and what safe action applies now?</h2>
                <p>Select an evaluated case or run one of the controlled demo profiles.</p>
              </div>
            ) : (
              <>
                <div className="decision-hero card">
                  <div>
                    <div className="decision-meta">
                      <span className={`status-pill ${decision.status.toLowerCase()}`}>{decision.status.replaceAll("_", " ")}</span>
                      <span>Case {selectedCase?.display_id}</span>
                    </div>
                    <p className="eyebrow">Deterministic outcome</p>
                    <h2>{decision.decision.selected_action.replaceAll("_", " ")}</h2>
                    <p className="rule-copy">Rule <code>{decision.decision.rule_id}</code> · {decision.decision.rule_version}</p>
                  </div>
                  <div className={`risk-orb ${tone}`} style={{ "--risk": `${decision.prediction.risk_score * 100}%` } as CSSProperties}>
                    <span>Risk</span>
                    <strong>{(decision.prediction.risk_score * 100).toFixed(1)}%</strong>
                    <small>{tone}</small>
                  </div>
                </div>

                <div className="evidence-grid">
                  <article className="card stat-card"><span>Model</span><strong>v{decision.prediction.model_version}</strong><small>{decision.prediction.model_alias} · {decision.prediction.model_policy_version}</small></article>
                  <article className="card stat-card"><span>Impact</span><strong>{decision.decision.impact}</strong><small>{decision.decision.approval_required ? "Human approval required" : "Allowlisted automation"}</small></article>
                  <article className="card stat-card"><span>Read-back</span><strong>{decision.verifications.at(-1)?.passed ? "PASSED" : decision.status === "PENDING_APPROVAL" ? "WAITING" : "N/A"}</strong><small>{decision.executions.at(-1)?.tool_name ?? "No tool execution yet"}</small></article>
                  <article className="card stat-card"><span>Ledger</span><strong>{decision.ledger_verification.valid ? "VALID" : "FAILED"}</strong><small>{decision.ledger_verification.entries} chained entries</small></article>
                </div>

                {decision.status === "PENDING_APPROVAL" && (
                  <div className="approval-card card">
                    <div><p className="eyebrow">Human in the loop</p><h3>High-impact twin action is paused</h3><p>Operator and approver identities must differ. The selected action cannot run until an approver records an outcome.</p></div>
                    <label>Approval rationale<textarea value={approvalReason} onChange={(event) => setApprovalReason(event.target.value)} /></label>
                    <div className="approval-actions">
                      <button className="button secondary" disabled={busy} onClick={() => void submitApproval("REJECTED")}>Reject</button>
                      <button className="button warning" disabled={busy} onClick={() => void submitApproval("APPROVED")}>Approve simulated hold</button>
                    </div>
                  </div>
                )}

                {decision.explanation && (
                  <article className="explanation-card card">
                    <div className="section-heading"><div><p className="eyebrow">Non-authoritative explanation</p><h3>{decision.explanation.output.summary}</h3></div><span className="provider-chip">{decision.explanation.provider} · {decision.explanation.model}</span></div>
                    <p>{decision.explanation.output.risk_explanation}</p>
                    <blockquote>{decision.explanation.output.message_draft}</blockquote>
                    <p className="authority-note">{decision.explanation.output.authority_statement}</p>
                  </article>
                )}

                <article className="audit-card card">
                  <div className="section-heading"><div><p className="eyebrow">Decision ledger</p><h3>Verified audit chain</h3></div><span className="hash-chip">head {shortHash(decision.ledger_verification.head_hash)}</span></div>
                  <div className="timeline">
                    {decision.ledger.map((entry) => (
                      <div className="timeline-row" key={entry.sequence}>
                        <span className="timeline-dot">{entry.sequence}</span>
                        <div><strong>{entry.event_type.replaceAll("_", " ")}</strong><small>{entry.actor_type} · {entry.actor_id}</small></div>
                        <div className="timeline-end"><time>{new Date(entry.occurred_at).toLocaleTimeString()}</time><code>{shortHash(entry.entry_hash)}</code></div>
                      </div>
                    ))}
                  </div>
                </article>
              </>
            )}
          </section>
        </section>
      </main>
      {busy && <div className="busy-indicator" role="status"><span /> Workflow running</div>}
    </div>
  );
}

export default App;

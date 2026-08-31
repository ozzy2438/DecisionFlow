# DecisionFlow — CV and LinkedIn Copy

## CV bullets

- Built a governed AI decision system over **1.59M real enterprise Purchase-to-Pay events**,
  combining LightGBM risk prediction, deterministic decisioning, Human-in-the-Loop approvals, and
  Temporal-based durable execution with verified actions and an immutable Decision Ledger.
- Engineered a leakage-controlled LightGBM candidate across **183,447 eligible cases**, achieving
  **0.871053 held-out ROC-AUC** and registering versioned parameters, metrics, model artifacts,
  label policy, and source provenance in MLflow.
- Implemented idempotent Digital Twin actions, independent read-back verification, role and
  separation-of-duties controls, and SHA-256 chained audit records; validated worker recovery and
  retries with a real Temporal server plus **60 backend tests and 86.35% branch-aware coverage**.

## LinkedIn project post

I’ve completed **DecisionFlow**, an Applied AI Engineering reference system for governed
Purchase-to-Pay decisions.

It uses the public BPI Challenge 2019 event log—251,734 purchase-order cases and 1,595,923
events—to discover the real process and train a point-in-time LightGBM SLA-risk model. Candidate
version 2 achieved a held-out ROC-AUC of 0.871053 and is tracked with its parameters, metrics,
artifacts, label policy, and data provenance in MLflow.

The interesting part was not predicting risk. It was turning that prediction into a governed,
durable, and verifiable business action.

DecisionFlow keeps those responsibilities separate:

- the model estimates risk;
- deterministic, versioned rules choose the permitted action;
- high-impact actions pause for Human-in-the-Loop approval;
- Temporal persists execution across retries and worker restarts;
- actions run only inside a simulated operational twin;
- independent read-back verifies the applied state;
- an immutable SHA-256 Decision Ledger records the complete chain.

The LLM is deliberately non-authoritative: it can summarize and explain a fixed outcome, but it
cannot choose an action, change a threshold, approve work, call a tool, or run SQL.

This is a production-grade portfolio/reference system, not a claim of deployment inside a live ERP
or payment environment.

GitHub: https://github.com/ozzy2438/DecisionFlow

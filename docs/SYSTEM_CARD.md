# DecisionFlow System Card / Production Evidence

## System status

**Classification:** production-grade portfolio/reference system
**Operational scope:** local governed Digital Twin
**Not claimed:** deployment inside a real enterprise ERP or payment environment

DecisionFlow demonstrates a complete controlled decision path over historical Purchase-to-Pay
data:

`Event → prediction → deterministic decision → optional human approval → simulated action →
read-back verification → immutable audit`

Evidence in this card was last reproduced on 31 August 2026.

## Intended decision

For one open Purchase Order / Invoice case:

> Is this case at risk of missing its assumed SLA, and which safe action is permitted now?

DecisionFlow is not an autonomous purchasing, invoicing, or payment system. It cannot release
funds or mutate a real enterprise system.

## Data and process provenance

| Item | Evidence |
| --- | --- |
| Source | BPI Challenge 2019, 4TU.ResearchData |
| License | CC BY 4.0 |
| DOI | `10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1` |
| Pinned MD5 | `4eb909242351193a61e1c15b9c3cc814` |
| Cases | 251,734 purchase-order line items |
| Events | 1,595,923 |
| Activities | 42 |
| Discovered variants | 11,973 |

The raw XES is not stored in Git. Bootstrap downloads it from the publisher, verifies size and
checksum, streams it into PostgreSQL, and runs count, required-field, referential-integrity, and
case/event consistency gates. Completed imports are immutable at the database layer.

## ML model provenance

| Item | Verified value |
| --- | --- |
| Algorithm | LightGBM binary classifier in a preprocessing pipeline |
| Registry model | `decisionflow-sla-risk` |
| Registered version | 2 |
| Alias | `candidate` |
| Governance status | `CANDIDATE_NOT_PRODUCTION_APPROVED` |
| MLflow run | `2187d3fb3fe549fe95c46c554351a488` |
| Label policy | `sla-proxy-v1` |
| Observation point | seven days after PO-item creation |
| Assumed SLA | 90 days to `Clear Invoice` |
| Eligible cases | 183,447 |
| Chronological split | 128,411 train / 27,511 validation / 27,525 test |
| Test ROC-AUC | 0.871053 |
| Test average precision | 0.661269 |
| Test Brier score | 0.100118 |

MLflow stores the training parameters, validation/test metrics, model artifact, input signature,
example, feature importance, training report, label-policy tag, and source checksum. Runtime
scoring fails closed unless the Registry version is `READY` and carries the expected candidate,
policy, and source-provenance metadata.

There is intentionally no champion/production alias. Promotion requires an owner-approved SLA and
formal model validation.

## Deterministic decision boundary

The model returns a probability only. `p2p-action-policy-v1` owns action selection:

- below `0.50`: no action;
- at least `0.50`: automatic low-impact follow-up task;
- at least `0.80` plus invoice-present/required-GR-missing context: proposed simulated payment hold
  requiring separate approval.

Thresholds are code-controlled and versioned. The LLM receives the fixed result afterward and has
no tool, SQL, threshold, approval, or permission interface.

## Governance and reliability evidence

| Control | Implementation | Reproduced evidence |
| --- | --- | --- |
| Human-in-the-Loop | Temporal waits for an approval signal; RBAC and separation of duties apply | high-impact action had zero executions before approval |
| Unauthorized action blocking | only approver role can signal approval | operator attempt returned HTTP 403; execution count remained zero |
| Durable workflow | Temporal Workflow + two Activities + long-running worker | signal persisted while worker was offline and completed after restart |
| Retry | bounded exponential Activity retry policy | transient failure completed on Activity attempt 2 |
| Idempotency | unique workflow, decision, action, and tool idempotency keys | repeated request returned the same decision and one twin resource |
| Controlled action | compile-time allowlisted Digital Twin tools | no ERP/payment connector exists |
| Read-back verification | new transaction compares canonical expected and observed state | safe and HITL actions both passed with one verification |
| Failure behavior | mismatch records failed verification and fails the workflow | covered by fault-injection integration test |
| Immutable records | PostgreSQL triggers reject updates/deletes | direct ledger update and delete both rejected |
| Audit integrity | per-decision SHA-256 previous-hash chain | five-entry and seven-entry demo ledgers independently validated |
| Model provenance | MLflow Registry alias/version and run tags | candidate v2 resolved to the expected run/checksum/policy |
| Monitoring | Prometheus scrape and alert rules | API `up=1`; config and three rules validated |

## Security boundaries

- All operational cases carry a database-enforced `is_simulated = true` constraint.
- API routes require role-scoped credentials; staging/production configuration fails closed when
  deployment tokens are absent.
- Local demo tokens are synthetic, hashed after startup, held in memory, and never browser-persisted.
- OpenAI is opt-in through `DECISIONFLOW_OPENAI_API_KEY`; the default template profile receives no
  host-level key.
- LLM payloads exclude vendor identity and free-form case context.
- Pydantic rejects unknown request fields and malformed structured explanations.
- CORS is allowlisted; responses carry request IDs and defensive headers.
- Runtime API, worker, and frontend containers execute as non-root users.
- No raw BPI dataset, runtime database, MLflow volume, credential, or `.env` file is committed.

## Testing and delivery evidence

- 60 backend unit/integration tests
- PostgreSQL integration tests with destructive setup restricted to a `*_test` database
- real Temporal server workflow, signal, retry, and recovery tests
- 86.35% branch-aware coverage with an enforced 85% floor
- Ruff and strict mypy
- frontend tests, TypeScript checking, and Vite production build
- npm audit: 0 vulnerabilities
- Docker Compose validation and one-command bootstrap
- Prometheus configuration and alert-rule validation
- GitHub Actions parity for tests, images, and non-root assertions
- Dependabot for Python, npm, Docker, and GitHub Actions

## Monitoring

DecisionFlow exposes:

- `/health/live` for process liveness;
- `/health/ready` for PostgreSQL, MLflow, and Temporal readiness;
- `/metrics` for request count, error, and latency telemetry.

The local Prometheus profile includes API-down, sustained-5xx, and p95-latency rules. Alert routing
is intentionally deployment-specific.

## Known limitations

1. The 90-day SLA is a transparent proxy, not a ground-truth corporate commitment.
2. Offline model performance is not evidence of effectiveness on another organization or future
   distribution.
3. Historical research data cannot prove current organizational behavior or business impact.
4. Actions are simulated; no live ERP/payment integration has been tested or claimed.
5. The default explanation is a deterministic fallback. Live OpenAI output requires a separately
   authorized valid credential.
6. Real deployment requires approved thresholds, model promotion, managed secrets, TLS, backup and
   recovery, retention, privacy/security review, drift and bias evaluation, load testing, incident
   response, and alert routing.
7. DecisionFlow and PM4Py Community are AGPL-3.0; closed-source commercial use must resolve
   applicable licensing obligations.

## Appropriate use

DecisionFlow is appropriate for:

- portfolio and architecture review;
- local reproducible demonstration;
- governed-AI design discussion;
- process-mining and decision-intelligence experimentation on the licensed source data.

It is not appropriate for authorizing real payments, bypassing organizational controls, or
representing a production deployment without the owner gates above.

## Reproduce the evidence

```bash
make up
make test
make lint
make frontend-test
make smoke
make monitoring
```

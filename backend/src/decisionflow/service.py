"""Transactional governed-decision application service and Digital Twin tools."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from decisionflow.decision_engine import (
    DEFAULT_DECISION_POLICY,
    Action,
    DecisionPolicy,
    select_action,
)
from decisionflow.ledger import append_ledger_event, canonical_json, utc_now, verify_decision_ledger
from decisionflow.models import (
    ApprovalRecord,
    DecisionExplanation,
    DecisionLedgerEntry,
    DecisionRecord,
    ReadbackVerification,
    ToolExecution,
    TwinCase,
    TwinFollowUpTask,
    TwinPaymentHold,
)
from decisionflow.scoring import RiskScorer, feature_hash, normalize_features


class NotFoundError(LookupError):
    pass


class ConflictError(RuntimeError):
    pass


class ApprovalRequiredError(RuntimeError):
    pass


class SeparationOfDutiesError(RuntimeError):
    pass


class VerificationError(RuntimeError):
    pass


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


class GovernedDecisionService:
    """Own the only path from a risk score to a simulated operational mutation."""

    def __init__(
        self,
        engine: Engine,
        scorer: RiskScorer,
        *,
        policy: DecisionPolicy = DEFAULT_DECISION_POLICY,
    ) -> None:
        self.engine = engine
        self.scorer = scorer
        self.policy = policy
        self.sessions = sessionmaker(engine, expire_on_commit=False)

    def create_twin_case(
        self,
        *,
        features: dict[str, Any],
        case_context: dict[str, Any] | None = None,
        display_id: str | None = None,
        source_case_id: str | None = None,
        snapshot_at: datetime | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_features(features)
        case_id = uuid4()
        safe_display_id = display_id or f"SIM-{str(case_id)[:8].upper()}"
        if not safe_display_id or len(safe_display_id) > 80:
            raise ValueError("display_id must contain 1 to 80 characters")
        now = utc_now()
        record = TwinCase(
            id=case_id,
            display_id=safe_display_id,
            source_case_id=source_case_id,
            is_simulated=True,
            status="OPEN",
            snapshot_at=(snapshot_at or now).astimezone(UTC),
            feature_snapshot=normalized,
            case_context=case_context or {},
            created_at=now,
        )
        try:
            with self.sessions.begin() as session:
                session.add(record)
        except IntegrityError as exc:
            raise ConflictError(f"Twin case display_id already exists: {safe_display_id}") from exc
        return self.get_case(case_id)

    def get_case(self, case_id: UUID) -> dict[str, Any]:
        with self.sessions() as session:
            record = session.get(TwinCase, case_id)
            if record is None:
                raise NotFoundError(f"Twin case not found: {case_id}")
            decisions = list(
                session.scalars(
                    select(DecisionRecord)
                    .where(DecisionRecord.twin_case_id == case_id)
                    .order_by(DecisionRecord.created_at.desc())
                )
            )
            follow_ups = list(
                session.scalars(
                    select(TwinFollowUpTask).where(TwinFollowUpTask.twin_case_id == case_id)
                )
            )
            holds = list(
                session.scalars(
                    select(TwinPaymentHold).where(TwinPaymentHold.twin_case_id == case_id)
                )
            )
            return {
                "id": str(record.id),
                "display_id": record.display_id,
                "source_case_id": record.source_case_id,
                "is_simulated": record.is_simulated,
                "status": record.status,
                "snapshot_at": _iso(record.snapshot_at),
                "feature_snapshot": record.feature_snapshot,
                "case_context": record.case_context,
                "created_at": _iso(record.created_at),
                "decision_ids": [str(item.id) for item in decisions],
                "twin_state": {
                    "open_follow_up_tasks": len(follow_ups),
                    "active_payment_holds": sum(item.active for item in holds),
                },
            }

    def list_cases(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self.sessions() as session:
            ids = list(
                session.scalars(
                    select(TwinCase.id).order_by(TwinCase.created_at.desc()).limit(limit)
                )
            )
        return [self.get_case(case_id) for case_id in ids]

    def evaluate_case(
        self,
        case_id: UUID,
        *,
        idempotency_key: str,
        workflow_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        if not idempotency_key or len(idempotency_key) > 200:
            raise ValueError("idempotency_key must contain 1 to 200 characters")
        if not workflow_id or len(workflow_id) > 200:
            raise ValueError("workflow_id must contain 1 to 200 characters")
        with self.sessions() as session:
            existing = session.scalar(
                select(DecisionRecord).where(DecisionRecord.idempotency_key == idempotency_key)
            )
            if existing is not None:
                if existing.twin_case_id != case_id:
                    raise ConflictError("Idempotency key was already used for another case")
                return self.get_decision(existing.id)
            twin_case = session.get(TwinCase, case_id)
            if twin_case is None:
                raise NotFoundError(f"Twin case not found: {case_id}")
            if twin_case.status != "OPEN":
                raise ConflictError("Only an open twin case can be evaluated")
            features = dict(twin_case.feature_snapshot)

        prediction = self.scorer.score(features)
        selection = select_action(prediction.score, features, self.policy)
        decision_id = uuid4()
        now = utc_now()
        record = DecisionRecord(
            id=decision_id,
            twin_case_id=case_id,
            workflow_id=workflow_id,
            idempotency_key=idempotency_key,
            created_at=now,
            model_name=prediction.model_name,
            model_version=prediction.model_version,
            model_alias=prediction.model_alias,
            model_policy_version=prediction.model_policy_version,
            risk_score=prediction.score,
            rule_id=selection.rule_id,
            rule_version=self.policy.version,
            selected_action=selection.action.value,
            impact=selection.impact.value,
            approval_required=selection.approval_required,
            feature_snapshot=features,
            input_hash=feature_hash(features),
        )
        try:
            with self.sessions.begin() as session:
                # Serialize evaluations on a case, then repeat the idempotency check.
                session.execute(
                    select(TwinCase.id).where(TwinCase.id == case_id).with_for_update()
                ).scalar_one()
                existing = session.scalar(
                    select(DecisionRecord).where(
                        DecisionRecord.idempotency_key == idempotency_key
                    )
                )
                if existing is not None:
                    return self.get_decision(existing.id)
                session.add(record)
                session.flush()
                append_ledger_event(
                    session,
                    decision_id=decision_id,
                    event_type="DECISION_RECORDED",
                    actor_type="USER",
                    actor_id=actor_id,
                    payload={
                        "model": {
                            "name": prediction.model_name,
                            "version": prediction.model_version,
                            "alias": prediction.model_alias,
                            "policy_version": prediction.model_policy_version,
                        },
                        "risk_score": prediction.score,
                        "input_hash": record.input_hash,
                        "rule": {
                            "id": selection.rule_id,
                            "version": self.policy.version,
                            "reason": selection.reason,
                            "action_threshold": self.policy.action_threshold,
                            "high_risk_threshold": self.policy.high_risk_threshold,
                        },
                        "selected_action": selection.action.value,
                        "impact": selection.impact.value,
                        "approval_required": selection.approval_required,
                        "simulation": True,
                    },
                )
                if selection.action is Action.NO_ACTION:
                    append_ledger_event(
                        session,
                        decision_id=decision_id,
                        event_type="WORKFLOW_COMPLETED",
                        actor_type="SYSTEM",
                        actor_id="decisionflow-worker",
                        payload={"final_result": "NO_ACTION_REQUIRED", "verified": True},
                    )
                elif selection.approval_required:
                    append_ledger_event(
                        session,
                        decision_id=decision_id,
                        event_type="APPROVAL_REQUESTED",
                        actor_type="SYSTEM",
                        actor_id="decisionflow-worker",
                        payload={
                            "action": selection.action.value,
                            "impact": selection.impact.value,
                        },
                    )
        except IntegrityError as exc:
            with self.sessions() as session:
                existing = session.scalar(
                    select(DecisionRecord).where(
                        DecisionRecord.idempotency_key == idempotency_key
                    )
                )
                if existing is not None and existing.twin_case_id == case_id:
                    return self.get_decision(existing.id)
            raise ConflictError("Decision idempotency conflict") from exc

        if selection.action is Action.CREATE_FOLLOW_UP_TASK:
            self.execute_decision(decision_id)
        return self.get_decision(decision_id)

    def submit_approval(
        self,
        decision_id: UUID,
        *,
        outcome: str,
        approver_id: str,
        reason: str,
    ) -> dict[str, Any]:
        normalized_outcome = outcome.upper()
        if normalized_outcome not in {"APPROVED", "REJECTED"}:
            raise ValueError("Approval outcome must be APPROVED or REJECTED")
        if not 3 <= len(reason.strip()) <= 1000:
            raise ValueError("Approval reason must contain 3 to 1000 characters")
        with self.sessions.begin() as session:
            decision = session.get(DecisionRecord, decision_id)
            if decision is None:
                raise NotFoundError(f"Decision not found: {decision_id}")
            if not decision.approval_required:
                raise ConflictError("This decision does not require approval")
            existing = session.scalar(
                select(ApprovalRecord).where(ApprovalRecord.decision_id == decision_id)
            )
            if existing is not None:
                if existing.outcome == normalized_outcome and existing.approver_id == approver_id:
                    return self.get_decision(decision_id)
                raise ConflictError("An approval outcome is already recorded for this decision")
            initiating_actor = session.scalar(
                select(DecisionLedgerEntry.actor_id).where(
                    DecisionLedgerEntry.decision_id == decision_id,
                    DecisionLedgerEntry.sequence == 1,
                )
            )
            if initiating_actor == approver_id:
                raise SeparationOfDutiesError(
                    "The initiating operator cannot approve the same high-impact decision"
                )
            approval = ApprovalRecord(
                id=uuid4(),
                decision_id=decision_id,
                outcome=normalized_outcome,
                approver_id=approver_id,
                reason=reason.strip(),
                created_at=utc_now(),
            )
            session.add(approval)
            append_ledger_event(
                session,
                decision_id=decision_id,
                event_type=f"APPROVAL_{normalized_outcome}",
                actor_type="HUMAN_APPROVER",
                actor_id=approver_id,
                payload={"outcome": normalized_outcome, "reason": reason.strip()},
            )
            if normalized_outcome == "REJECTED":
                append_ledger_event(
                    session,
                    decision_id=decision_id,
                    event_type="WORKFLOW_COMPLETED",
                    actor_type="SYSTEM",
                    actor_id="decisionflow-worker",
                    payload={"final_result": "ACTION_REJECTED", "verified": True},
                )

        if normalized_outcome == "APPROVED":
            self.execute_decision(decision_id)
        return self.get_decision(decision_id)

    def execute_decision(self, decision_id: UUID) -> dict[str, Any]:
        """Invoke one of two compile-time allowlisted Digital Twin mutations."""

        with self.sessions.begin() as session:
            decision = session.execute(
                select(DecisionRecord)
                .where(DecisionRecord.id == decision_id)
                .with_for_update()
            ).scalar_one_or_none()
            if decision is None:
                raise NotFoundError(f"Decision not found: {decision_id}")
            existing_execution = session.scalar(
                select(ToolExecution)
                .where(
                    ToolExecution.decision_id == decision_id,
                    ToolExecution.status == "SUCCEEDED",
                )
                .order_by(ToolExecution.attempt.desc())
            )
            if existing_execution is not None:
                execution_id = existing_execution.id
            else:
                if decision.selected_action == Action.NO_ACTION.value:
                    raise ConflictError("NO_ACTION decisions cannot invoke a tool")
                if decision.approval_required:
                    approval = session.scalar(
                        select(ApprovalRecord).where(ApprovalRecord.decision_id == decision_id)
                    )
                    if approval is None or approval.outcome != "APPROVED":
                        raise ApprovalRequiredError("High-impact action requires human approval")
                previous_attempts = session.scalar(
                    select(func.count(ToolExecution.id)).where(
                        ToolExecution.decision_id == decision_id
                    )
                )
                attempt = int(previous_attempts or 0) + 1
                action_key = f"{decision_id}:{decision.selected_action}:apply"
                request_payload = {
                    "decision_id": str(decision.id),
                    "twin_case_id": str(decision.twin_case_id),
                    "action": decision.selected_action,
                    "simulation": True,
                    "action_idempotency_key": action_key,
                }
                response_payload = self._apply_allowlisted_tool(
                    session, decision=decision, action_key=action_key
                )
                execution = ToolExecution(
                    id=uuid4(),
                    decision_id=decision_id,
                    attempt=attempt,
                    tool_name=self._tool_name(decision.selected_action),
                    idempotency_key=f"{action_key}:attempt:{attempt}",
                    request_payload=request_payload,
                    response_payload=response_payload,
                    status="SUCCEEDED",
                    created_at=utc_now(),
                )
                session.add(execution)
                session.flush()
                execution_id = execution.id
                append_ledger_event(
                    session,
                    decision_id=decision_id,
                    event_type="TOOL_EXECUTION_SUCCEEDED",
                    actor_type="SYSTEM",
                    actor_id="digital-twin-tool-runner",
                    payload={
                        "execution_id": str(execution.id),
                        "tool_name": execution.tool_name,
                        "attempt": attempt,
                        "request": request_payload,
                        "response": response_payload,
                    },
                )

        # A new transaction deliberately reads the committed twin state back from PostgreSQL.
        self._verify_execution(execution_id)
        return self.get_decision(decision_id)

    def record_explanation(
        self,
        decision_id: UUID,
        *,
        provider: str,
        model: str,
        status: str,
        prompt_version: str,
        input_hash: str,
        output: dict[str, Any],
    ) -> dict[str, Any]:
        with self.sessions.begin() as session:
            decision = session.get(DecisionRecord, decision_id)
            if decision is None:
                raise NotFoundError(f"Decision not found: {decision_id}")
            existing = session.scalar(
                select(DecisionExplanation).where(
                    DecisionExplanation.decision_id == decision_id
                )
            )
            if existing is None:
                explanation = DecisionExplanation(
                    id=uuid4(),
                    decision_id=decision_id,
                    provider=provider,
                    model=model,
                    status=status,
                    prompt_version=prompt_version,
                    input_hash=input_hash,
                    output=output,
                    created_at=utc_now(),
                )
                session.add(explanation)
                session.flush()
                append_ledger_event(
                    session,
                    decision_id=decision_id,
                    event_type="EXPLANATION_RECORDED",
                    actor_type="LLM" if status == "GENERATED" else "SYSTEM",
                    actor_id=f"{provider}:{model}",
                    payload={
                        "explanation_id": str(explanation.id),
                        "provider": provider,
                        "model": model,
                        "status": status,
                        "prompt_version": prompt_version,
                        "input_hash": input_hash,
                        "non_authoritative": True,
                    },
                )
            elif (
                existing.provider != provider
                or existing.model != model
                or existing.input_hash != input_hash
                or existing.output != output
            ):
                raise ConflictError("A different immutable explanation already exists")
        return self.get_decision(decision_id)

    def _apply_allowlisted_tool(
        self, session: Session, *, decision: DecisionRecord, action_key: str
    ) -> dict[str, Any]:
        now = utc_now()
        if decision.selected_action == Action.CREATE_FOLLOW_UP_TASK.value:
            existing = session.scalar(
                select(TwinFollowUpTask).where(TwinFollowUpTask.idempotency_key == action_key)
            )
            if existing is None:
                existing = TwinFollowUpTask(
                    id=uuid4(),
                    twin_case_id=decision.twin_case_id,
                    decision_id=decision.id,
                    idempotency_key=action_key,
                    queue="AP_EXCEPTION_FOLLOW_UP",
                    status="OPEN",
                    due_at=now + timedelta(hours=24),
                    created_at=now,
                )
                session.add(existing)
                session.flush()
            return {
                "resource_type": "SIMULATED_FOLLOW_UP_TASK",
                "resource_id": str(existing.id),
                "status": existing.status,
                "queue": existing.queue,
                "simulation": True,
            }
        if decision.selected_action == Action.PLACE_SIMULATED_PAYMENT_HOLD.value:
            existing_hold = session.scalar(
                select(TwinPaymentHold).where(TwinPaymentHold.idempotency_key == action_key)
            )
            if existing_hold is None:
                existing_hold = TwinPaymentHold(
                    id=uuid4(),
                    twin_case_id=decision.twin_case_id,
                    decision_id=decision.id,
                    idempotency_key=action_key,
                    reason_code="SLA_RISK_MISSING_REQUIRED_GR",
                    active=True,
                    created_at=now,
                )
                session.add(existing_hold)
                session.flush()
            return {
                "resource_type": "SIMULATED_PAYMENT_HOLD",
                "resource_id": str(existing_hold.id),
                "active": existing_hold.active,
                "reason_code": existing_hold.reason_code,
                "simulation": True,
            }
        raise ConflictError(f"Action is not allowlisted: {decision.selected_action}")

    @staticmethod
    def _tool_name(action: str) -> str:
        tools = {
            Action.CREATE_FOLLOW_UP_TASK.value: "digital_twin.create_follow_up_task",
            Action.PLACE_SIMULATED_PAYMENT_HOLD.value: "digital_twin.place_payment_hold",
        }
        try:
            return tools[action]
        except KeyError as exc:
            raise ConflictError(f"Action is not allowlisted: {action}") from exc

    def _verify_execution(self, execution_id: UUID) -> None:
        verification_failed = False
        with self.sessions.begin() as session:
            execution = session.execute(
                select(ToolExecution)
                .where(ToolExecution.id == execution_id)
                .with_for_update()
            ).scalar_one_or_none()
            if execution is None:
                raise NotFoundError(f"Tool execution not found: {execution_id}")
            prior = session.scalar(
                select(ReadbackVerification).where(
                    ReadbackVerification.execution_id == execution_id
                )
            )
            if prior is not None:
                if not prior.passed:
                    raise VerificationError("The previously recorded read-back verification failed")
                return
            action_key = str(execution.request_payload["action_idempotency_key"])
            expected = dict(execution.response_payload)
            if execution.tool_name == "digital_twin.create_follow_up_task":
                task = session.scalar(
                    select(TwinFollowUpTask).where(TwinFollowUpTask.idempotency_key == action_key)
                )
                observed = (
                    {
                        "resource_type": "SIMULATED_FOLLOW_UP_TASK",
                        "resource_id": str(task.id),
                        "status": task.status,
                        "queue": task.queue,
                        "simulation": True,
                    }
                    if task is not None
                    else {"resource_type": "SIMULATED_FOLLOW_UP_TASK", "missing": True}
                )
            elif execution.tool_name == "digital_twin.place_payment_hold":
                hold = session.scalar(
                    select(TwinPaymentHold).where(TwinPaymentHold.idempotency_key == action_key)
                )
                observed = (
                    {
                        "resource_type": "SIMULATED_PAYMENT_HOLD",
                        "resource_id": str(hold.id),
                        "active": hold.active,
                        "reason_code": hold.reason_code,
                        "simulation": True,
                    }
                    if hold is not None
                    else {"resource_type": "SIMULATED_PAYMENT_HOLD", "missing": True}
                )
            else:
                raise ConflictError(f"Tool is not allowlisted: {execution.tool_name}")
            passed = canonical_json(expected) == canonical_json(observed)
            verification = ReadbackVerification(
                id=uuid4(),
                execution_id=execution_id,
                expected_state=expected,
                observed_state=observed,
                passed=passed,
                created_at=utc_now(),
            )
            session.add(verification)
            session.flush()
            append_ledger_event(
                session,
                decision_id=execution.decision_id,
                event_type=(
                    "READBACK_VERIFICATION_PASSED"
                    if passed
                    else "READBACK_VERIFICATION_FAILED"
                ),
                actor_type="SYSTEM",
                actor_id="digital-twin-verifier",
                payload={
                    "verification_id": str(verification.id),
                    "execution_id": str(execution_id),
                    "expected": expected,
                    "observed": observed,
                    "passed": passed,
                },
            )
            append_ledger_event(
                session,
                decision_id=execution.decision_id,
                event_type="WORKFLOW_COMPLETED" if passed else "WORKFLOW_FAILED",
                actor_type="SYSTEM",
                actor_id="decisionflow-worker",
                payload={
                    "final_result": "ACTION_APPLIED_AND_VERIFIED"
                    if passed
                    else "ACTION_VERIFICATION_FAILED",
                    "verified": passed,
                },
            )
            verification_failed = not passed
        if verification_failed:
            raise VerificationError("Digital Twin read-back did not match the expected state")

    def get_decision(self, decision_id: UUID) -> dict[str, Any]:
        with self.sessions() as session:
            decision = session.get(DecisionRecord, decision_id)
            if decision is None:
                raise NotFoundError(f"Decision not found: {decision_id}")
            approval = session.scalar(
                select(ApprovalRecord).where(ApprovalRecord.decision_id == decision_id)
            )
            explanation = session.scalar(
                select(DecisionExplanation).where(
                    DecisionExplanation.decision_id == decision_id
                )
            )
            executions = list(
                session.scalars(
                    select(ToolExecution)
                    .where(ToolExecution.decision_id == decision_id)
                    .order_by(ToolExecution.attempt)
                )
            )
            verifications: list[ReadbackVerification] = []
            for execution in executions:
                verification = session.scalar(
                    select(ReadbackVerification).where(
                        ReadbackVerification.execution_id == execution.id
                    )
                )
                if verification is not None:
                    verifications.append(verification)
            ledger = list(
                session.scalars(
                    select(DecisionLedgerEntry)
                    .where(DecisionLedgerEntry.decision_id == decision_id)
                    .order_by(DecisionLedgerEntry.sequence)
                )
            )
            status = self._derive_status(decision, approval, executions, verifications, ledger)
            verification_report = verify_decision_ledger(session, decision_id)
            return {
                "id": str(decision.id),
                "twin_case_id": str(decision.twin_case_id),
                "workflow_id": decision.workflow_id,
                "idempotency_key": decision.idempotency_key,
                "created_at": _iso(decision.created_at),
                "status": status,
                "simulation": True,
                "prediction": {
                    "model_name": decision.model_name,
                    "model_version": decision.model_version,
                    "model_alias": decision.model_alias,
                    "model_policy_version": decision.model_policy_version,
                    "risk_score": decision.risk_score,
                    "input_hash": decision.input_hash,
                },
                "decision": {
                    "rule_id": decision.rule_id,
                    "rule_version": decision.rule_version,
                    "selected_action": decision.selected_action,
                    "impact": decision.impact,
                    "approval_required": decision.approval_required,
                },
                "approval": (
                    {
                        "outcome": approval.outcome,
                        "approver_id": approval.approver_id,
                        "reason": approval.reason,
                        "created_at": _iso(approval.created_at),
                    }
                    if approval is not None
                    else None
                ),
                "explanation": (
                    {
                        "provider": explanation.provider,
                        "model": explanation.model,
                        "status": explanation.status,
                        "prompt_version": explanation.prompt_version,
                        "input_hash": explanation.input_hash,
                        "output": explanation.output,
                        "created_at": _iso(explanation.created_at),
                        "non_authoritative": True,
                    }
                    if explanation is not None
                    else None
                ),
                "executions": [
                    {
                        "id": str(item.id),
                        "attempt": item.attempt,
                        "tool_name": item.tool_name,
                        "status": item.status,
                        "request": item.request_payload,
                        "response": item.response_payload,
                        "created_at": _iso(item.created_at),
                    }
                    for item in executions
                ],
                "verifications": [
                    {
                        "id": str(item.id),
                        "execution_id": str(item.execution_id),
                        "passed": item.passed,
                        "expected": item.expected_state,
                        "observed": item.observed_state,
                        "created_at": _iso(item.created_at),
                    }
                    for item in verifications
                ],
                "ledger": [
                    {
                        "sequence": item.sequence,
                        "event_type": item.event_type,
                        "actor_type": item.actor_type,
                        "actor_id": item.actor_id,
                        "occurred_at": _iso(item.occurred_at),
                        "payload": item.payload,
                        "previous_hash": item.previous_hash,
                        "entry_hash": item.entry_hash,
                    }
                    for item in ledger
                ],
                "ledger_verification": verification_report,
            }

    def get_decision_by_workflow_id(self, workflow_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            decision_id = session.scalar(
                select(DecisionRecord.id).where(DecisionRecord.workflow_id == workflow_id)
            )
        if decision_id is None:
            raise NotFoundError(f"Decision workflow not found: {workflow_id}")
        return self.get_decision(decision_id)

    @staticmethod
    def _derive_status(
        decision: DecisionRecord,
        approval: ApprovalRecord | None,
        executions: list[ToolExecution],
        verifications: list[ReadbackVerification],
        ledger: list[DecisionLedgerEntry],
    ) -> str:
        if ledger and ledger[-1].event_type == "WORKFLOW_FAILED":
            return "FAILED"
        if decision.approval_required and approval is None:
            return "PENDING_APPROVAL"
        if approval is not None and approval.outcome == "REJECTED":
            return "REJECTED"
        if decision.selected_action == Action.NO_ACTION.value:
            return "COMPLETED"
        if verifications and verifications[-1].passed:
            return "COMPLETED"
        if executions:
            return "VERIFYING"
        return "READY_TO_EXECUTE"

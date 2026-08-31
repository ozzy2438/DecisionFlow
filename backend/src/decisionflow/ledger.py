"""Append and verify a per-decision, SHA-256-linked audit ledger."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from decisionflow.models import DecisionLedgerEntry, DecisionRecord

GENESIS_HASH = "0" * 64


def utc_now() -> datetime:
    return datetime.now(UTC)


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def calculate_entry_hash(
    *,
    decision_id: UUID,
    sequence: int,
    event_type: str,
    actor_type: str,
    actor_id: str,
    occurred_at: datetime,
    payload: dict[str, Any],
    previous_hash: str,
) -> str:
    material = {
        "actor_id": actor_id,
        "actor_type": actor_type,
        "decision_id": str(decision_id),
        "event_type": event_type,
        "occurred_at": occurred_at.astimezone(UTC).isoformat(timespec="microseconds"),
        "payload": payload,
        "previous_hash": previous_hash,
        "sequence": sequence,
    }
    return hashlib.sha256(canonical_json(material).encode()).hexdigest()


def append_ledger_event(
    session: Session,
    *,
    decision_id: UUID,
    event_type: str,
    actor_type: str,
    actor_id: str,
    payload: dict[str, Any],
    occurred_at: datetime | None = None,
) -> DecisionLedgerEntry:
    # Row lock serializes sequence allocation for concurrent activities and approval signals.
    session.execute(
        select(DecisionRecord.id).where(DecisionRecord.id == decision_id).with_for_update()
    ).scalar_one()
    previous = session.scalars(
        select(DecisionLedgerEntry)
        .where(DecisionLedgerEntry.decision_id == decision_id)
        .order_by(DecisionLedgerEntry.sequence.desc())
        .limit(1)
    ).first()
    sequence = 1 if previous is None else previous.sequence + 1
    previous_hash = GENESIS_HASH if previous is None else previous.entry_hash
    timestamp = (occurred_at or utc_now()).astimezone(UTC)
    entry = DecisionLedgerEntry(
        decision_id=decision_id,
        sequence=sequence,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        occurred_at=timestamp,
        payload=payload,
        previous_hash=previous_hash,
        entry_hash=calculate_entry_hash(
            decision_id=decision_id,
            sequence=sequence,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            occurred_at=timestamp,
            payload=payload,
            previous_hash=previous_hash,
        ),
    )
    session.add(entry)
    session.flush()
    return entry


def verify_decision_ledger(session: Session, decision_id: UUID) -> dict[str, Any]:
    entries = list(
        session.scalars(
            select(DecisionLedgerEntry)
            .where(DecisionLedgerEntry.decision_id == decision_id)
            .order_by(DecisionLedgerEntry.sequence)
        )
    )
    errors: list[str] = []
    expected_previous = GENESIS_HASH
    for expected_sequence, entry in enumerate(entries, start=1):
        if entry.sequence != expected_sequence:
            errors.append(f"sequence gap at {expected_sequence}")
        if entry.previous_hash != expected_previous:
            errors.append(f"previous hash mismatch at sequence {entry.sequence}")
        recalculated = calculate_entry_hash(
            decision_id=entry.decision_id,
            sequence=entry.sequence,
            event_type=entry.event_type,
            actor_type=entry.actor_type,
            actor_id=entry.actor_id,
            occurred_at=entry.occurred_at,
            payload=entry.payload,
            previous_hash=entry.previous_hash,
        )
        if recalculated != entry.entry_hash:
            errors.append(f"entry hash mismatch at sequence {entry.sequence}")
        expected_previous = entry.entry_hash
    return {
        "decision_id": str(decision_id),
        "entries": len(entries),
        "head_hash": expected_previous,
        "valid": bool(entries) and not errors,
        "errors": errors,
    }

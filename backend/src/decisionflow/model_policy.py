"""Versioned, non-LLM policy for constructing the SLA prediction target."""

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelPolicy:
    """Governed proxy-label policy for the first DecisionFlow model.

    BPI 2019 has no source SLA label. Version 1 therefore uses a transparent 90-day completion
    target and scores cases that are still open seven days after PO creation. The 90-day target is
    an ASSUMED demo policy, not a fact asserted by the source company.
    """

    version: str = "sla-proxy-v1"
    observation_horizon_days: int = 7
    sla_days: int = 90
    cohort_start: date = date(2018, 1, 1)
    cohort_end_exclusive: date = date(2019, 1, 1)
    start_activity: str = "Create Purchase Order Item"
    completion_activity: str = "Clear Invoice"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["cohort_start"] = self.cohort_start.isoformat()
        payload["cohort_end_exclusive"] = self.cohort_end_exclusive.isoformat()
        payload["evidence_status"] = "ASSUMED_OPERATIONAL_POLICY"
        return payload


DEFAULT_MODEL_POLICY = ModelPolicy()

"""Small serializable contracts for organization plans."""

from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from uuid import uuid4


PLAN_SCHEMA_VERSION = 1


class RiskLevel(StrEnum):
    NORMAL = "normal"
    REVIEW_REQUIRED = "review_required"
    PROTECTED = "protected"


class PlanStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    INVALID = "invalid"


@dataclass(frozen=True)
class PlanItem:
    file_id: int
    source: Path
    relative_destination: str
    fingerprint_size: int
    fingerprint_mtime_ns: int
    reason: str
    risk_level: RiskLevel = RiskLevel.NORMAL
    evidence: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SkippedItem:
    source: Path
    code: str
    reason: str
    file_id: int | None = None


@dataclass(frozen=True)
class PlanWarning:
    code: str
    message: str
    file_id: int | None = None


@dataclass(frozen=True)
class PlanningMetrics:
    candidates_considered: int = 0
    discarded_by_policy: int = 0
    resolved_by_rules: int = 0
    resolved_by_preferences: int = 0
    sent_to_ai: int = 0
    inference_count: int = 0
    duration_ms: int = 0

    @property
    def considered(self) -> int:
        return self.candidates_considered

    @property
    def policy_rejected(self) -> int:
        return self.discarded_by_policy

    @property
    def rules_resolved(self) -> int:
        return self.resolved_by_rules

    @property
    def preferences_resolved(self) -> int:
        return self.resolved_by_preferences


@dataclass(frozen=True)
class OrganizationPlan:
    id: str
    revision: int
    request_text: str
    status: PlanStatus
    items: tuple[PlanItem, ...] = field(default_factory=tuple)
    skipped: tuple[SkippedItem, ...] = field(default_factory=tuple)
    warnings: tuple[PlanWarning, ...] = field(default_factory=tuple)
    metrics: PlanningMetrics = field(default_factory=PlanningMetrics)
    schema_version: int = PLAN_SCHEMA_VERSION

    @classmethod
    def draft(
        cls,
        request_text: str,
        *,
        items: tuple[PlanItem, ...] = (),
        skipped: tuple[SkippedItem, ...] = (),
        warnings: tuple[PlanWarning, ...] = (),
        metrics: PlanningMetrics | None = None,
    ) -> "OrganizationPlan":
        return cls(
            id=str(uuid4()), revision=1, request_text=request_text,
            status=PlanStatus.DRAFT, items=items, skipped=skipped,
            warnings=warnings, metrics=metrics or PlanningMetrics(),
        )

    def edited(self, **changes: object) -> "OrganizationPlan":
        """Return the next immutable revision of this plan."""
        if "id" in changes or "revision" in changes or "schema_version" in changes:
            raise ValueError("La identidad, revisión y esquema del plan no son editables.")
        return replace(self, revision=self.revision + 1, status=PlanStatus.DRAFT, **changes)

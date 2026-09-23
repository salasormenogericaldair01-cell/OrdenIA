"""Create, validate and persist a plan without executing file operations."""

from dataclasses import dataclass, replace
from time import perf_counter

from ordenia.database.repositories import Repository

from .candidate_models import CandidateFile, CandidateGroup, CandidateSearchResult
from .destination_resolver import DestinationResolution, DestinationResolver
from .intent_models import IntentState, OrganizationIntent
from .models import OrganizationPlan, PlanItem, PlanWarning, PlanningMetrics, SkippedItem
from .policy import AutomationPolicy
from .validator import PlanValidator


class PlanningNeedsClarification(ValueError):
    pass


@dataclass(frozen=True)
class PlanningOutcome:
    intent: OrganizationIntent
    candidates: CandidateSearchResult | None = None
    groups: tuple[CandidateGroup, ...] = ()
    plan: OrganizationPlan | None = None
    inference_count: int = 0
    provider_error: str = ""

    @property
    def ready(self) -> bool:
        return self.plan is not None and not self.intent.clarification_required


class BatchPlanner:
    def __init__(
        self, repository: Repository, resolver: DestinationResolver,
        policy: AutomationPolicy | None = None,
    ) -> None:
        self.repository = repository
        self.resolver = resolver
        self.validator = PlanValidator(repository, policy)

    def build(
        self, intent: OrganizationIntent, result: CandidateSearchResult,
        groups: tuple[CandidateGroup, ...], *, user_skipped: tuple[SkippedItem, ...] = (),
        sent_to_ai: int = 0, inference_count: int = 0,
        started_at: float | None = None,
    ) -> OrganizationPlan:
        files = result.reviewable_candidates
        if not files:
            raise PlanningNeedsClarification("No se encontraron archivos elegibles para preparar el plan.")

        resolutions: dict[int, DestinationResolution] = {}
        grouped_ids: set[int] = set()
        for group in groups:
            resolution = self.resolver.resolve(intent, group.files, group)
            if resolution is None:
                raise PlanningNeedsClarification("No se pudo determinar un destino relativo seguro para el grupo.")
            for file in group.files:
                resolutions[file.file_id] = resolution
                grouped_ids.add(file.file_id)
        remaining = tuple(file for file in files if file.file_id not in grouped_ids)
        if remaining:
            resolution = self.resolver.resolve(intent, remaining)
            if resolution is None:
                raise PlanningNeedsClarification("Indica en qué carpeta relativa deben organizarse los archivos.")
            for file in remaining:
                resolutions[file.file_id] = resolution

        items: list[PlanItem] = []
        warnings: list[PlanWarning] = []
        preference_resolved = 0
        for file in files:
            resolution = resolutions[file.file_id]
            evidence = tuple(dict.fromkeys((*file.evidence, *resolution.evidence)))
            items.append(PlanItem(
                file.file_id, file.path, resolution.relative_path, file.size, file.mtime_ns,
                f"Seleccionado por evidencia local; destino resuelto mediante {resolution.source}.",
                file.policy_level, evidence,
            ))
            if resolution.source == "preference":
                preference_resolved += 1
            if file.policy_level.value == "review_required":
                warnings.append(PlanWarning(file.policy_code, file.policy_reason, file.file_id))

        skipped = tuple(SkippedItem(
            file.path, file.policy_code, file.policy_reason, file.file_id,
        ) for file in result.skipped) + user_skipped
        duration_ms = int((perf_counter() - started_at) * 1000) if started_at is not None else 0
        metrics = PlanningMetrics(
            candidates_considered=result.considered,
            discarded_by_policy=len(skipped),
            resolved_by_rules=len(items) - preference_resolved,
            resolved_by_preferences=preference_resolved,
            sent_to_ai=sent_to_ai,
            inference_count=inference_count,
            duration_ms=max(0, duration_ms),
        )
        draft = OrganizationPlan.draft(
            intent.original_request, items=tuple(items), skipped=skipped,
            warnings=tuple(warnings), metrics=metrics,
        )
        validated = self.validator.validate(draft)
        self.repository.plans.create(validated)
        return validated

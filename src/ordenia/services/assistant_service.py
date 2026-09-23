"""Synchronous V0.5 planning pipeline; it has no file-operation dependency."""

import unicodedata
from dataclasses import replace
from time import perf_counter

from ordenia.ai.models import DEFAULT_AI_MODEL
from ordenia.ai.providers.base import AIProvider
from ordenia.automation.batch_planner import (
    BatchPlanner,
    PlanningNeedsClarification,
    PlanningOutcome,
)
from ordenia.automation.candidate_finder import CandidateFinder
from ordenia.automation.candidate_models import CandidateFile, CandidateQuery, CandidateSearchResult
from ordenia.automation.destination_resolver import DestinationResolver
from ordenia.automation.grouping import RelationshipGrouper
from ordenia.automation.intent_models import IntentState, OrganizationIntent, RecencyMode
from ordenia.automation.intent_parser import IntentParser
from ordenia.automation.models import SkippedItem
from ordenia.automation.policy import AutomationPolicy
from ordenia.database.repositories import Repository


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in normalized if not unicodedata.combining(character))


class AssistantService:
    """Convert one request into a validated persisted plan, never execute it."""

    def __init__(
        self, repository: Repository, *, provider: AIProvider | None = None,
        policy: AutomationPolicy | None = None,
        resolver: DestinationResolver | None = None,
        intent_parser: IntentParser | None = None,
    ) -> None:
        self.repository = repository
        self.policy = policy
        self.resolver = resolver or DestinationResolver(repository)
        model = repository.ai.get_setting("model", DEFAULT_AI_MODEL)
        self.intent_parser = intent_parser or IntentParser(provider, model=model)
        self.finder = CandidateFinder(repository, policy)
        self.grouper = RelationshipGrouper()
        self.planner = BatchPlanner(repository, self.resolver, policy)

    def _folder_ids(self, intent: OrganizationIntent) -> tuple[int, ...]:
        if not intent.source_hints:
            return ()
        hints = tuple(_fold(hint) for hint in intent.source_hints)
        return tuple(folder.id for folder in self.repository.list_folders()
                     if any(hint in _fold(str(folder.path)) for hint in hints))

    @staticmethod
    def _excluded(file: CandidateFile, exclusions: tuple[str, ...]) -> bool:
        parts = {_fold(part) for part in file.path.parts}
        stem = _fold(file.path.stem)
        return any(
            _fold(exclusion) in parts or _fold(exclusion) == stem
            for exclusion in exclusions if exclusion.strip()
        )

    def _select(
        self, result: CandidateSearchResult, intent: OrganizationIntent,
    ) -> tuple[CandidateSearchResult, tuple[SkippedItem, ...]]:
        eligible: list[CandidateFile] = []
        review: list[CandidateFile] = []
        user_skipped: list[SkippedItem] = []
        for collection, target in ((result.eligible, eligible), (result.review_required, review)):
            for file in collection:
                if self._excluded(file, intent.explicit_exclusions):
                    user_skipped.append(SkippedItem(
                        file.path, "user_exclusion",
                        "El archivo coincide con una exclusión explícita del usuario.", file.file_id,
                    ))
                else:
                    target.append(file)
        combined = eligible + review
        if intent.recency in {RecencyMode.LATEST, RecencyMode.RECENT}:
            combined.sort(key=lambda file: (file.detected_at, file.file_id), reverse=True)
        if intent.quantity_hint is not None:
            chosen_ids = {file.file_id for file in combined[:intent.quantity_hint]}
            for file in combined[intent.quantity_hint:]:
                user_skipped.append(SkippedItem(
                    file.path, "quantity_limit", "Quedó fuera de la cantidad indicada por el usuario.", file.file_id,
                ))
            eligible = [file for file in eligible if file.file_id in chosen_ids]
            review = [file for file in review if file.file_id in chosen_ids]
        selected = CandidateSearchResult(
            result.query, tuple(eligible), tuple(review), result.skipped, result.considered,
        )
        return selected, tuple(user_skipped)

    def prepare_plan(self, request: str) -> PlanningOutcome:
        started = perf_counter()
        existing = self.resolver.existing_paths()
        vocabulary = self.repository.candidates.organization_vocabulary()
        known_sources = tuple(folder.path.name for folder in self.repository.list_folders())
        parsed = self.intent_parser.parse(
            request, known_destinations=existing, known_sources=known_sources,
            vocabulary=vocabulary,
        )
        intent = parsed.intent
        if intent.clarification_required:
            return PlanningOutcome(intent, inference_count=parsed.inference_count, provider_error=parsed.provider_error)

        limit = max(200, (intent.quantity_hint or 20) * 10)
        query = CandidateQuery(
            watched_folder_ids=self._folder_ids(intent),
            text=" ".join(intent.search_terms), categories=intent.categories,
            extensions=intent.extensions, detected_from=intent.detected_from,
            detected_to=intent.detected_to, limit=min(50_000, limit), include_content=True,
        )
        result = self.finder.find(query)
        selected, user_skipped = self._select(result, intent)
        if not selected.reviewable_candidates:
            clarified = replace(
                intent, clarification_required=True, state=IntentState.AMBIGUOUS,
                clarification_reason="No se encontraron archivos elegibles con esos criterios.",
            )
            return PlanningOutcome(clarified, selected, inference_count=parsed.inference_count,
                                   provider_error=parsed.provider_error)
        groups = self.grouper.group(selected.reviewable_candidates)
        try:
            plan = self.planner.build(
                intent, selected, groups, user_skipped=user_skipped,
                sent_to_ai=parsed.sent_to_ai, inference_count=parsed.inference_count,
                started_at=started,
            )
        except PlanningNeedsClarification as exc:
            clarified = replace(
                intent, clarification_required=True, state=IntentState.AMBIGUOUS,
                clarification_reason=str(exc),
            )
            return PlanningOutcome(clarified, selected, groups, inference_count=parsed.inference_count,
                                   provider_error=parsed.provider_error)
        return PlanningOutcome(intent, selected, groups, plan, parsed.inference_count, parsed.provider_error)

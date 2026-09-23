"""Explainable candidate ranking over OrdenIA's SQLite index."""

import re
import unicodedata
from dataclasses import replace

from ordenia.database.candidates import CandidateSource
from ordenia.database.repositories import Repository

from .candidate_models import CandidateFile, CandidateQuery, CandidateSearchResult
from .models import RiskLevel
from .policy import AutomationPolicy, PROTECTED_EXTENSIONS


def normalized_tokens(value: str) -> frozenset[str]:
    folded = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(character for character in folded if not unicodedata.combining(character))
    plain = plain.replace("_", " ").replace("-", " ")
    return frozenset(re.findall(r"\w+", plain, re.UNICODE))


def _matches(terms: frozenset[str], value: str) -> bool:
    return bool(terms) and terms <= normalized_tokens(value)


class CandidateFinder:
    def __init__(self, repository: Repository, policy: AutomationPolicy | None = None) -> None:
        self.repository = repository
        self.policy = policy

    def _policy(self) -> AutomationPolicy:
        return self.policy or AutomationPolicy(protected_paths=self.repository.plans.list_protected_paths())

    @staticmethod
    def _rank(source: CandidateSource, query: CandidateQuery) -> CandidateFile | None:
        file = source.file
        terms = normalized_tokens(query.text)
        matched_by: list[str] = []
        evidence: list[str] = []
        score = 0.0

        signals = (
            ("name", file.name, 8.0, "El nombre coincide con los términos buscados."),
            ("path", str(file.path), 2.0, "La ruta coincide con los términos buscados."),
            ("title", source.title, 4.0, "El título extraído coincide con la consulta."),
            ("keywords", " ".join(source.keywords), 4.0, "Las palabras clave extraídas coinciden."),
            ("topic", source.topic, 6.0, "El tema de una sugerencia IA existente coincide."),
            ("tags", " ".join(source.tags), 5.0, "Las etiquetas de una sugerencia IA existente coinciden."),
        )
        if terms:
            for code, value, weight, reason in signals:
                if value and _matches(terms, value):
                    matched_by.append(code)
                    evidence.append(reason)
                    score += weight
            if source.content_match:
                matched_by.append("content")
                detail = f"El contenido indexado coincide: {source.content_excerpt}" if source.content_excerpt else "El contenido indexado coincide con la consulta."
                evidence.append(detail)
                score += 7.0
            if not matched_by and file.extension.casefold() not in PROTECTED_EXTENSIONS:
                return None
        else:
            matched_by.append("structured_filters")
            evidence.append("Coincide con los filtros estructurados de la consulta.")
            score += 1.0

        if query.extensions:
            score += 1.0
            evidence.append(f"Tiene una extensión solicitada: {file.extension or 'sin extensión'}.")
        if query.categories:
            score += 1.0
            evidence.append(f"Pertenece a la categoría solicitada: {file.category}.")
        if query.detected_from or query.detected_to:
            score += 0.5
            evidence.append("Fue detectado dentro del periodo indicado.")

        return CandidateFile(
            file.id, file.watched_folder_id, file.path, file.source_directory,
            file.name, file.extension, file.category, file.size, file.mtime_ns,
            file.detected_at, file.modified_at, tuple(matched_by), score,
            tuple(evidence), source.topic, source.tags, source.keywords,
        )

    def find(self, query: CandidateQuery) -> CandidateSearchResult:
        sources = self.repository.candidates.search(query)
        policy = self._policy()
        eligible: list[CandidateFile] = []
        review: list[CandidateFile] = []
        skipped: list[CandidateFile] = []
        considered = 0
        for source in sources:
            candidate = self._rank(source, query)
            if candidate is None:
                continue
            considered += 1
            decision = policy.evaluate(source.file, source.folder)
            candidate = replace(
                candidate, policy_level=decision.level,
                policy_code=decision.code, policy_reason=decision.reason,
            )
            if decision.level is RiskLevel.PROTECTED:
                skipped.append(candidate)
            elif decision.level is RiskLevel.REVIEW_REQUIRED:
                review.append(candidate)
            else:
                eligible.append(candidate)

        ranking = lambda item: (-item.score, item.name.casefold(), item.file_id)
        eligible.sort(key=ranking)
        review.sort(key=ranking)
        skipped.sort(key=ranking)
        return CandidateSearchResult(
            query, tuple(eligible[:query.limit]), tuple(review[:query.limit]),
            tuple(skipped[:query.limit]), considered,
        )

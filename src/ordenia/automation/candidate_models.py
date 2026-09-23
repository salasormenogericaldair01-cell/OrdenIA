"""Typed contracts for deterministic candidate retrieval and grouping."""

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .models import RiskLevel


@dataclass(frozen=True)
class CandidateQuery:
    """Structured query over OrdenIA's local index.

    Date bounds apply to ``detected_at``. OrdenIA also returns ``modified_at``
    as metadata, but it does not claim to know the Windows download date.
    """

    watched_folder_ids: tuple[int, ...] = field(default_factory=tuple)
    text: str = ""
    categories: tuple[str, ...] = field(default_factory=tuple)
    extensions: tuple[str, ...] = field(default_factory=tuple)
    statuses: tuple[str, ...] = ("Pendiente",)
    detected_from: date | datetime | None = None
    detected_to: date | datetime | None = None
    limit: int = 200
    include_content: bool = True
    active_only: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 50_000:
            raise ValueError("El límite debe estar entre 1 y 50.000 archivos.")
        if any(folder_id <= 0 for folder_id in self.watched_folder_ids):
            raise ValueError("Los identificadores de carpetas vigiladas deben ser positivos.")
        extensions = tuple(dict.fromkeys(
            extension.strip().casefold().lstrip(".")
            for extension in self.extensions if extension.strip().strip(".")
        ))
        categories = tuple(dict.fromkeys(category.strip() for category in self.categories if category.strip()))
        statuses = tuple(dict.fromkeys(status.strip() for status in self.statuses if status.strip()))
        object.__setattr__(self, "text", self.text.strip())
        object.__setattr__(self, "extensions", extensions)
        object.__setattr__(self, "categories", categories)
        object.__setattr__(self, "statuses", statuses)
        if self.detected_from and self.detected_to:
            start = self.detected_from.date() if isinstance(self.detected_from, datetime) else self.detected_from
            end = self.detected_to.date() if isinstance(self.detected_to, datetime) else self.detected_to
            if start > end:
                raise ValueError("La fecha inicial no puede ser posterior a la fecha final.")


@dataclass(frozen=True)
class CandidateFile:
    file_id: int
    watched_folder_id: int
    path: Path
    source_directory: Path
    name: str
    extension: str
    category: str
    size: int
    mtime_ns: int
    detected_at: str
    modified_at: str
    matched_by: tuple[str, ...]
    score: float
    evidence: tuple[str, ...]
    topic: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    keywords: tuple[str, ...] = field(default_factory=tuple)
    policy_level: RiskLevel = RiskLevel.NORMAL
    policy_code: str = "eligible"
    policy_reason: str = ""


@dataclass(frozen=True)
class CandidateSearchResult:
    query: CandidateQuery
    eligible: tuple[CandidateFile, ...] = field(default_factory=tuple)
    review_required: tuple[CandidateFile, ...] = field(default_factory=tuple)
    skipped: tuple[CandidateFile, ...] = field(default_factory=tuple)
    considered: int = 0

    @property
    def reviewable_candidates(self) -> tuple[CandidateFile, ...]:
        return self.eligible + self.review_required


@dataclass(frozen=True)
class CandidateGroup:
    id: str
    files: tuple[CandidateFile, ...]
    group_label: str
    evidence: tuple[str, ...]
    score: float
    common_topics: tuple[str, ...] = field(default_factory=tuple)
    common_tags: tuple[str, ...] = field(default_factory=tuple)
    date_range: tuple[str, str] = ("", "")

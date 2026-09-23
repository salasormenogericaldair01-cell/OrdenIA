"""Typed organization intent produced from one human request."""

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class IntentState(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    REVIEW_REQUIRED = "review_required"


class RecencyMode(StrEnum):
    ANY = "any"
    TODAY = "today"
    YESTERDAY = "yesterday"
    LATEST = "latest"
    RECENT = "recent"


@dataclass(frozen=True)
class OrganizationIntent:
    original_request: str
    search_terms: tuple[str, ...] = field(default_factory=tuple)
    topic: str = ""
    extensions: tuple[str, ...] = field(default_factory=tuple)
    categories: tuple[str, ...] = field(default_factory=tuple)
    source_hints: tuple[str, ...] = field(default_factory=tuple)
    detected_from: date | None = None
    detected_to: date | None = None
    recency: RecencyMode = RecencyMode.ANY
    quantity_hint: int | None = None
    destination_hints: tuple[str, ...] = field(default_factory=tuple)
    grouping_hints: tuple[str, ...] = field(default_factory=tuple)
    user_context: tuple[str, ...] = field(default_factory=tuple)
    explicit_exclusions: tuple[str, ...] = field(default_factory=tuple)
    clarification_required: bool = False
    clarification_reason: str = ""
    state: IntentState = IntentState.RESOLVED
    evidence: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class IntentParseResult:
    intent: OrganizationIntent
    sent_to_ai: int = 0
    inference_count: int = 0
    provider_error: str = ""

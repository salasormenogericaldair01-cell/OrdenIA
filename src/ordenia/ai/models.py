"""Typed values shared by providers, persistence and the UI."""

from dataclasses import dataclass, field


AI_STATUSES = frozenset({"pending", "analyzing", "ready", "failed", "stale", "unavailable"})
DEFAULT_AI_MODEL = "qwen3:4b-instruct"


@dataclass(frozen=True)
class AISuggestion:
    document_type: str
    topic: str
    tags: tuple[str, ...]
    suggested_path: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class AIRecord:
    file_id: int
    status: str = "pending"
    provider: str = ""
    model: str = ""
    document_type: str = ""
    topic: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    suggested_path: str = ""
    confidence: float = 0.0
    reason: str = ""
    created_at: str = ""
    fingerprint_size: int = 0
    fingerprint_mtime_ns: int = 0
    error: str = ""

    @property
    def suggestion(self) -> AISuggestion | None:
        if self.status not in {"ready", "stale"} or not self.suggested_path:
            return None
        return AISuggestion(
            self.document_type, self.topic, self.tags, self.suggested_path,
            self.confidence, self.reason,
        )


@dataclass(frozen=True)
class ProviderStatus:
    provider: str
    available: bool
    models: tuple[str, ...] = field(default_factory=tuple)
    message: str = ""


@dataclass(frozen=True)
class AIResponse:
    content: str
    load_seconds: float = 0.0
    prompt_eval_count: int = 0
    eval_count: int = 0
    eval_seconds: float = 0.0


SUGGESTION_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string", "minLength": 1, "maxLength": 80},
        "topic": {"type": "string", "minLength": 1, "maxLength": 100},
        "tags": {
            "type": "array", "maxItems": 20,
            "items": {"type": "string", "minLength": 1, "maxLength": 40},
        },
        "suggested_path": {
            "type": "string", "minLength": 1, "maxLength": 180,
            "pattern": r'^[^\\/:*?"<>|%]+(?:/[^\\/:*?"<>|%]+)*$',
            "description": "Ruta relativa de carpetas, separada con / y sin incluir el nombre del archivo.",
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "minLength": 1, "maxLength": 500},
    },
    "required": ["document_type", "topic", "tags", "suggested_path", "confidence", "reason"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class AIContext:
    file_name: str
    extension: str
    category: str
    title: str
    metadata: dict[str, str]
    keywords: tuple[str, ...]
    fragments: tuple[str, ...]
    existing_paths: tuple[str, ...]
    preferences: tuple[str, ...]

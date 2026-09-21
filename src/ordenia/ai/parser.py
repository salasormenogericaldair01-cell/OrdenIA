"""Strict parsing for untrusted model output."""

import json
import re
from pathlib import PurePosixPath, PureWindowsPath

from .models import AISuggestion

MAX_PATH_LENGTH = 180
MAX_PATH_PARTS = 8
MAX_TAGS = 20
_DANGEROUS_PATH_CHARS = re.compile(r'[<>:"|?*\x00-\x1f%]')
_RESERVED_WINDOWS_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", "CLOCK$", *(
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
)})


class InvalidSuggestion(ValueError):
    """Raised when model output cannot safely become a suggestion."""


def validate_relative_path(value: object) -> str:
    if not isinstance(value, str):
        raise InvalidSuggestion("La ruta sugerida debe ser texto.")
    raw = value.strip().replace("\\", "/")
    if not raw or len(raw) > MAX_PATH_LENGTH:
        raise InvalidSuggestion("La ruta sugerida está vacía o es demasiado larga.")
    windows = PureWindowsPath(raw)
    posix = PurePosixPath(raw)
    if raw.startswith(("/", "//")) or windows.is_absolute() or windows.drive or posix.is_absolute():
        raise InvalidSuggestion("La IA solo puede sugerir una ruta relativa.")
    parts = tuple(part.strip() for part in raw.split("/"))
    if (not parts or len(parts) > MAX_PATH_PARTS or any(
            not part or part in {".", ".."} or part.endswith((".", " "))
            or part.split(".", 1)[0].upper() in _RESERVED_WINDOWS_NAMES
            or _DANGEROUS_PATH_CHARS.search(part)
            for part in parts)):
        raise InvalidSuggestion("La ruta sugerida contiene segmentos no permitidos.")
    return "/".join(parts)


def _short_text(value: object, field: str, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise InvalidSuggestion(f"{field} debe ser texto.")
    result = " ".join(value.split()).strip()
    if (required and not result) or len(result) > maximum or any(ord(char) < 32 for char in result):
        raise InvalidSuggestion(f"{field} no es válido.")
    return result


def _json_object(response: str) -> dict[str, object]:
    text = response.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidSuggestion("La IA no devolvió JSON válido.") from exc
    if not isinstance(value, dict):
        raise InvalidSuggestion("La respuesta de IA debe ser un objeto JSON.")
    return value


def parse_suggestion(response: str) -> AISuggestion:
    value = _json_object(response)
    document_type = _short_text(value.get("document_type"), "document_type", 80)
    topic = _short_text(value.get("topic"), "topic", 100)
    reason = _short_text(value.get("reason"), "reason", 500)
    path = validate_relative_path(value.get("suggested_path"))
    confidence = value.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise InvalidSuggestion("confidence debe ser un número entre 0 y 1.")
    score = float(confidence)
    if not 0.0 <= score <= 1.0:
        raise InvalidSuggestion("confidence debe estar entre 0 y 1.")
    raw_tags = value.get("tags")
    if not isinstance(raw_tags, list) or len(raw_tags) > MAX_TAGS:
        raise InvalidSuggestion("tags debe ser una lista corta.")
    tags: list[str] = []
    seen: set[str] = set()
    for raw_tag in raw_tags:
        tag = _short_text(raw_tag, "tag", 40)
        key = tag.casefold()
        if key not in seen:
            seen.add(key)
            tags.append(tag)
    return AISuggestion(document_type, topic, tuple(tags), path, score, reason)

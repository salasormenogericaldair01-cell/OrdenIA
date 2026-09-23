"""Resolve safe relative destinations from explicit and existing evidence."""

import unicodedata
from dataclasses import dataclass
from pathlib import Path

from ordenia.core.destination_catalog import existing_relative_paths
from ordenia.core.destinations import destination_root, validate_relative_destination
from ordenia.database.repositories import Repository
from ordenia.platform.actions import default_central_root

from .candidate_models import CandidateFile, CandidateGroup
from .intent_models import OrganizationIntent


@dataclass(frozen=True)
class DestinationResolution:
    relative_path: str
    source: str
    evidence: tuple[str, ...]


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in normalized if not unicodedata.combining(character))


def _tokens(value: str) -> frozenset[str]:
    return frozenset(part for part in _fold(value).replace("/", " ").split() if len(part) > 2)


class DestinationResolver:
    def __init__(
        self, repository: Repository, *, existing_paths: tuple[str, ...] | None = None,
    ) -> None:
        self.repository = repository
        self._provided_paths = existing_paths

    def roots(self) -> tuple[Path, ...]:
        roots = list(self.repository.list_managed_roots())
        central = Path(self.repository.get_setting("central_destination", str(default_central_root())))
        for folder in self.repository.list_folders():
            try:
                roots.append(destination_root(
                    folder.path, folder.destination_strategy, central, folder.custom_destination,
                ))
            except ValueError:
                continue
        seen: set[str] = set()
        unique: list[Path] = []
        for root in roots:
            key = str(root).casefold()
            if key not in seen:
                seen.add(key)
                unique.append(root)
        return tuple(unique)

    def existing_paths(self) -> tuple[str, ...]:
        if self._provided_paths is not None:
            return self._provided_paths
        return existing_relative_paths(self.roots(), limit=80, maximum_depth=4)

    @staticmethod
    def _group_context(
        intent: OrganizationIntent, group: CandidateGroup | None,
        files: tuple[CandidateFile, ...],
    ) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        topic = intent.topic
        if not topic and group and group.common_topics:
            topic = group.common_topics[0]
        keywords = group.common_tags if group and group.common_tags else ()
        if group:
            keywords = tuple(dict.fromkeys((*keywords, *(
                word.strip() for word in group.group_label.split("·") if word.strip()
            ))))
        tags = tuple(dict.fromkeys(tag for file in files for tag in file.tags))
        return topic, keywords, tags

    @staticmethod
    def _related_path(paths: tuple[str, ...], hints: tuple[str, ...], topic: str) -> str:
        hint_tokens = set().union(*(_tokens(hint) for hint in hints)) if hints else set()
        topic_tokens = _tokens(topic)
        scored: list[tuple[int, int, str]] = []
        for path in paths:
            path_tokens = _tokens(path)
            hint_score = len(hint_tokens & path_tokens)
            topic_score = len(topic_tokens & path_tokens)
            if hint_score or topic_score:
                scored.append((hint_score * 5 + topic_score * 3, -len(path.split("/")), path))
        return max(scored)[2] if scored else ""

    @staticmethod
    def _apply_grouping(path: str, grouping_hints: tuple[str, ...]) -> str:
        if not grouping_hints:
            return path
        grouping = grouping_hints[0]
        parts = path.replace("\\", "/").split("/") if path else []
        for index, part in enumerate(parts):
            if "practica" in _fold(part) and "practica" in _fold(grouping):
                parts[index] = grouping
                return "/".join(parts)
        if not any(_fold(grouping) == _fold(part) for part in parts):
            parts.append(grouping)
        return "/".join(parts)

    def resolve(
        self, intent: OrganizationIntent, files: tuple[CandidateFile, ...],
        group: CandidateGroup | None = None,
    ) -> DestinationResolution | None:
        if not files:
            return None
        topic, group_keywords, tags = self._group_context(intent, group, files)
        hints = intent.destination_hints
        evidence: list[str] = []
        base = ""
        source = ""

        # A complete explicit relative path is authoritative.
        for hint in hints:
            if "/" in hint or "\\" in hint:
                try:
                    base = validate_relative_destination(hint)
                    source = "explicit"
                    evidence.append(f"El usuario indicó explícitamente «{base}».")
                    break
                except ValueError:
                    # A path-like value from an untrusted model must fail
                    # closed instead of being silently reinterpreted.
                    return None

        feedback = self.repository.ai.relevant_feedback(topic, "", tuple(dict.fromkeys((*tags, *group_keywords))), 5)
        if not base and feedback:
            for item in feedback:
                chosen = str(item["chosen_path"])
                if not hints or any(_tokens(hint) & _tokens(chosen) for hint in hints) or (_tokens(topic) & _tokens(chosen)):
                    try:
                        base = validate_relative_destination(chosen)
                        source = "preference"
                        evidence.append(f"Una decisión anterior relacionada usó «{base}».")
                        break
                    except ValueError:
                        continue

        paths = self.existing_paths()
        if not base:
            related = self._related_path(paths, hints, topic)
            if related:
                base = related
                source = "existing"
                evidence.append(f"Se reutilizó la estructura existente «{base}».")

        if not base and hints:
            base = hints[0]
            source = "explicit"
            evidence.append(f"El usuario relacionó los archivos con «{hints[0]}».")
        if not base and topic:
            base = topic
            source = "new"
            evidence.append(f"Se propuso una carpeta relativa basada en el tema «{topic}».")
        if not base:
            return None

        base_tokens = _tokens(base)
        if topic and not (_tokens(topic) <= base_tokens):
            base = f"{base}/{topic}"
            evidence.append(f"Se añadió el tema «{topic}» a la jerarquía.")
        base = self._apply_grouping(base, intent.grouping_hints)
        if intent.grouping_hints:
            evidence.append(f"Se aplicó la agrupación «{intent.grouping_hints[0]}».")
        try:
            relative = validate_relative_destination(base)
        except ValueError:
            return None
        return DestinationResolution(relative, source, tuple(evidence))

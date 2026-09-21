"""Build compact, deterministic context from the local content index."""

import os
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .models import AIContext

if TYPE_CHECKING:
    from ordenia.database.repositories import Repository

MAX_AI_CONTEXT_CHARS = 6000
MAX_EXISTING_PATHS = 40
MAX_FEEDBACK_EXAMPLES = 5


def _clean(value: str, maximum: int) -> str:
    return " ".join(value.split())[:maximum]


def _bounded(values: tuple[str, ...], budget: int) -> tuple[str, ...]:
    result: list[str] = []
    used = 0
    for value in values:
        if used >= budget:
            break
        item = value[:budget - used]
        if item:
            result.append(item)
            used += len(item)
    return tuple(result)


def _fragments(text: str, keywords: tuple[str, ...], budget: int) -> tuple[str, ...]:
    compact = " ".join(text.split())
    if not compact or budget <= 0:
        return ()
    fragments: list[str] = []
    first = compact[:min(2200, budget)]
    if first:
        fragments.append(first)
    used = len(first)
    lowered = compact.casefold()
    for keyword in keywords:
        if used >= budget:
            break
        position = lowered.find(keyword.casefold(), max(0, len(first) - 100))
        if position < 0:
            continue
        start = max(0, position - 180)
        piece = compact[start:start + min(500, budget - used)]
        if piece and piece not in fragments:
            fragments.append(piece)
            used += len(piece)
    return tuple(fragments)


class ContentContextBuilder:
    def __init__(self, repository: "Repository", roots: Callable[[], tuple[Path, ...]] | None = None,
                 max_characters: int = MAX_AI_CONTEXT_CHARS) -> None:
        self.repository = repository
        self.roots = roots or repository.list_managed_roots
        self.max_characters = max(1000, max_characters)

    def _existing_paths(self) -> tuple[str, ...]:
        result: list[str] = []
        seen: set[str] = set()
        for root in self.roots():
            try:
                root = root.resolve()
                if not root.is_dir():
                    continue
            except OSError:
                continue
            for current, directories, _files in os.walk(root, followlinks=False):
                current_path = Path(current)
                try:
                    depth = len(current_path.relative_to(root).parts)
                except ValueError:
                    continue
                directories[:] = sorted(directories, key=str.casefold) if depth < 3 else []
                for directory in directories:
                    relative = (current_path / directory).relative_to(root).as_posix()
                    key = relative.casefold()
                    if key not in seen:
                        seen.add(key)
                        result.append(relative)
                        if len(result) >= MAX_EXISTING_PATHS:
                            return tuple(result)
        return tuple(result)

    def build(self, file_id: int) -> AIContext:
        file = self.repository.get_file(file_id)
        analysis = self.repository.content.get(file_id)
        metadata = {
            key: _clean(value, 200) for key, value in {
                "author": analysis.author,
                "subject": analysis.subject,
                "pages": str(analysis.page_count) if analysis.page_count is not None else "",
                "slides": str(analysis.slide_count) if analysis.slide_count is not None else "",
            }.items() if value
        }
        previous = self.repository.ai.get(file_id)
        feedback = self.repository.ai.relevant_feedback(
            previous.topic or analysis.title or file.name, previous.document_type,
            tuple(dict.fromkeys((*previous.tags, *analysis.keywords))), MAX_FEEDBACK_EXAMPLES,
        )
        preferences = _bounded(tuple(
            f"Para tipo '{item['document_type']}' y tema '{item['topic']}', el usuario eligió '{item['chosen_path']}'."
            for item in feedback
        ), 1200)
        existing = _bounded(self._existing_paths(), 1500)
        fixed_size = sum(map(len, (file.name, file.extension, file.category, analysis.title)))
        fixed_size += sum(map(len, metadata.values())) + sum(map(len, analysis.keywords))
        fixed_size += sum(map(len, existing)) + sum(map(len, preferences))
        budget = max(500, self.max_characters - fixed_size)
        return AIContext(
            file.name, file.extension, file.category, _clean(analysis.title, 300), metadata,
            analysis.keywords[:15], _fragments(analysis.text, analysis.keywords, budget),
            existing, preferences,
        )

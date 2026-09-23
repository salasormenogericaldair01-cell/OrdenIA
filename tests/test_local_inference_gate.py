"""AIService and intent parsing share one process-wide inference slot."""

import json
import threading
import time
from pathlib import Path

from ordenia.ai.models import AIResponse, ProviderStatus
from ordenia.ai.providers.base import AIProvider
from ordenia.analysis.models import AnalysisOutcome
from ordenia.automation.intent_parser import IntentParser
from ordenia.database.repositories import Repository
from ordenia.services.ai_service import AIService
from ordenia.services.content_service import ContentService


_SUGGESTION = json.dumps({
    "document_type": "material", "topic": "SQL", "tags": ["SQL"],
    "suggested_path": "Estudios/SQL", "confidence": 0.8, "reason": "Material SQL",
})
_INTENT = json.dumps({
    "search_terms": ["redes"], "topic": "Redes", "extensions": ["pdf"],
    "categories": ["Documentos"], "source_hints": [], "date_reference": "any",
    "quantity_hint": 1, "destination_hints": ["Estudios/Redes"],
    "grouping_hints": [], "user_context": [], "explicit_exclusions": [],
    "clarification_required": False, "clarification_reason": "",
})


class _Tracker:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.maximum = 0

    def enter(self) -> None:
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)

    def leave(self) -> None:
        with self.lock:
            self.active -= 1


class _SlowProvider(AIProvider):
    name = "ollama"

    def __init__(self, tracker: _Tracker, result: str, entered: threading.Event,
                 release: threading.Event | None = None) -> None:
        self.tracker = tracker
        self.result = result
        self.entered = entered
        self.release = release
        self.calls = 0

    def check(self) -> ProviderStatus:
        return ProviderStatus(self.name, True)

    def _infer(self) -> AIResponse:
        self.calls += 1
        self.tracker.enter()
        self.entered.set()
        try:
            if self.release is not None:
                assert self.release.wait(5)
            time.sleep(0.05)
            return AIResponse(self.result)
        finally:
            self.tracker.leave()

    def generate(self, system_prompt: str, user_prompt: str, model: str) -> AIResponse:
        return self._infer()

    def generate_structured(self, system_prompt: str, user_prompt: str, model: str,
                            schema: dict[str, object]) -> AIResponse:
        return self._infer()


def test_ai_service_and_intent_parser_never_infer_concurrently(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "sql.txt"
    path.write_text("SQL", encoding="utf-8")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    stat = path.stat()
    file_id = repository.upsert_file(folder.id, path, stat.st_size, "Documentos", "2026-01-01", stat.st_mtime_ns).id
    repository.content.save(file_id, AnalysisOutcome("indexed", "Texto", "SQL", keywords=("SQL",)),
                            stat.st_size, stat.st_mtime_ns)

    tracker = _Tracker()
    first_entered, second_entered, release = threading.Event(), threading.Event(), threading.Event()
    file_provider = _SlowProvider(tracker, _SUGGESTION, first_entered, release)
    intent_provider = _SlowProvider(tracker, _INTENT, second_entered)
    content = ContentService(repository)
    ai_service = AIService(repository, content, file_provider)
    parsed: list[object] = []
    try:
        ai_service.request([file_id])
        assert first_entered.wait(3)
        thread = threading.Thread(target=lambda: parsed.append(IntentParser(intent_provider).parse(
            "Clasifica el curso que vimos en el lugar habitual."
        )))
        thread.start()
        time.sleep(0.1)
        assert not second_entered.is_set()
        release.set()
        thread.join(5)
        assert parsed and second_entered.is_set()
        assert tracker.maximum == 1
        assert file_provider.calls == intent_provider.calls == 1
    finally:
        release.set()
        ai_service.close()
        content.close()

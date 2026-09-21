"""Background AI orchestration with deterministic fake providers."""

import json
import threading
import time
from pathlib import Path

from ordenia.ai.models import AIResponse, DEFAULT_AI_MODEL, ProviderStatus
from ordenia.ai.providers.base import AIProvider, ProviderTimeout, ProviderUnavailable
from ordenia.analysis.models import AnalysisOutcome
from ordenia.database.repositories import Repository
from ordenia.services.ai_service import AIService
from ordenia.services.content_service import ContentService


def response(path: str = "Estudios/Base de Datos") -> str:
    return json.dumps({
        "document_type": "material_academico", "topic": "bases de datos",
        "tags": ["SQL", "normalización"], "suggested_path": path,
        "confidence": 0.88, "reason": "Contenido académico sobre bases de datos.",
    })


class FakeProvider(AIProvider):
    name = "ollama"

    def __init__(self, result: str | Exception = response()) -> None:
        self.result = result
        self.calls = 0
        self.prompts: list[tuple[str, str, str]] = []

    def check(self) -> ProviderStatus:
        return ProviderStatus(self.name, True, ("qwen3:4b",), "ok")

    def generate(self, system_prompt: str, user_prompt: str, model: str) -> AIResponse:
        self.calls += 1
        self.prompts.append((system_prompt, user_prompt, model))
        if isinstance(self.result, Exception):
            raise self.result
        return AIResponse(self.result)


def indexed(tmp_path: Path, count: int = 1) -> tuple[Repository, list[Path], list[int]]:
    root = tmp_path / "watched"
    root.mkdir()
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    paths: list[Path] = []
    ids: list[int] = []
    for index in range(count):
        path = root / f"document-{index}.txt"
        path.write_text(f"SQL normalización documento {index}", encoding="utf-8")
        stat = path.stat()
        file_id = repository.upsert_file(folder.id, path, stat.st_size, "Documentos", "2026-01-01", stat.st_mtime_ns).id
        repository.content.save(file_id, AnalysisOutcome(
            "indexed", "Texto", path.read_text(encoding="utf-8"), keywords=("SQL", "normalización")
        ), stat.st_size, stat.st_mtime_ns)
        paths.append(path)
        ids.append(file_id)
    return repository, paths, ids


def wait_for(repository: Repository, file_id: int, statuses: set[str], timeout: float = 5) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = repository.ai.get(file_id).status
        if status in statuses:
            return status
        time.sleep(0.02)
    return repository.ai.get(file_id).status


def test_pending_to_ready_uses_configured_model(tmp_path: Path) -> None:
    repository, _paths, ids = indexed(tmp_path)
    content = ContentService(repository)
    provider = FakeProvider()
    service = AIService(repository, content, provider)
    try:
        job = service.request(ids)
        assert job
        assert wait_for(repository, ids[0], {"ready"}) == "ready"
        record = repository.ai.get(ids[0])
        assert record.model == DEFAULT_AI_MODEL and record.provider == "ollama"
        assert record.suggested_path == "Estudios/Base de Datos"
        assert provider.calls == 1
        assert _paths[0].is_file() and repository.list_operations() == []
    finally:
        service.close()
        content.close()


def test_any_local_model_name_remains_configurable(tmp_path: Path) -> None:
    repository, _paths, ids = indexed(tmp_path)
    repository.ai.set_setting("model", "modelo-local-personalizado")
    content = ContentService(repository)
    provider = FakeProvider()
    service = AIService(repository, content, provider)
    try:
        service.request(ids)
        assert wait_for(repository, ids[0], {"ready"}) == "ready"
        assert provider.prompts[0][2] == "modelo-local-personalizado"
        assert repository.ai.get(ids[0]).model == "modelo-local-personalizado"
    finally:
        service.close()
        content.close()


def test_ai_prepares_pending_content_before_inference(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "pending.txt"
    path.write_text("SQL normalización", encoding="utf-8")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    stat = path.stat()
    file_id = repository.upsert_file(folder.id, path, stat.st_size, "Documentos", "2026-01-01", stat.st_mtime_ns).id
    content = ContentService(repository)
    provider = FakeProvider()
    service = AIService(repository, content, provider)
    try:
        service.request([file_id])
        assert wait_for(repository, file_id, {"ready"}, 8) == "ready"
        assert repository.content.get(file_id).status == "indexed"
        assert provider.calls == 1
    finally:
        service.close()
        content.close()


def test_stale_suggestion_can_be_reanalyzed_to_ready(tmp_path: Path) -> None:
    repository, paths, ids = indexed(tmp_path)
    content = ContentService(repository)
    service = AIService(repository, content, FakeProvider())
    try:
        service.request(ids)
        assert wait_for(repository, ids[0], {"ready"}) == "ready"
        paths[0].write_text("SQL normalización con contenido actualizado", encoding="utf-8")
        changed = paths[0].stat()
        repository.upsert_file(1, paths[0], changed.st_size, "Documentos", "2026-01-02", changed.st_mtime_ns)
        assert repository.ai.get(ids[0]).status == "stale"
        service.request(ids, force=True)
        assert wait_for(repository, ids[0], {"ready", "failed"}, 8) == "ready"
        assert repository.content.get(ids[0]).status == "indexed"
    finally:
        service.close()
        content.close()


def test_invalid_json_and_malicious_path_fail_safely(tmp_path: Path) -> None:
    for index, result in enumerate(("not json", response("C:\\Windows"))):
        case = tmp_path / str(index)
        case.mkdir()
        repository, _paths, ids = indexed(case)
        content = ContentService(repository)
        service = AIService(repository, content, FakeProvider(result))
        try:
            service.request(ids)
            assert wait_for(repository, ids[0], {"failed"}) == "failed"
            assert not repository.ai.get(ids[0]).suggested_path
        finally:
            service.close()
            content.close()


def test_offline_and_timeout_have_distinct_states(tmp_path: Path) -> None:
    cases = ((ProviderUnavailable("IA local no disponible"), "unavailable"),
             (ProviderTimeout("Tiempo agotado"), "failed"))
    for index, (error, expected) in enumerate(cases):
        case = tmp_path / str(index)
        case.mkdir()
        repository, _paths, ids = indexed(case)
        content = ContentService(repository)
        service = AIService(repository, content, FakeProvider(error))
        try:
            service.request(ids)
            assert wait_for(repository, ids[0], {expected}) == expected
            assert repository.ai.get(ids[0]).error
        finally:
            service.close()
            content.close()


def test_cancelled_active_inference_does_not_publish_result(tmp_path: Path) -> None:
    repository, _paths, ids = indexed(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    class Slow(FakeProvider):
        def generate(self, *args) -> AIResponse:
            entered.set()
            assert release.wait(5)
            return AIResponse(response())

    content = ContentService(repository)
    service = AIService(repository, content, Slow())
    try:
        job = service.request(ids)
        assert entered.wait(3)
        service.cancel(job)
        release.set()
        assert wait_for(repository, ids[0], {"pending"}) == "pending"
        assert repository.ai.get(ids[0]).suggestion is None
    finally:
        release.set()
        service.close()
        content.close()


def test_cancel_stops_queued_inferences(tmp_path: Path) -> None:
    repository, _paths, ids = indexed(tmp_path, 3)
    entered = threading.Event()
    release = threading.Event()

    class Slow(FakeProvider):
        def generate(self, *args) -> AIResponse:
            self.calls += 1
            entered.set()
            assert release.wait(5)
            return AIResponse(response())

    provider = Slow()
    content = ContentService(repository)
    service = AIService(repository, content, provider)
    try:
        job = service.request(ids)
        assert entered.wait(3)
        service.cancel(job)
        release.set()
        deadline = time.monotonic() + 4
        while service._pending and time.monotonic() < deadline:
            time.sleep(0.02)
        assert provider.calls == 1
        assert all(repository.ai.get(file_id).status == "pending" for file_id in ids)
    finally:
        release.set()
        service.close()
        content.close()


def test_file_changed_during_inference_marks_suggestion_stale(tmp_path: Path) -> None:
    repository, paths, ids = indexed(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    class Slow(FakeProvider):
        def generate(self, *args) -> AIResponse:
            entered.set()
            assert release.wait(5)
            return AIResponse(response())

    content = ContentService(repository)
    service = AIService(repository, content, Slow())
    try:
        service.request(ids)
        assert entered.wait(3)
        paths[0].write_text("contenido completamente modificado", encoding="utf-8")
        release.set()
        assert wait_for(repository, ids[0], {"stale"}) == "stale"
        assert repository.ai.get(ids[0]).suggestion is None  # invalid snapshot was never published
    finally:
        release.set()
        service.close()
        content.close()


def test_queue_runs_only_one_inference_at_a_time(tmp_path: Path) -> None:
    repository, _paths, ids = indexed(tmp_path, 3)
    guard = threading.Lock()
    active = maximum = 0

    class Counting(FakeProvider):
        def generate(self, *args) -> AIResponse:
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.03)
            with guard:
                active -= 1
            return AIResponse(response())

    content = ContentService(repository)
    service = AIService(repository, content, Counting())
    try:
        service.request(ids)
        for file_id in ids:
            assert wait_for(repository, file_id, {"ready"}) == "ready"
        assert maximum == 1
    finally:
        service.close()
        content.close()


def test_health_check_is_background_and_reports_models(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    repository, _paths, _ids = indexed(tmp_path)
    content = ContentService(repository)
    service = AIService(repository, content, FakeProvider())
    received: list[ProviderStatus] = []
    service.provider_status.connect(received.append)
    try:
        service.check_connection()
        deadline = time.monotonic() + 3
        while not received and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert received[0].models == ("qwen3:4b",)
    finally:
        service.close()
        content.close()

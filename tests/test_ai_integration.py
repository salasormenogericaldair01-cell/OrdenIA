"""End-to-end V0.4 flow with a local fake model and real safe moves."""

import json
import time
from pathlib import Path

from ordenia.ai.models import AIResponse, DEFAULT_AI_MODEL, ProviderStatus
from ordenia.ai.providers.base import AIProvider
from ordenia.database.repositories import Repository
from ordenia.services.file_service import FileService


class LocalFake(AIProvider):
    name = "ollama"

    def check(self) -> ProviderStatus:
        return ProviderStatus("ollama", True, ("qwen3:4b",), "local fake")

    def generate(self, _system: str, _user: str, _model: str) -> AIResponse:
        return AIResponse(json.dumps({
            "document_type": "material_academico", "topic": "bases de datos",
            "tags": ["SQL", "normalización", "modelo relacional"],
            "suggested_path": "Estudios/Base de Datos", "confidence": 0.91,
            "reason": "Documento académico sobre SQL y normalización.",
        }))


def test_extract_suggest_move_undo_stale_and_restart(tmp_path: Path) -> None:
    root = tmp_path / "TestOrdenIA"
    root.mkdir()
    source = root / "S01_Ingenieria.pdf.txt"
    source.write_text("Curso de SQL, normalización y modelo relacional", encoding="utf-8")
    repository = Repository(tmp_path / "ordenia.sqlite3")
    folder = repository.add_folder(root)
    stat = source.stat()
    file_id = repository.upsert_file(folder.id, source, stat.st_size, "Documentos", "2026-09-21", stat.st_mtime_ns).id
    service = FileService(repository)
    service.ai.provider = LocalFake()
    try:
        service.analyze_with_ai([file_id])
        deadline = time.monotonic() + 8
        while repository.ai.get(file_id).status not in {"ready", "failed", "unavailable"} and time.monotonic() < deadline:
            time.sleep(0.02)
        suggestion = repository.ai.get(file_id)
        assert suggestion.status == "ready"
        assert source.is_file() and repository.list_operations() == []

        proposed = service.proposal(repository.get_file(file_id), suggestion.suggested_path)
        service._organize(file_id, suggestion.suggested_path, suggestion.suggested_path)
        assert proposed.is_file() and not source.exists()
        assert repository.ai.get(file_id).status == "ready"
        operation = repository.list_operations()[0]
        service._undo(operation.id)
        assert source.is_file() and not proposed.exists()
        assert repository.ai.get(file_id).status == "ready"

        source.write_text("Contenido nuevo y diferente", encoding="utf-8")
        changed = source.stat()
        repository.upsert_file(folder.id, source, changed.st_size, "Documentos", "2026-09-21", changed.st_mtime_ns)
        assert repository.ai.get(file_id).status == "stale"
    finally:
        service.close()

    reopened = Repository(repository.db_path)
    assert reopened.ai.get(file_id).status == "stale"
    assert reopened.ai.get_setting("model") == DEFAULT_AI_MODEL

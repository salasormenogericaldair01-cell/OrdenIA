"""Basic offscreen UI integration for the separate intelligent-analysis panel."""

import time
import json
from pathlib import Path

import pytest

from ordenia.ai.models import AIResponse, DEFAULT_AI_MODEL, ProviderStatus
from ordenia.ai.providers.base import AIProvider
from ordenia.database.repositories import Repository


class FakeProvider(AIProvider):
    name = "ollama"

    def check(self) -> ProviderStatus:
        return ProviderStatus("ollama", True, ("qwen3:4b",), "ok")

    def generate(self, _system: str, _user: str, _model: str) -> AIResponse:
        return AIResponse(json.dumps({
            "document_type": "material_academico", "topic": "bases de datos",
            "tags": ["SQL"], "suggested_path": "Estudios/Base de Datos",
            "confidence": 0.9, "reason": "Material académico.",
        }))


def test_ai_panel_updates_without_blocking_content_panel(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from ordenia.services.file_service import FileService
    from ordenia.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "course.txt"
    path.write_text("SQL y normalización", encoding="utf-8")
    other_path = root / "other.txt"
    other_path.write_text("Sin sugerencia", encoding="utf-8")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    stat = path.stat()
    file_id = repository.upsert_file(folder.id, path, stat.st_size, "Documentos", "2026-01-01", stat.st_mtime_ns).id
    other_stat = other_path.stat()
    other_id = repository.upsert_file(
        folder.id, other_path, other_stat.st_size, "Documentos", "2026-01-01", other_stat.st_mtime_ns
    ).id
    service = FileService(repository)
    service.ai.provider = FakeProvider()
    window = MainWindow(repository, service)
    try:
        window.resize(1000, 620)
        window.navigation.setCurrentRow(1)
        window.show()
        app.processEvents()
        page = window.pages[1]
        course_row = next(
            row for row in range(page.table.rowCount())
            if page.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == file_id
        )
        page.table.setCurrentCell(course_row, 0)
        app.processEvents()
        assert page.inspector.tabs.currentIndex() == page.inspector.DETAILS_TAB
        page._analyze_selected()
        content_deadline = time.monotonic() + 5
        while repository.content.get(file_id).status != "indexed" and time.monotonic() < content_deadline:
            app.processEvents()
            time.sleep(0.02)
        assert repository.content.get(file_id).status == "indexed"
        page._analyze_ai()
        deadline = time.monotonic() + 5
        while repository.ai.get(file_id).status != "ready" and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        for _ in range(3):
            app.processEvents()
        assert page.ai_values["Estado IA"].text() == "Listo"
        assert page.ai_values["Tema"].text() == "bases de datos"
        assert page.ai_values["Confianza estimada"].text() == "Alta"
        assert page.ai_use_button.isEnabled()
        assert page.analysis_values["Estado"].text() == "Indexado"
        assert page.ai_section.isVisible()
        assert page.inspector.tabs.currentIndex() == page.inspector.AI_TAB
        assert page.inspector.ai_ready_badge.isVisible()
        assert page.inspector.ai_scroll.verticalScrollBar().value() == 0

        other_row = next(
            row for row in range(page.table.rowCount())
            if page.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == other_id
        )
        page.table.setCurrentCell(other_row, 0)
        for _ in range(2):
            app.processEvents()
        assert page.ai_values["Estado IA"].text() == "Sin analizar"
        assert page.ai_values["Tema"].text() == "—"
        assert not page.ai_use_button.isEnabled()
        assert not page.copy_suggestion_button.isEnabled()
        assert not page.inspector.ai_ready_badge.isVisible()
        assert page.inspector.tabs.currentIndex() == page.inspector.DETAILS_TAB
    finally:
        window.close()


@pytest.mark.parametrize("size", ((1366, 768), (1600, 900), (1920, 1080)))
def test_files_page_two_column_layout_at_desktop_sizes(tmp_path: Path, monkeypatch, size: tuple[int, int]) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from ordenia.services.file_service import FileService
    from ordenia.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    repository = Repository(tmp_path / "data.sqlite3")
    service = FileService(repository)
    window = MainWindow(repository, service)
    try:
        window.resize(*size)
        window.navigation.setCurrentRow(1)
        window.show()
        app.processEvents()
        page = window.pages[1]
        assert page.workspace_splitter.orientation() == Qt.Orientation.Horizontal
        assert page.workspace_splitter.count() == 2
        assert all(part > 0 for part in page.workspace_splitter.sizes())
        assert page.inspector.width() >= page.inspector.minimumWidth()
        assert [page.inspector.tabs.tabText(index) for index in range(page.inspector.tabs.count())] == [
            "Detalles", "Contenido", "IA"
        ]
        assert page.table.isVisible()
        assert page.inspector.isVisible()
    finally:
        window.close()


def test_using_suggestion_without_final_confirmation_never_moves_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
    from ordenia.ai.models import AISuggestion
    from ordenia.services.file_service import FileService
    from ordenia.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "course.txt"
    path.write_text("SQL", encoding="utf-8")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    stat = path.stat()
    file_id = repository.upsert_file(folder.id, path, stat.st_size, "Documentos", "2026-01-01", stat.st_mtime_ns).id
    repository.ai.save(file_id, AISuggestion(
        "material_academico", "bases de datos", ("SQL",), "Estudios/Base de Datos", 0.9, "Curso."
    ), "ollama", "qwen3:4b", stat.st_size, stat.st_mtime_ns)
    service = FileService(repository)
    window = MainWindow(repository, service)
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("Estudios/Base de Datos", True))
    monkeypatch.setattr(QMessageBox, "exec", lambda _dialog: 0)
    try:
        page = window.pages[1]
        page.table.setCurrentCell(0, 0)
        page._use_ai_suggestion()
        app.processEvents()
        assert path.is_file()
        assert repository.list_operations() == []
    finally:
        window.close()


def test_settings_marks_instruct_as_recommended_without_replacing_old_choice(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from ordenia.services.file_service import FileService
    from ordenia.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    repository = Repository(tmp_path / "data.sqlite3")
    repository.ai.set_setting("model", "qwen3:4b")
    service = FileService(repository)
    window = MainWindow(repository, service)
    try:
        settings = window.pages[4]
        settings._on_ai_status(ProviderStatus("ollama", True, ("qwen3:4b", DEFAULT_AI_MODEL), "ok"))
        app.processEvents()
        assert settings.ai_model.text() == "qwen3:4b"
        assert f"{DEFAULT_AI_MODEL} (recomendado)" in settings.ai_models.text()
        assert settings.ai_status.text() == "● Disponible"
    finally:
        window.close()

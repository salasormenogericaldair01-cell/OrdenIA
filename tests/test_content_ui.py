"""The UI requests content work through the service and shows indexed matches."""

import time
from pathlib import Path


def test_manual_content_action_search_snippet_and_details(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from ordenia.database.repositories import Repository
    from ordenia.services.file_service import FileService
    from ordenia.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "neutral.txt"
    path.write_text("Un proyecto usa ESP32 sensor ultrasónico", encoding="utf-8")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    stat = path.stat()
    file_id = repository.upsert_file(folder.id, path, stat.st_size, "Documentos", "2026-01-01", stat.st_mtime_ns).id
    service = FileService(repository)
    window = MainWindow(repository, service)
    try:
        page = window.pages[1]
        page.table.setCurrentCell(0, 0)
        page._analyze_selected()
        deadline = time.monotonic() + 8
        while repository.content.get(file_id).status != "indexed" and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        app.processEvents()
        assert repository.content.get(file_id).status == "indexed"
        page.search_name.setChecked(False)
        page.search_box.setText("ESP32")
        app.processEvents()
        assert page.table.rowCount() == 1
        assert "ESP32" in page.table.item(0, 8).text()
        page.table.setCurrentCell(0, 0)
        assert page.analysis_values["Estado"].text() == "Indexado"
        assert "ESP32" in page.content_preview.text()
        assert window.pages[0].content_values["Indexados"].text() == "1"
    finally:
        window.close()

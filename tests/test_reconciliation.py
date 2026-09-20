"""Index reconciliation never touches user files or deletes operation history."""

import sqlite3
import time
from pathlib import Path

import pytest

from ordenia.core.classifier import ExtensionClassifier
from ordenia.database.repositories import Repository
from ordenia.monitoring.watcher import FolderWatcher
from ordenia.services.file_service import FileService


def _legacy_index(database: Path, root: Path) -> None:
    with sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE watched_folders (id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL,
                enabled INTEGER NOT NULL, removed INTEGER NOT NULL, include_subfolders INTEGER NOT NULL,
                destination_strategy TEXT NOT NULL, custom_destination TEXT, created_at TEXT NOT NULL);
            CREATE TABLE files (id INTEGER PRIMARY KEY, watched_folder_id INTEGER NOT NULL,
                path TEXT UNIQUE NOT NULL, path_key TEXT UNIQUE NOT NULL, source_directory TEXT NOT NULL,
                name TEXT NOT NULL, extension TEXT NOT NULL, size INTEGER NOT NULL, category TEXT NOT NULL,
                detected_at TEXT NOT NULL, modified_at TEXT NOT NULL, status TEXT NOT NULL);
            CREATE TABLE operations (id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
                original_path TEXT NOT NULL, destination_path TEXT NOT NULL, created_at TEXT NOT NULL,
                operation_type TEXT NOT NULL, status TEXT NOT NULL, error_message TEXT,
                undone_at TEXT, restored_path TEXT);
        """)
        db.execute("INSERT INTO watched_folders VALUES (1, ?, 1, 0, 1, 'inside', NULL, '2026-01-01')", (str(root),))
        for identifier, name, category in (
            (1, "informe.pdf", "Documentos"), (2, "foto.jpg", "Imágenes"),
            (3, "desktop.ini", "Otros"), (4, "archivo_que_ya_no_existe.pdf", "Documentos"),
        ):
            path = root / name
            db.execute("INSERT INTO files VALUES (?, 1, ?, ?, ?, ?, ?, 1, ?, '2026-01-01', '2026-01-01', 'Pendiente')",
                       (identifier, str(path), str(path).casefold(), str(root), name, path.suffix, category))
        db.execute("INSERT INTO operations VALUES (1, 4, ?, ?, '2026-01-01', 'move', 'Deshecho', NULL, '2026-01-02', ?)",
                   (str(root / "archivo_que_ya_no_existe.pdf"), str(root / "OrdenIA" / "Documentos" / "archivo_que_ya_no_existe.pdf"),
                    str(root / "archivo_que_ya_no_existe.pdf")))


def test_legacy_excluded_missing_and_history_survive_reconciliation(tmp_path: Path) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    for name in ("informe.pdf", "foto.jpg", "desktop.ini"):
        (root / name).write_text(name)
    database = tmp_path / "old.sqlite3"
    _legacy_index(database, root)
    repository = Repository(database)
    service = FileService(repository)
    try:
        assert repository.get_file(3).index_state == "excluded"  # startup migration hides old desktop.ini
        assert {file.name for file in repository.search_files()[0]} == {"informe.pdf", "foto.jpg", "archivo_que_ya_no_existe.pdf"}
        service._scan_folder(1)
        assert repository.index_counts(1) == {"active": 2, "excluded": 1, "missing": 1}
        assert repository.registered_counts()[1] == 2
        assert repository.dashboard_counts()["total"] == 2
        assert repository.count_by_category() == {"Documentos": 1, "Imágenes": 1}
        assert {file.name for file in repository.recent_files()} == {"informe.pdf", "foto.jpg"}
        assert {file.name for file in repository.search_files()[0]} == {"informe.pdf", "foto.jpg"}
        assert len(repository.list_operations()) == 1
        assert repository.get_operation(1).status == "Deshecho"
        service._scan_folder(1)
        assert repository.index_counts(1) == {"active": 2, "excluded": 1, "missing": 1}
        assert len(repository.list_files()) == 4
        assert len(repository.list_operations()) == 1
        assert (root / "desktop.ini").is_file()
    finally:
        service.close()


@pytest.mark.parametrize("name", ["desktop.ini", "Thumbs.db", ".DS_Store", "download.part", "~$lock.docx"])
def test_legacy_exclusion_policy_hides_existing_rows(tmp_path: Path, name: str) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / name
    path.write_text("old")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    file = repository.upsert_file(folder.id, path, 3, "Otros", "2026-01-01")
    service = FileService(repository)
    try:
        assert repository.get_file(file.id).index_state == "excluded"
        assert repository.search_files()[1] == 0
        assert repository.registered_counts().get(folder.id, 0) == 0
        assert path.is_file()
    finally:
        service.close()


def test_legacy_unorganized_row_inside_managed_destination_is_excluded(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    managed = root / "OrdenIA" / "Documentos"
    managed.mkdir(parents=True)
    path = managed / "orphan.pdf"
    path.write_text("old")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    file = repository.upsert_file(folder.id, path, 3, "Documentos", "2026-01-01")
    service = FileService(repository)
    try:
        assert repository.get_file(file.id).index_state == "excluded"
        assert repository.search_files()[1] == 0
        assert path.is_file()
    finally:
        service.close()


def test_reconcile_external_deletion_and_reappearance(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "report.pdf"
    path.write_text("report")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    file = repository.upsert_file(folder.id, path, 6, "Documentos", "2026-01-01")
    service = FileService(repository)
    try:
        path.unlink()  # simulated external deletion in a temporary test directory
        service._scan_folder(folder.id)
        assert repository.get_file(file.id).index_state == "missing"
        assert repository.registered_counts().get(folder.id, 0) == 0
        path.write_text("report")
        service._scan_folder(folder.id)
        assert repository.get_file(file.id).index_state == "active"
        assert repository.registered_counts()[folder.id] == 1
        assert len(repository.list_files()) == 1
    finally:
        service.close()


def test_external_rename_reuses_record_and_move_out_marks_missing(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    old = root / "old.pdf"
    renamed = root / "new.pdf"
    old.write_text("report")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    file = repository.upsert_file(folder.id, old, 6, "Documentos", "2026-01-01")
    watcher = FolderWatcher(repository, ExtensionClassifier(), lambda: None)
    old.rename(renamed)
    watcher._process_move(folder, old, renamed)
    assert repository.get_file(file.id).path == renamed
    assert repository.get_file(file.id).index_state == "active"
    assert len(repository.list_files()) == 1
    outside = tmp_path / "outside.pdf"
    renamed.rename(outside)
    watcher._process_move(folder, renamed, outside)
    assert repository.get_file(file.id).index_state == "missing"
    assert repository.registered_counts().get(folder.id, 0) == 0


def test_watchdog_moved_event_reuses_existing_record(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    old = root / "old.pdf"
    new = root / "new.pdf"
    old.write_text("report")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    file = repository.upsert_file(folder.id, old, 6, "Documentos", "2026-01-01")
    watcher = FolderWatcher(repository, ExtensionClassifier(), lambda: None)
    watcher.start()
    try:
        old.rename(new)
        deadline = time.monotonic() + 6
        while repository.get_file(file.id).path != new and time.monotonic() < deadline:
            time.sleep(0.05)
        assert repository.get_file(file.id).path == new
        assert repository.get_file(file.id).index_state == "active"
        assert len(repository.list_files()) == 1
    finally:
        watcher.stop()


def test_organized_file_stays_active_through_reconciliation_and_undo(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "report.pdf"
    path.write_text("report")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    file = repository.upsert_file(folder.id, path, 6, "Documentos", "2026-01-01")
    service = FileService(repository)
    try:
        service._organize(file.id)
        operation = repository.list_operations()[0]
        service._scan_folder(folder.id)
        assert repository.get_file(file.id).index_state == "active"
        assert repository.registered_counts()[folder.id] == 1
        assert repository.get_operation(operation.id).status == "Completado"
        service._undo(operation.id)
        service._scan_folder(folder.id)
        assert repository.get_file(file.id).path == path
        assert repository.get_file(file.id).index_state == "active"
        assert repository.get_operation(operation.id).status == "Deshecho"
        assert repository.registered_counts()[folder.id] == 1
    finally:
        service.close()


def test_ui_counts_and_scan_summary_use_active_rows(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from ordenia.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    root = tmp_path / "watched"
    root.mkdir()
    (root / "informe.pdf").write_text("report")
    (root / "desktop.ini").write_text("system")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    for name, category in (("informe.pdf", "Documentos"), ("desktop.ini", "Otros")):
        path = root / name
        repository.upsert_file(folder.id, path, path.stat().st_size, category, "2026-01-01")
    service = FileService(repository)
    window = MainWindow(repository, service)
    try:
        service._scan_folder(folder.id)
        app.processEvents()
        assert window.pages[1].table.rowCount() == 1
        assert window.pages[1].table.item(0, 0).text() == "informe.pdf"
        assert window.pages[0].values["Pendientes de revisar"].text() == "1"
        assert window.pages[2].table.item(0, 4).text() == "1"
        assert "1 excluidos" in window.pages[2].table.item(0, 4).toolTip()
        assert "1 encontrados" in window.pages[2].progress_label.text()
        assert "1 activos" in window.pages[2].progress_label.text()
    finally:
        window.close()

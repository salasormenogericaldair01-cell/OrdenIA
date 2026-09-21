import sqlite3
from pathlib import Path

import pytest

from ordenia.database.repositories import Repository


def test_move_history_and_removed_folder_are_retained(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(tmp_path)
    original = tmp_path / "report.pdf"
    original.write_text("x")
    file = repository.upsert_file(folder.id, original, 1, "Documentos", "2026-01-01T00:00:00+00:00")
    destination = tmp_path / "OrdenIA" / "Documentos" / "report.pdf"
    destination.parent.mkdir(parents=True)
    original.rename(destination)
    repository.record_move(file.id, original, destination)
    operation = repository.list_operations()[0]
    assert repository.get_file(file.id).status == "Organizado"
    assert operation.status == "Completado"
    destination.rename(original)
    repository.record_undo(operation, original)
    assert repository.get_file(file.id).status == "Pendiente"
    assert repository.list_operations()[1].status == "Deshecho"
    assert repository.list_operations()[1].undone_at is not None
    repository.remove_folder(folder.id)
    assert repository.list_folders() == []
    assert repository.get_folder(folder.id).path == tmp_path


def test_repository_refuses_completed_move_without_physical_transfer(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(tmp_path)
    original = tmp_path / "report.pdf"
    original.write_text("still here")
    file = repository.upsert_file(folder.id, original, original.stat().st_size, "Documentos", "2026-01-01T00:00:00+00:00")
    destination = tmp_path / "OrdenIA" / "Documentos" / "report.pdf"
    with pytest.raises(OSError):
        repository.record_move(file.id, original, destination)
    assert repository.get_file(file.id).status == "Pendiente"
    assert repository.list_operations() == []


def test_reopen_preserves_file_states_paths_and_history(tmp_path: Path) -> None:
    database = tmp_path / "data.sqlite3"
    repository = Repository(database)
    folder = repository.add_folder(tmp_path)
    original = tmp_path / "report.pdf"
    original.write_text("report")
    ignored_path = tmp_path / "skip.xyz"
    ignored_path.write_text("skip")
    file = repository.upsert_file(folder.id, original, 6, "Documentos", "2026-01-01T00:00:00+00:00")
    ignored = repository.upsert_file(folder.id, ignored_path, 4, "Otros", "2026-01-01T00:00:00+00:00")
    repository.set_file_status(ignored.id, "Ignorado")
    destination = tmp_path / "OrdenIA" / "Documentos" / original.name
    destination.parent.mkdir(parents=True)
    original.rename(destination)
    repository.record_move(file.id, original, destination)

    reopened = Repository(database)
    assert reopened.get_file(file.id).path == destination
    assert reopened.get_file(file.id).status == "Organizado"
    assert reopened.get_file(ignored.id).status == "Ignorado"
    operation = reopened.list_operations()[0]
    assert operation.original_path == original
    assert operation.destination_path == destination
    assert operation.status == "Completado"

    destination.rename(original)
    reopened.record_undo(operation, original)
    reopened_again = Repository(database)
    assert reopened_again.get_file(file.id).path == original
    assert reopened_again.get_file(file.id).status == "Pendiente"
    assert reopened_again.get_operation(operation.id).status == "Deshecho"
    assert reopened_again.get_operation(operation.id).restored_path == original
    assert reopened_again.list_operations()[0].status == "Completado"


def test_migration_preserves_legacy_operation_then_reconciles_missing_file(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    original = tmp_path / "report.pdf"
    original.write_text("user data")
    destination = tmp_path / "OrdenIA" / "Documentos" / original.name
    with sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE watched_folders (id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, enabled INTEGER NOT NULL, removed INTEGER NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE files (id INTEGER PRIMARY KEY, watched_folder_id INTEGER NOT NULL, path TEXT UNIQUE NOT NULL, source_directory TEXT NOT NULL, name TEXT NOT NULL, extension TEXT NOT NULL, size INTEGER NOT NULL, category TEXT NOT NULL, detected_at TEXT NOT NULL, modified_at TEXT NOT NULL, status TEXT NOT NULL);
            CREATE TABLE operations (id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL, original_path TEXT NOT NULL, destination_path TEXT NOT NULL, created_at TEXT NOT NULL, operation_type TEXT NOT NULL, undone_at TEXT, restored_path TEXT);
        """)
        db.execute("INSERT INTO watched_folders VALUES (1, ?, 1, 0, '2026-01-01')", (str(tmp_path),))
        db.execute("INSERT INTO files VALUES (1, 1, ?, ?, ?, '.pdf', 9, 'Documentos', '2026-01-01', '2026-01-01', 'Organizado')", (str(destination), str(tmp_path), original.name))
        db.execute("INSERT INTO operations VALUES (1, 1, ?, ?, '2026-01-01', 'move', NULL, NULL)", (str(original), str(destination)))

    repository = Repository(database)
    assert original.read_text() == "user data"
    assert repository.get_operation(1).status == "Completado"
    assert repository.get_file(1).status == "Organizado"
    assert repository.get_file(1).path == destination
    counts = repository.reconcile_folder(repository.get_folder(1), ())
    assert counts["missing"] == 1
    assert repository.get_file(1).index_state == "missing"
    assert Repository(database).get_operation(1).status == "Completado"


def test_v01_database_migrates_without_losing_files_or_operations(tmp_path: Path) -> None:
    database = tmp_path / "ordenia.sqlite3"
    root = tmp_path / "watched"
    root.mkdir()
    source = root / "report.pdf"
    destination = root / "OrdenIA" / "Documentos" / source.name
    destination.parent.mkdir(parents=True)
    destination.write_text("old user data")
    with sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE watched_folders (id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, enabled INTEGER NOT NULL, removed INTEGER NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE files (id INTEGER PRIMARY KEY, watched_folder_id INTEGER NOT NULL, path TEXT UNIQUE NOT NULL, source_directory TEXT NOT NULL, name TEXT NOT NULL, extension TEXT NOT NULL, size INTEGER NOT NULL, category TEXT NOT NULL, detected_at TEXT NOT NULL, modified_at TEXT NOT NULL, status TEXT NOT NULL);
            CREATE TABLE operations (id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL, original_path TEXT NOT NULL, destination_path TEXT NOT NULL, created_at TEXT NOT NULL, operation_type TEXT NOT NULL, status TEXT NOT NULL, error_message TEXT, undone_at TEXT, restored_path TEXT);
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        db.execute("INSERT INTO watched_folders VALUES (1, ?, 1, 0, '2026-01-01')", (str(root),))
        db.execute("INSERT INTO files VALUES (1, 1, ?, ?, ?, '.pdf', 13, 'Documentos', '2026-01-01', '2026-01-01', 'Organizado')", (str(destination), str(root), source.name))
        db.execute("INSERT INTO operations VALUES (1, 1, ?, ?, '2026-01-01', 'move', 'Completado', NULL, NULL, NULL)", (str(source), str(destination)))
        db.execute("INSERT INTO settings VALUES ('show_notifications', '0')")
    repository = Repository(database)
    assert destination.read_text() == "old user data"
    assert repository.get_file(1).status == "Organizado"
    assert repository.get_file(1).path == destination
    assert repository.get_file(1).path_key
    assert repository.get_operation(1).status == "Completado"
    assert repository.get_setting("show_notifications") == "0"
    folder = repository.get_folder(1)
    assert folder.include_subfolders is True
    assert folder.destination_strategy == "inside"
    assert folder.custom_destination is None
    assert Repository(database).get_file(1).id == 1

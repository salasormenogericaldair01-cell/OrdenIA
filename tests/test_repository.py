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


def test_migration_marks_unverified_legacy_move_failed_without_deleting_data(tmp_path: Path) -> None:
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
    assert repository.get_operation(1).status == "Fallido"
    assert repository.get_file(1).status == "Pendiente"
    assert repository.get_file(1).path == original
    assert Repository(database).get_operation(1).status == "Fallido"

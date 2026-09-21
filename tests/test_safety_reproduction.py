"""Regression assertions for the independent V0.3 safety reproductions."""

import errno
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from ordenia.analysis.models import AnalysisOutcome
from ordenia.core.organizer import Organizer, _move_without_replacing, verify_transfer
from ordenia.database.repositories import Repository
from ordenia.database.connection import connect
from ordenia.database.models import IndexedEntry
from ordenia.services.file_locks import FileOperationLocks
from ordenia.services.file_service import FileService


def _indexed(repository: Repository, root: Path, path: Path) -> int:
    folder = repository.list_folders()[0] if repository.list_folders() else repository.add_folder(root)
    stat = path.stat()
    return repository.upsert_file(folder.id, path, stat.st_size, "Documentos", "2026-01-01", stat.st_mtime_ns).id


def _cross_volume(monkeypatch: pytest.MonkeyPatch, source: Path) -> None:
    original = os.link

    def link(src: str | Path, dst: str | Path, *args: object, **kwargs: object) -> None:
        if Path(src) == source:
            raise OSError(errno.EXDEV, "different volumes")
        original(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", link)


def _partial_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    def copy(incoming: object, outgoing: object, length: int = 0) -> None:
        outgoing.write(incoming.read(8))  # type: ignore[attr-defined]
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(shutil, "copyfileobj", copy)


def test_cross_volume_move_success_keeps_source_until_verified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source" / "file.pdf"
    source.parent.mkdir()
    source.write_bytes(b"complete-content")
    target = tmp_path / "target" / "file.pdf"
    _cross_volume(monkeypatch, source)
    from ordenia.core import organizer as module
    original_verify = module._verify_copy
    checks = []

    def verify(path: Path, size: int) -> None:
        checks.append(source.exists())
        original_verify(path, size)

    monkeypatch.setattr(module, "_verify_copy", verify)
    result = _move_without_replacing(source, target)
    assert checks == [True, True]
    assert result.read_bytes() == b"complete-content"
    verify_transfer(source, target)


@pytest.mark.parametrize("operation", ["move", "undo"])
def test_partial_cross_volume_copy_keeps_last_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    source = tmp_path / "left" / "file.pdf"
    source.parent.mkdir()
    source.write_bytes(b"complete-content")
    target = tmp_path / "right" / "file.pdf"
    _cross_volume(monkeypatch, source)
    _partial_copy(monkeypatch)
    with pytest.raises(OSError, match="disk full"):
        (Organizer().undo(source, target) if operation == "undo" else _move_without_replacing(source, target))
    assert source.read_bytes() == b"complete-content"
    assert not target.exists()


def test_cross_volume_undo_success_and_name_collision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "right" / "file.pdf"
    destination.parent.mkdir()
    destination.write_bytes(b"original")
    original = tmp_path / "left" / "file.pdf"
    original.parent.mkdir()
    original.write_bytes(b"existing")
    _cross_volume(monkeypatch, destination)
    restored = Organizer().undo(destination, original)
    assert restored.name == "file (1).pdf"
    assert restored.read_bytes() == b"original"
    assert original.read_bytes() == b"existing"
    verify_transfer(destination, restored)


@pytest.mark.parametrize("operation", ["move", "undo"])
@pytest.mark.parametrize("phase", ["verify", "publish", "delete_source"])
def test_cross_volume_phase_failure_preserves_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, phase: str,
) -> None:
    source = tmp_path / "left" / "file.pdf"
    source.parent.mkdir()
    source.write_bytes(b"complete-content")
    target = tmp_path / "right" / "file.pdf"
    _cross_volume(monkeypatch, source)
    from ordenia.core import organizer as module
    if phase == "verify":
        def fail_verify(_path: Path, _size: int) -> None:
            raise OSError("verification failed")
        monkeypatch.setattr(module, "_verify_copy", fail_verify)
    elif phase == "publish":
        original_link = os.link
        def fail_publish(src: str | Path, dst: str | Path, *args: object, **kwargs: object) -> None:
            if Path(src).parent.name.startswith(".ordenia-"):
                raise PermissionError("publication failed")
            original_link(src, dst, *args, **kwargs)
        monkeypatch.setattr(os, "link", fail_publish)
        def fail_rename(_source: str | Path, _target: str | Path) -> None:
            raise PermissionError("publication failed")
        monkeypatch.setattr(os, "rename", fail_rename)
    else:
        original_unlink = Path.unlink
        def fail_unlink(path: Path, *args: object, **kwargs: object) -> None:
            if path == source:
                raise PermissionError("source deletion failed")
            original_unlink(path, *args, **kwargs)
        monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(OSError):
        Organizer().undo(source, target) if operation == "undo" else _move_without_replacing(source, target)
    assert source.read_bytes() == b"complete-content"
    assert not target.exists()


def test_failed_cross_volume_move_keeps_sqlite_and_history_consistent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    source = root / "file.txt"
    source.write_bytes(b"complete-content")
    repository = Repository(tmp_path / "db.sqlite3")
    file_id = _indexed(repository, root, source)
    service = FileService(repository)
    try:
        _cross_volume(monkeypatch, source)
        _partial_copy(monkeypatch)
        service._organize(file_id)
        assert source.read_bytes() == b"complete-content"
        assert repository.get_file(file_id).path == source
        assert repository.get_file(file_id).status == "Pendiente"
        assert [operation.status for operation in repository.list_operations()] == ["Fallido"]
    finally:
        service.close()


def test_failed_cross_volume_undo_keeps_sqlite_and_history_consistent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    source = root / "file.txt"
    source.write_bytes(b"complete-content")
    repository = Repository(tmp_path / "db.sqlite3")
    file_id = _indexed(repository, root, source)
    service = FileService(repository)
    try:
        service._organize(file_id)
        move = repository.list_operations()[0]
        destination = move.destination_path
        _cross_volume(monkeypatch, destination)
        _partial_copy(monkeypatch)
        service._undo(move.id)
        assert destination.read_bytes() == b"complete-content"
        assert not source.exists()
        assert repository.get_file(file_id).path == destination
        assert repository.get_operation(move.id).status == "Completado"
        assert [item.status for item in repository.list_operations()] == ["Fallido", "Completado"]
    finally:
        service.close()


def test_analyze_and_organize_same_file_serialize_other_file_proceeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    a, b = root / "a.txt", root / "b.txt"
    a.write_text("sensor ultrasónico", encoding="utf-8")
    b.write_text("independent", encoding="utf-8")
    repository = Repository(tmp_path / "db.sqlite3")
    a_id = _indexed(repository, root, a)
    b_id = _indexed(repository, root, b)
    service = FileService(repository)
    entered, release = threading.Event(), threading.Event()
    original = service.content.analyzer.analyze

    def slow(path: Path) -> AnalysisOutcome:
        if path == a:
            entered.set()
            assert release.wait(5)
        return original(path)

    monkeypatch.setattr(service.content.analyzer, "analyze", slow)
    try:
        service.analyze_content([a_id])
        assert entered.wait(5)
        moving = threading.Thread(target=service._organize, args=(a_id,))
        moving.start()
        service._organize(b_id)
        assert repository.get_file(b_id).status == "Organizado"
        assert repository.get_file(a_id).status == "Pendiente"
        release.set()
        moving.join(5)
        assert not moving.is_alive()
        assert repository.get_file(a_id).status == "Organizado"
        assert repository.content.get(a_id).status == "indexed"
        assert repository.search_files("ultrasónico", search_name=False, search_content=True)[1] == 1
        assert not service.file_locks._entries
    finally:
        release.set()
        service.close()


def test_analysis_of_replaced_path_is_stale_not_indexed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "file.txt"
    path.write_text("secret-old", encoding="utf-8")
    repository = Repository(tmp_path / "db.sqlite3")
    file_id = _indexed(repository, root, path)
    service = FileService(repository)
    original = service.content.analyzer.analyze

    def replace_after_read(source: Path) -> AnalysisOutcome:
        result = original(source)
        source.rename(root / "renamed.txt")
        source.write_text("new-content", encoding="utf-8")
        return result

    monkeypatch.setattr(service.content.analyzer, "analyze", replace_after_read)
    try:
        service.content._process(file_id, False, False)
        assert repository.content.get(file_id).status == "stale"
        assert repository.search_files("secret-old", search_name=False, search_content=True)[1] == 0
    finally:
        service.close()


def test_analysis_external_rename_same_fingerprint_is_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    old, new = root / "old.txt", root / "new.txt"
    old.write_text("sensor-phrase", encoding="utf-8")
    repository = Repository(tmp_path / "db.sqlite3")
    file_id = _indexed(repository, root, old)
    folder = repository.get_folder(1)
    service = FileService(repository)
    original = service.content.analyzer.analyze

    def rename_after_read(path: Path) -> AnalysisOutcome:
        outcome = original(path)
        path.rename(new)
        stat = new.stat()
        assert repository.reconcile_external_move(folder, old, new, (), IndexedEntry(
            new, stat.st_size, "Documentos", "2026-01-01", stat.st_mtime_ns))
        return outcome

    monkeypatch.setattr(service.content.analyzer, "analyze", rename_after_read)
    try:
        service.content._process(file_id, False, False)
        assert repository.get_file(file_id).path == new
        assert repository.content.get(file_id).status == "stale"
        assert repository.search_files("sensor", search_name=False, search_content=True)[1] == 0
    finally:
        service.close()


@pytest.mark.parametrize("query,expected", [
    ("sensor", 2), ("sensor ultrasónico", 2), ("sensor muy ultrasónico", 0),
    ("ultrasónico", 2), ("sensor + ultrasónico!", 2), ("other", 0),
])
def test_fts_and_fallback_multiword(tmp_path: Path, query: str, expected: int) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    repository = Repository(tmp_path / "db.sqlite3")
    folder = repository.add_folder(root)
    for name, content in (("one.txt", "sensor ultrasónico"), ("two.txt", "sensor de distancia ultrasónico")):
        path = root / name
        path.write_text(content, encoding="utf-8")
        file_id = _indexed(repository, root, path)
        stat = path.stat()
        repository.content.save(file_id, AnalysisOutcome("indexed", text=content), stat.st_size, stat.st_mtime_ns)
    for fts in (True, False):
        repository.content.fts_enabled = fts
        rows, count = repository.search_files(query, search_name=False, search_content=True)
        assert count == expected
        assert len(rows) == expected


def test_fts_matches_accents_without_requiring_exact_diacritics(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "notes.txt"
    path.write_text("sensor ultrasónico", encoding="utf-8")
    repository = Repository(tmp_path / "db.sqlite3")
    file_id = _indexed(repository, root, path)
    stat = path.stat()
    repository.content.save(file_id, AnalysisOutcome("indexed", text="sensor ultrasónico"),
                            stat.st_size, stat.st_mtime_ns)
    assert repository.content.fts_enabled
    assert repository.search_files("sensor ultrasonico", search_name=False, search_content=True)[1] == 1


def test_verify_transfer_rejects_source_still_present_and_truncated_copy(tmp_path: Path) -> None:
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"complete")
    target.write_bytes(b"short")
    with pytest.raises(OSError):
        verify_transfer(source, target)


def test_file_lock_table_is_cleaned_and_files_are_independent() -> None:
    locks = FileOperationLocks()
    with locks.hold(1):
        with locks.hold(2):
            assert set(locks._entries) == {1, 2}
    assert locks._entries == {}


def test_legacy_case_collision_preserves_rows_and_unique_keys(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    root = tmp_path / "watched"
    root.mkdir()
    real = root / "file.txt"
    real.write_text("content")
    with sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE watched_folders(id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL,
                enabled INTEGER NOT NULL, removed INTEGER NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE files(id INTEGER PRIMARY KEY, watched_folder_id INTEGER NOT NULL,
                path TEXT UNIQUE NOT NULL, source_directory TEXT NOT NULL, name TEXT NOT NULL,
                extension TEXT NOT NULL, size INTEGER NOT NULL, category TEXT NOT NULL,
                detected_at TEXT NOT NULL, modified_at TEXT NOT NULL, status TEXT NOT NULL);
        """)
        db.execute("INSERT INTO watched_folders VALUES (1, ?, 1, 0, '2026-01-01')", (str(root),))
        for file_id, path in ((1, real), (2, root / "FILE.TXT")):
            db.execute("INSERT INTO files VALUES (?, 1, ?, ?, ?, '.txt', 7, 'Documentos', '2026-01-01', '2026-01-01', 'Pendiente')",
                       (file_id, str(path), str(root), path.name))
    repository = Repository(database)
    with connect(database) as db:
        rows = db.execute("SELECT id, path_key FROM files ORDER BY id").fetchall()
    assert len(rows) == 2
    assert len({row["path_key"] for row in rows}) == 2
    assert sum("#legacy-" in row["path_key"] for row in rows) == 1
    # The suffix protects the unique key and retains historical references.
    # Reconciliation, rather than filesystem-dependent migration, decides visibility.
    counts = repository.reconcile_folder(repository.get_folder(1), ())
    assert counts["active"] == 1


@pytest.mark.parametrize("destination_online", [False, True])
def test_legacy_operation_migration_is_independent_of_disk_availability(
    tmp_path: Path, destination_online: bool,
) -> None:
    database = tmp_path / "legacy.sqlite3"
    root = tmp_path / "watched"
    root.mkdir()
    original = root / "report.pdf"
    destination = root / "OrdenIA" / "Documentos" / "report.pdf"
    if destination_online:
        destination.parent.mkdir(parents=True)
        destination.write_text("historical content")
    with sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE watched_folders(id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL,
                enabled INTEGER NOT NULL, removed INTEGER NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE files(id INTEGER PRIMARY KEY, watched_folder_id INTEGER NOT NULL,
                path TEXT UNIQUE NOT NULL, source_directory TEXT NOT NULL, name TEXT NOT NULL,
                extension TEXT NOT NULL, size INTEGER NOT NULL, category TEXT NOT NULL,
                detected_at TEXT NOT NULL, modified_at TEXT NOT NULL, status TEXT NOT NULL);
            CREATE TABLE operations(id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
                original_path TEXT NOT NULL, destination_path TEXT NOT NULL,
                created_at TEXT NOT NULL, operation_type TEXT NOT NULL,
                undone_at TEXT, restored_path TEXT);
        """)
        db.execute("INSERT INTO watched_folders VALUES (1, ?, 1, 0, '2026-01-01')", (str(root),))
        db.execute("INSERT INTO files VALUES (1, 1, ?, ?, 'report.pdf', '.pdf', 18, 'Documentos', '2026-01-01', '2026-01-01', 'Organizado')",
                   (str(destination), str(root)))
        db.execute("INSERT INTO operations VALUES (1, 1, ?, ?, '2026-01-01', 'move', NULL, NULL)",
                   (str(original), str(destination)))
    repository = Repository(database)
    assert repository.get_operation(1).status == "Completado"
    assert repository.get_file(1).path == destination
    # Availability is classified later, without rewriting historical facts.
    counts = repository.reconcile_folder(repository.get_folder(1), (root / "OrdenIA",))
    assert counts["active" if destination_online else "missing"] == 1


def test_watcher_retries_unstable_event_automatically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ordenia.core.classifier import ExtensionClassifier
    from ordenia.monitoring.watcher import FolderWatcher

    root = tmp_path / "watched"
    root.mkdir()
    repository = Repository(tmp_path / "db.sqlite3")
    folder = repository.add_folder(root)
    watcher = FolderWatcher(repository, ExtensionClassifier(), lambda: None, retry_delays=(0.01,))
    path = root / "slow.txt"
    path.write_text("first")
    original = watcher._wait_stable
    first_done = threading.Event()
    calls = 0

    def first_unstable(file_path: Path) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_done.set()
            return False
        return original(file_path)

    monkeypatch.setattr(watcher, "_wait_stable", first_unstable)
    try:
        watcher.start()
        watcher._enqueue(folder, path)
        assert first_done.wait(5)
        deadline = time.monotonic() + 5
        while repository.dashboard_counts()["total"] == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert repository.dashboard_counts()["total"] == 1
        assert repository.search_files("slow")[1] == 1
        assert calls == 2
    finally:
        watcher.stop()

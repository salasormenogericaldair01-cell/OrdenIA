"""Content state, FTS search, movement preservation and safe migration."""

import sqlite3
import os
import threading
import time
from pathlib import Path

import pymupdf
import pytest
from docx import Document

from ordenia.database.repositories import Repository
from ordenia.services.file_service import FileService
from ordenia.services.content_service import ContentService


def _wait_for(repository: Repository, file_ids: list[int], status: str, timeout: float = 8) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(repository.content.get(file_id).status == status for file_id in file_ids):
            return
        time.sleep(0.02)
    assert {file_id: repository.content.get(file_id).status for file_id in file_ids} == {file_id: status for file_id in file_ids}


def _indexed(repository: Repository, folder_id: int, path: Path, category: str = "Documentos") -> int:
    stat = path.stat()
    return repository.upsert_file(folder_id, path, stat.st_size, category, "2026-01-01", stat.st_mtime_ns).id


def test_content_search_fts_filters_snippets_and_fallback(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    pdf = root / "documento1.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((70, 70), "ESP32 sensor ultrasonico")
        document.save(pdf)
    docx = root / "documento2.docx"
    document = Document()
    document.add_paragraph("inventario institucional")
    document.save(docx)
    txt = root / "notas.txt"
    txt.write_text("rutas de recolección", encoding="utf-8")
    code = root / "codigo.ino"
    code.write_text("ESP32 sensor de temperatura", encoding="utf-8")
    repository = Repository(tmp_path / "index.sqlite3")
    folder = repository.add_folder(root)
    ids = [_indexed(repository, folder.id, path, "Código" if path == code else "Documentos")
           for path in (pdf, docx, txt, code)]
    service = FileService(repository)
    try:
        assert all(repository.content.get(file_id).status == "pending" for file_id in ids)
        assert service.analyze_content(ids)
        _wait_for(repository, ids, "indexed")
        assert repository.content.fts_enabled
        results, total = repository.search_files("ESP32", search_name=False, search_content=True)
        assert total == 2 and {file.name for file in results} == {"documento1.pdf", "codigo.ino"}
        assert all("ESP32" in file.match_snippet and len(file.match_snippet) < 200 for file in results)
        assert repository.search_files("inventario", search_name=False, search_content=True)[0][0].name == docx.name
        assert repository.search_files("recolección", search_name=False, search_content=True)[0][0].name == txt.name
        filtered, total = repository.search_files("ESP32", "Pendiente", "Código", search_name=False, search_content=True)
        assert total == 1 and filtered[0].name == code.name
        assert repository.matching_file_ids("ESP32", "Pendiente", "Código", False, True) == [ids[3]]
        assert repository.search_files("ESP32", search_name=True, search_content=False)[1] == 0
        repository.content.fts_enabled = False
        assert repository.search_files("ESP32", search_name=False, search_content=True)[1] == 2
    finally:
        service.close()
    reopened = Repository(tmp_path / "index.sqlite3")
    assert reopened.content.get(ids[0]).status == "indexed"
    assert reopened.search_files("ESP32", search_name=False, search_content=True)[1] == 2


def test_change_marks_stale_removes_fts_and_reanalysis_restores_it(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "neutral.txt"
    path.write_text("sensor ESP32", encoding="utf-8")
    repository = Repository(tmp_path / "index.sqlite3")
    folder = repository.add_folder(root)
    file_id = _indexed(repository, folder.id, path)
    service = FileService(repository)
    try:
        service.analyze_content([file_id])
        _wait_for(repository, [file_id], "indexed")
        assert repository.search_files("ESP32", search_name=False, search_content=True)[1] == 1
        path.write_text("inventario nuevo", encoding="utf-8")
        stat = path.stat()
        repository.upsert_file(folder.id, path, stat.st_size, "Documentos", "2026-01-02", stat.st_mtime_ns)
        assert repository.content.get(file_id).status == "stale"
        assert repository.search_files("ESP32", search_name=False, search_content=True)[1] == 0
        service.analyze_content([file_id])
        _wait_for(repository, [file_id], "indexed")
        assert repository.search_files("inventario", search_name=False, search_content=True)[1] == 1
        assert repository.search_files("ESP32", search_name=False, search_content=True)[1] == 0
        previous = path.stat()
        path.write_text("x" * previous.st_size, encoding="utf-8")
        os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000_000))
        same_size = path.stat()
        assert same_size.st_size == previous.st_size
        repository.upsert_file(folder.id, path, same_size.st_size, "Documentos", "2026-01-03", same_size.st_mtime_ns)
        assert repository.content.get(file_id).status == "stale"
        assert repository.search_files("inventario", search_name=False, search_content=True)[1] == 0
    finally:
        service.close()


def test_organize_undo_and_reconcile_preserve_or_hide_content(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "neutral.txt"
    path.write_text("ESP32 dato local", encoding="utf-8")
    repository = Repository(tmp_path / "index.sqlite3")
    folder = repository.add_folder(root)
    file_id = _indexed(repository, folder.id, path)
    service = FileService(repository)
    try:
        service.analyze_content([file_id])
        _wait_for(repository, [file_id], "indexed")
        analyzed_at = repository.content.get(file_id).analyzed_at
        service._organize(file_id)
        operation = repository.list_operations()[0]
        assert operation.status == "Completado"
        assert repository.content.get(file_id).status == "indexed"
        assert repository.content.get(file_id).analyzed_at == analyzed_at
        assert repository.search_files("ESP32", search_name=False, search_content=True)[1] == 1
        service.analyze_content([file_id], force=True)
        deadline = time.monotonic() + 5
        while file_id in service.content._pending and time.monotonic() < deadline:
            time.sleep(0.02)
        assert repository.content.get(file_id).status == "indexed"
        service._undo(operation.id)
        assert repository.get_operation(operation.id).status == "Deshecho"
        assert repository.content.get(file_id).status == "indexed"
        assert repository.search_files("ESP32", search_name=False, search_content=True)[1] == 1
        path.unlink()
        service._scan_folder(folder.id)
        assert repository.get_file(file_id).index_state == "missing"
        assert repository.search_files("ESP32", search_name=False, search_content=True)[1] == 0
        assert repository.content.get(file_id).status == "indexed"  # retained for audit
    finally:
        service.close()


def test_content_service_never_reads_excluded_names_or_managed_destinations(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "watched"
    managed = root / "OrdenIA" / "Documentos"
    managed.mkdir(parents=True)
    legacy = root / "desktop.ini"
    legacy.write_text("private desktop configuration")
    destination_file = managed / "managed.txt"
    destination_file.write_text("private managed content")
    repository = Repository(tmp_path / "index.sqlite3")
    folder = repository.add_folder(root)
    repository.register_managed_root(root / "OrdenIA")
    ids = [_indexed(repository, folder.id, path) for path in (legacy, destination_file)]
    service = ContentService(repository)

    def forbidden(_path: Path):
        raise AssertionError("Excluded content was read")

    monkeypatch.setattr(service.analyzer, "analyze", forbidden)
    try:
        service.request(ids, force=True)
        _wait_for(repository, ids, "skipped")
        assert all(repository.content.get(file_id).character_count == 0 for file_id in ids)
    finally:
        service.close()


def test_unsupported_no_text_and_failed_states(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    legacy = root / "legacy.doc"
    legacy.write_text("legacy")
    blank = root / "blank.pdf"
    with pymupdf.open() as document:
        document.new_page()
        document.save(blank)
    broken = root / "broken.pdf"
    broken.write_bytes(b"broken")
    repository = Repository(tmp_path / "index.sqlite3")
    folder = repository.add_folder(root)
    ids = [_indexed(repository, folder.id, path) for path in (legacy, blank, broken)]
    service = FileService(repository)
    try:
        service.analyze_content(ids)
        deadline = time.monotonic() + 8
        while any(repository.content.get(file_id).status in {"pending", "analyzing"} for file_id in ids) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert [repository.content.get(file_id).status for file_id in ids] == ["unsupported", "no_text", "failed"]
        assert repository.content.get(ids[0]).error == "No compatible con análisis de contenido en V0.3"
        assert repository.content.get(ids[1]).error == "PDF sin texto extraíble"
    finally:
        service.close()


def test_analyzing_state_and_cancel_stops_new_tasks(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    repository = Repository(tmp_path / "index.sqlite3")
    folder = repository.add_folder(root)
    ids = []
    for index in range(12):
        path = root / f"file-{index}.txt"
        path.write_text("ESP32")
        ids.append(_indexed(repository, folder.id, path))
    service = FileService(repository)
    entered = threading.Event()
    release = threading.Event()
    original = service.content.analyzer.analyze

    def slow(path: Path):
        entered.set()
        assert release.wait(5)
        return original(path)

    monkeypatch.setattr(service.content.analyzer, "analyze", slow)
    try:
        job = service.analyze_content(ids)
        assert entered.wait(5)
        assert any(repository.content.get(file_id).status == "analyzing" for file_id in ids)
        service.cancel_content(job)
        release.set()
        deadline = time.monotonic() + 8
        while len(service.content._pending) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not service.content._pending
        assert sum(repository.content.get(file_id).status == "indexed" for file_id in ids) <= 2
        assert repository.content.counts().get("pending", 0) >= 10
    finally:
        release.set()
        service.close()


def test_v02_schema_migrates_without_losing_history_settings_or_index_state(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "informe.txt"
    path.write_text("inventario")
    database = tmp_path / "old.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE watched_folders(id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, enabled INTEGER NOT NULL,
                removed INTEGER NOT NULL, include_subfolders INTEGER NOT NULL, destination_strategy TEXT NOT NULL,
                custom_destination TEXT, created_at TEXT NOT NULL);
            CREATE TABLE files(id INTEGER PRIMARY KEY, watched_folder_id INTEGER NOT NULL, path TEXT UNIQUE NOT NULL,
                path_key TEXT UNIQUE NOT NULL, source_directory TEXT NOT NULL, name TEXT NOT NULL,
                extension TEXT NOT NULL, size INTEGER NOT NULL, category TEXT NOT NULL, detected_at TEXT NOT NULL,
                modified_at TEXT NOT NULL, status TEXT NOT NULL, index_state TEXT NOT NULL);
            CREATE TABLE operations(id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL, original_path TEXT NOT NULL,
                destination_path TEXT NOT NULL, created_at TEXT NOT NULL, operation_type TEXT NOT NULL,
                status TEXT NOT NULL, error_message TEXT, undone_at TEXT, restored_path TEXT);
            CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        db.execute("INSERT INTO watched_folders VALUES (1, ?, 1, 0, 1, 'inside', NULL, '2026-01-01')", (str(root),))
        db.execute("INSERT INTO files VALUES (1, 1, ?, ?, ?, ?, '.txt', 10, 'Documentos', '2026-01-01', '2026-01-01', 'Pendiente', 'active')",
                   (str(path), str(path).casefold(), str(root), path.name))
        db.execute("INSERT INTO operations VALUES (1, 1, ?, ?, '2026-01-01', 'move', 'Deshecho', NULL, '2026-01-02', ?)",
                   (str(path), str(root / "OrdenIA" / "Documentos" / path.name), str(path)))
        db.execute("INSERT INTO settings VALUES ('show_notifications', '0')")
    repository = Repository(database)
    assert repository.get_folder(1).path == root
    assert repository.get_file(1).index_state == "active"
    assert repository.get_operation(1).status == "Deshecho"
    assert repository.get_setting("show_notifications") == "0"
    assert repository.content.get(1).status == "pending"
    assert repository.content.fts_enabled
    assert Repository(database).get_file(1).id == 1


def test_external_rename_retains_index_when_fingerprint_is_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    old = root / "old.txt"
    new = root / "new.txt"
    old.write_text("ESP32 manual")
    repository = Repository(tmp_path / "index.sqlite3")
    folder = repository.add_folder(root)
    file_id = _indexed(repository, folder.id, old)
    service = FileService(repository)
    try:
        service.analyze_content([file_id])
        _wait_for(repository, [file_id], "indexed")
        analyzed_at = repository.content.get(file_id).analyzed_at
        old.rename(new)
        service.watcher._process_move(folder, old, new)
        assert repository.get_file(file_id).path == new
        assert repository.content.get(file_id).status == "indexed"
        assert repository.content.get(file_id).analyzed_at == analyzed_at
        assert repository.search_files("ESP32", search_name=False, search_content=True)[1] == 1
        assert len(repository.list_files()) == 1
    finally:
        service.close()


def test_automatic_analysis_is_opt_in_and_size_limit_is_persistent(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "new.txt"
    path.write_text("ESP32 local")
    repository = Repository(tmp_path / "index.sqlite3")
    folder = repository.add_folder(root)
    file_id = _indexed(repository, folder.id, path)
    service = FileService(repository)
    try:
        assert service.content.request([file_id], automatic=True) == 0
        assert repository.content.get(file_id).status == "pending"
        repository.set_setting("auto_analyze_content", "1")
        repository.set_setting("auto_analysis_max_mb", "1")
        assert service.content.request([file_id], automatic=True)
        _wait_for(repository, [file_id], "indexed")
        assert Repository(repository.db_path).get_setting("auto_analysis_max_mb") == "1"
    finally:
        service.close()


def test_watcher_can_automatically_analyze_and_refresh_changed_content(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    repository = Repository(tmp_path / "index.sqlite3")
    repository.add_folder(root)
    repository.set_setting("auto_analyze_content", "1")
    service = FileService(repository)
    try:
        time.sleep(0.3)  # Windows observer needs a moment to attach its directory handle.
        path = root / "neutral.txt"
        path.write_text("ESP32 primer texto", encoding="utf-8")
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            files = repository.list_files()
            if files and repository.content.get(files[0].id).status == "indexed":
                break
            time.sleep(0.05)
        assert len(repository.list_files()) == 1
        file_id = repository.list_files()[0].id
        assert repository.search_files("ESP32", search_name=False, search_content=True)[1] == 1
        path.write_text("inventario actualizado", encoding="utf-8")
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if repository.search_files("inventario", search_name=False, search_content=True)[1] == 1:
                break
            time.sleep(0.05)
        assert repository.get_file(file_id).id == file_id
        assert repository.search_files("ESP32", search_name=False, search_content=True)[1] == 0
        assert repository.search_files("inventario", search_name=False, search_content=True)[1] == 1
        assert len(repository.list_files()) == 1
    finally:
        service.close()


def test_automatic_size_limit_skips_large_text_without_loading_it(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "large.txt"
    path.write_bytes(b"x" * (1024 * 1024 + 1))
    repository = Repository(tmp_path / "index.sqlite3")
    folder = repository.add_folder(root)
    file_id = _indexed(repository, folder.id, path)
    repository.set_setting("auto_analyze_content", "1")
    repository.set_setting("auto_analysis_max_mb", "1")
    service = FileService(repository)
    try:
        service.content.request([file_id], automatic=True)
        _wait_for(repository, [file_id], "skipped")
        assert "demasiado grande" in repository.content.get(file_id).error
        assert repository.content.get(file_id).character_count == 0
    finally:
        service.close()

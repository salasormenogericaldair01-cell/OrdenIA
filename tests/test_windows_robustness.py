"""Windows robustness, database concurrency and packaged runtime paths."""

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ordenia.core.classifier import ExtensionClassifier
from ordenia.database.connection import connect
from ordenia.database.repositories import Repository
from ordenia.monitoring.watcher import FolderWatcher
from ordenia.platform import runtime


def _wait_until(predicate, timeout: float = 3) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_watcher_requeues_unstable_file_until_it_is_ready(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "large-copy.bin"
    path.write_bytes(b"complete")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    watcher = FolderWatcher(repository, ExtensionClassifier(), lambda: None,
                            retry_delays=(0.01, 0.01, 0.01))
    results = iter((False, False, True))
    calls = 0

    def stability(_path: Path) -> bool:
        nonlocal calls
        calls += 1
        return next(results)

    monkeypatch.setattr(watcher, "_wait_stable", stability)
    try:
        watcher.start()
        watcher._enqueue(folder, path)
        _wait_until(lambda: repository.dashboard_counts()["total"] == 1)
        assert calls == 3
        assert path not in watcher._pending
        assert path not in watcher._retry_attempts
        assert repository.search_files("large-copy")[1] == 1
    finally:
        watcher.stop()


def test_watcher_backoff_does_not_block_other_files(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    slow, ready = root / "slow.bin", root / "ready.txt"
    slow.write_bytes(b"writing")
    ready.write_text("ready")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    watcher = FolderWatcher(repository, ExtensionClassifier(), lambda: None, retry_delays=(30.0,))
    first_attempt = threading.Event()

    def stability(path: Path) -> bool:
        if path == slow:
            first_attempt.set()
            return False
        return True

    monkeypatch.setattr(watcher, "_wait_stable", stability)
    try:
        watcher.start()
        watcher._enqueue(folder, slow)
        assert first_attempt.wait(2)
        watcher._enqueue(folder, ready)
        _wait_until(lambda: repository.search_files("ready")[1] == 1)
        assert slow in watcher._pending
        assert repository.search_files("slow")[1] == 0
    finally:
        watcher.stop()
    assert not watcher._retry_worker.is_alive()
    assert not watcher._pending


def test_watcher_retry_limit_cleans_pending_state(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "never-stable.bin"
    path.write_bytes(b"writing")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    watcher = FolderWatcher(repository, ExtensionClassifier(), lambda: None, retry_delays=(0.01, 0.01))
    calls = 0

    def unstable(_path: Path) -> bool:
        nonlocal calls
        calls += 1
        return False

    monkeypatch.setattr(watcher, "_wait_stable", unstable)
    try:
        watcher.start()
        watcher._enqueue(folder, path)
        _wait_until(lambda: calls == 3 and path not in watcher._pending)
        time.sleep(0.05)
        assert calls == 3
        assert not watcher._retry_heap
    finally:
        watcher.stop()


def test_watcher_retries_slow_external_rename_without_duplicate(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    old, new = root / "old.txt", root / "new.txt"
    old.write_text("content")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    indexed = repository.upsert_file(folder.id, old, old.stat().st_size, "Documentos", "2026-01-01")
    old.rename(new)
    watcher = FolderWatcher(repository, ExtensionClassifier(), lambda: None, retry_delays=(0.01,))
    results = iter((False, True))
    monkeypatch.setattr(watcher, "_wait_stable", lambda _path: next(results))
    try:
        watcher.start()
        watcher._enqueue_move(folder, old, new)
        _wait_until(lambda: repository.get_file(indexed.id).path == new)
        _wait_until(lambda: not watcher._pending_moves)
        assert len(repository.list_files()) == 1
    finally:
        watcher.stop()


def test_sqlite_wal_pragmas_and_concurrent_writers(tmp_path: Path) -> None:
    database = tmp_path / "concurrent.sqlite3"
    repository = Repository(database)
    assert repository.journal_mode == "wal"
    with connect(database) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert db.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 10_000

    def write(index: int) -> None:
        repository.set_setting(f"concurrent-{index}", str(index))

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(80)))
    assert all(repository.get_setting(f"concurrent-{index}") == str(index) for index in range(80))


def test_existing_database_enables_wal_without_losing_data(tmp_path: Path) -> None:
    database = tmp_path / "existing.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE legacy_marker(value TEXT NOT NULL)")
        db.execute("INSERT INTO legacy_marker VALUES ('preserved')")
        assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
    repository = Repository(database)
    assert repository.journal_mode == "wal"
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT value FROM legacy_marker").fetchone()[0] == "preserved"


def test_runtime_paths_use_localappdata_and_frozen_executable(tmp_path: Path, monkeypatch) -> None:
    local = tmp_path / "LocalAppData"
    assert runtime.data_directory({"LOCALAPPDATA": str(local)}) == local / "OrdenIA"
    monkeypatch.setattr(runtime.sys, "frozen", True, raising=False)
    monkeypatch.setattr(runtime.sys, "executable", str(tmp_path / "install" / "OrdenIA.exe"))
    assert runtime.is_frozen()
    assert runtime.application_directory() == (tmp_path / "install").resolve()


def test_packaging_configuration_tracks_version_and_optional_icon() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = (root / "packaging" / "ordenia.spec").read_text(encoding="utf-8")
    script = (root / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")
    installer = (root / "packaging" / "installer" / "ordenia.iss").read_text(encoding="utf-8")
    assert "ordenia.ico" in spec and "is_file()" in spec
    assert "from ordenia import __version__" in script
    assert "OrdenIA-Setup-{#MyAppVersion}" in installer
    assert "%LOCALAPPDATA%" not in installer  # uninstaller never removes user data

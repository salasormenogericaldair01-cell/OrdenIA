"""Runtime verification used by the packaged Windows build."""

import json
import importlib
import sqlite3
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ordenia import __version__
from ordenia.database.repositories import Repository
from ordenia.platform.runtime import is_frozen
from ordenia.ui.main_window import MainWindow


def prepare_legacy_database(database: Path, data_dir: Path) -> None:
    """Create a tiny V0.2 fixture only when the isolated smoke DB is absent."""
    if database.exists():
        return
    watched = data_dir / "smoke-watched"
    watched.mkdir(parents=True, exist_ok=True)
    sample = watched / "migration.txt"
    sample.write_text("OrdenIA packaging smoke test", encoding="utf-8")
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
        db.execute("INSERT INTO watched_folders VALUES (1, ?, 1, 0, 1, 'inside', NULL, '2026-01-01')", (str(watched),))
        db.execute("INSERT INTO files VALUES (1, 1, ?, ?, ?, ?, '.txt', ?, 'Documentos', '2026-01-01', '2026-01-01', 'Pendiente', 'active')",
                   (str(sample), str(sample).casefold(), str(watched), sample.name, sample.stat().st_size))
        db.execute("INSERT INTO settings VALUES ('smoke_migration_marker', 'v0.2')")


def run_smoke(app: QApplication, window: MainWindow, repository: Repository,
              data_dir: Path, report_path: Path) -> int:
    result = {"ok": False, "version": __version__, "frozen": is_frozen(),
              "data_directory": str(data_dir), "database": str(repository.db_path)}
    exit_code = 0

    def verify() -> None:
        nonlocal exit_code
        try:
            dependencies = ("PySide6", "pymupdf", "watchdog", "docx", "openpyxl",
                            "pptx", "lxml.etree", "sqlite3")
            result["dependencies"] = {
                name: bool(importlib.import_module(name)) for name in dependencies
            }
            with sqlite3.connect(repository.db_path) as db:
                result["fts5_compile_option"] = bool(db.execute(
                    "SELECT sqlite_compileoption_used('ENABLE_FTS5')").fetchone()[0])
                result["journal_mode"] = str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            result["fts5_repository"] = repository.content.fts_enabled
            result["migration_preserved"] = repository.get_setting("smoke_migration_marker") == "v0.2"
            with sqlite3.connect(repository.db_path) as db:
                result["migration_schema"] = bool(db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='file_analysis'").fetchone())
                result["ai_migration_schema"] = all(db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone() for table in ("ai_suggestions", "ai_feedback", "ai_settings"))
            result["ai_optional_without_ollama"] = repository.ai.get_setting("provider") == "ollama"
            result["window_visible"] = window.isVisible()
            result["ok"] = bool(result["window_visible"] and result["journal_mode"] == "wal"
                                and result["migration_preserved"] and result["migration_schema"]
                                and result["ai_migration_schema"] and result["ai_optional_without_ollama"]
                                and all(result["dependencies"].values()))
            if not result["ok"]:
                exit_code = 2
        except Exception as exc:  # report failures from a windowed executable
            result["error"] = f"{type(exc).__name__}: {exc}"
            exit_code = 2
        finally:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            window.close()
            app.quit()

    QTimer.singleShot(500, verify)
    app.exec()
    return exit_code

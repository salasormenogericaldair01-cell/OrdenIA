"""Consultas y transacciones de OrdenIA."""

import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path

from ordenia.core.organizer import verify_transfer

from .connection import connect
from .models import DetectedFile, Operation, WatchedFolder

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _folder(row: sqlite3.Row) -> WatchedFolder:
    return WatchedFolder(row["id"], Path(row["path"]), bool(row["enabled"]))


def _file(row: sqlite3.Row) -> DetectedFile:
    return DetectedFile(row["id"], row["watched_folder_id"], Path(row["path"]), Path(row["source_directory"]), row["name"], row["extension"], row["size"], row["category"], row["detected_at"], row["modified_at"], row["status"])


def _operation(row: sqlite3.Row) -> Operation:
    return Operation(row["id"], row["file_id"], Path(row["original_path"]), Path(row["destination_path"]), row["created_at"], row["operation_type"], row["status"], row["error_message"], row["undone_at"], Path(row["restored_path"]) if row["restored_path"] else None)


class Repository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._initialize()

    def _initialize(self) -> None:
        with connect(self.db_path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS watched_folders (
                    id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                    removed INTEGER NOT NULL DEFAULT 0 CHECK(removed IN (0, 1)),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY,
                    watched_folder_id INTEGER NOT NULL REFERENCES watched_folders(id),
                    path TEXT NOT NULL UNIQUE,
                    source_directory TEXT NOT NULL,
                    name TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    modified_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'Pendiente'
                        CHECK(status IN ('Pendiente', 'Organizado', 'Ignorado'))
                );
                CREATE INDEX IF NOT EXISTS idx_files_detected ON files(detected_at DESC);
                CREATE TABLE IF NOT EXISTS operations (
                    id INTEGER PRIMARY KEY,
                    file_id INTEGER NOT NULL REFERENCES files(id),
                    original_path TEXT NOT NULL,
                    destination_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    operation_type TEXT NOT NULL CHECK(operation_type IN ('move', 'undo')),
                    status TEXT NOT NULL DEFAULT 'Completado' CHECK(status IN ('Completado', 'Deshecho', 'Fallido')),
                    error_message TEXT,
                    undone_at TEXT,
                    restored_path TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_operations_created ON operations(created_at DESC);
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
            db.execute("BEGIN IMMEDIATE")
            folder_columns = {row["name"] for row in db.execute("PRAGMA table_info(watched_folders)")}
            if "removed" not in folder_columns:
                db.execute("ALTER TABLE watched_folders ADD COLUMN removed INTEGER NOT NULL DEFAULT 0")
            file_columns = {row["name"] for row in db.execute("PRAGMA table_info(files)")}
            if "source_directory" not in file_columns:
                db.execute("ALTER TABLE files ADD COLUMN source_directory TEXT NOT NULL DEFAULT ''")
                for row in db.execute("SELECT id, path FROM files"):
                    db.execute("UPDATE files SET source_directory = ? WHERE id = ?", (str(Path(row["path"]).parent), row["id"]))
            operation_columns = {row["name"] for row in db.execute("PRAGMA table_info(operations)")}
            if "status" not in operation_columns:
                db.execute("ALTER TABLE operations ADD COLUMN status TEXT NOT NULL DEFAULT 'Completado' CHECK(status IN ('Completado', 'Deshecho', 'Fallido'))")
                for row in db.execute("SELECT * FROM operations ORDER BY id"):
                    if row["undone_at"] is not None and row["operation_type"] == "move":
                        db.execute("UPDATE operations SET status = 'Deshecho' WHERE id = ?", (row["id"],))
                    else:
                        original = Path(row["original_path"])
                        destination = Path(row["destination_path"])
                        try:
                            verify_transfer(original, destination)
                        except OSError:
                            db.execute("UPDATE operations SET status = 'Fallido' WHERE id = ?", (row["id"],))
                            if row["operation_type"] == "move":
                                db.execute("UPDATE files SET path = ?, name = ?, status = 'Pendiente' WHERE id = ? AND path = ? AND status = 'Organizado'", (str(original), original.name, row["file_id"], str(destination)))
                            logger.warning("Operación antigua no verificable: %s", row["id"])
            if "error_message" not in operation_columns:
                db.execute("ALTER TABLE operations ADD COLUMN error_message TEXT")
                db.execute("UPDATE operations SET error_message = 'No se pudo verificar el movimiento registrado por la versión anterior.' WHERE status = 'Fallido' AND error_message IS NULL")

    def list_folders(self) -> list[WatchedFolder]:
        with connect(self.db_path) as db:
            return [_folder(row) for row in db.execute("SELECT * FROM watched_folders WHERE removed = 0 ORDER BY path COLLATE NOCASE")]

    def add_folder(self, path: Path) -> WatchedFolder:
        normalized = path.resolve()
        if not normalized.is_dir():
            raise NotADirectoryError(normalized)
        with connect(self.db_path) as db:
            db.execute("INSERT INTO watched_folders(path, enabled, removed, created_at) VALUES (?, 1, 0, ?) ON CONFLICT(path) DO UPDATE SET enabled = 1, removed = 0", (str(normalized), _now()))
            row = db.execute("SELECT * FROM watched_folders WHERE path = ?", (str(normalized),)).fetchone()
            assert row is not None
            return _folder(row)

    def set_folder_enabled(self, folder_id: int, enabled: bool) -> None:
        if enabled and not self.get_folder(folder_id).path.is_dir():
            raise NotADirectoryError("La carpeta ya no está disponible.")
        with connect(self.db_path) as db:
            db.execute("UPDATE watched_folders SET enabled = ? WHERE id = ? AND removed = 0", (int(enabled), folder_id))

    def remove_folder(self, folder_id: int) -> None:
        # Retain the row for the foreign keys and history, but hide it from the list.
        with connect(self.db_path) as db:
            db.execute("UPDATE watched_folders SET enabled = 0, removed = 1 WHERE id = ?", (folder_id,))

    def upsert_file(self, folder_id: int, path: Path, size: int, category: str, modified_at: str) -> DetectedFile:
        now = _now()
        with connect(self.db_path) as db:
            db.execute("""
                INSERT INTO files(watched_folder_id, path, source_directory, name, extension, size, category, detected_at, modified_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pendiente')
                ON CONFLICT(path) DO UPDATE SET
                    name = excluded.name, extension = excluded.extension, size = excluded.size,
                    category = excluded.category, modified_at = excluded.modified_at
            """, (folder_id, str(path), str(path.parent), path.name, path.suffix.lower(), size, category, now, modified_at))
            row = db.execute("SELECT * FROM files WHERE path = ?", (str(path),)).fetchone()
            assert row is not None
            return _file(row)

    def list_files(self) -> list[DetectedFile]:
        with connect(self.db_path) as db:
            return [_file(row) for row in db.execute("SELECT * FROM files ORDER BY detected_at DESC, id DESC")]

    def get_file(self, file_id: int) -> DetectedFile:
        with connect(self.db_path) as db:
            row = db.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            if row is None:
                raise LookupError("Archivo no encontrado.")
            return _file(row)

    def get_folder(self, folder_id: int) -> WatchedFolder:
        with connect(self.db_path) as db:
            row = db.execute("SELECT * FROM watched_folders WHERE id = ?", (folder_id,)).fetchone()
            if row is None:
                raise LookupError("Carpeta no encontrada.")
            return _folder(row)

    def set_file_status(self, file_id: int, status: str) -> None:
        if status not in {"Pendiente", "Ignorado"}:
            raise ValueError(status)
        with connect(self.db_path) as db:
            db.execute("UPDATE files SET status = ? WHERE id = ?", (status, file_id))

    def record_move(self, file_id: int, original: Path, destination: Path) -> None:
        verify_transfer(original, destination)
        with connect(self.db_path) as db:
            cursor = db.execute("UPDATE files SET path = ?, name = ?, status = 'Organizado' WHERE id = ? AND path = ? AND status = 'Pendiente'", (str(destination), destination.name, file_id, str(original)))
            if cursor.rowcount != 1:
                raise ValueError("El archivo ya no está pendiente en la ruta original.")
            db.execute("INSERT INTO operations(file_id, original_path, destination_path, created_at, operation_type, status) VALUES (?, ?, ?, ?, 'move', 'Completado')", (file_id, str(original), str(destination), _now()))

    def record_failed_move(self, file_id: int, original: Path, destination: Path, message: str) -> None:
        with connect(self.db_path) as db:
            db.execute("INSERT INTO operations(file_id, original_path, destination_path, created_at, operation_type, status, error_message) VALUES (?, ?, ?, ?, 'move', 'Fallido', ?)", (file_id, str(original), str(destination), _now(), message))

    def repair_pending_path(self, file_id: int, restored: Path) -> None:
        with connect(self.db_path) as db:
            db.execute("UPDATE files SET path = ?, name = ? WHERE id = ? AND status = 'Pendiente'", (str(restored), restored.name, file_id))

    def record_undo(self, operation: Operation, restored: Path) -> None:
        verify_transfer(operation.destination_path, restored)
        now = _now()
        with connect(self.db_path) as db:
            cursor = db.execute("UPDATE operations SET status = 'Deshecho', undone_at = ?, restored_path = ? WHERE id = ? AND status = 'Completado' AND undone_at IS NULL", (now, str(restored), operation.id))
            if cursor.rowcount != 1:
                raise ValueError("Esta operación ya fue deshecha.")
            updated = db.execute("UPDATE files SET path = ?, name = ?, status = 'Pendiente' WHERE id = ? AND path = ? AND status = 'Organizado'", (str(restored), restored.name, operation.file_id, str(operation.destination_path)))
            if updated.rowcount != 1:
                raise ValueError("El archivo ya no coincide con el movimiento registrado.")
            db.execute("INSERT INTO operations(file_id, original_path, destination_path, created_at, operation_type, status) VALUES (?, ?, ?, ?, 'undo', 'Completado')", (operation.file_id, str(operation.destination_path), str(restored), now))

    def record_failed_undo(self, operation: Operation, message: str) -> None:
        with connect(self.db_path) as db:
            db.execute("INSERT INTO operations(file_id, original_path, destination_path, created_at, operation_type, status, error_message) VALUES (?, ?, ?, ?, 'undo', 'Fallido', ?)", (operation.file_id, str(operation.destination_path), str(operation.original_path), _now(), message))

    def get_operation(self, operation_id: int) -> Operation:
        with connect(self.db_path) as db:
            row = db.execute("SELECT * FROM operations WHERE id = ?", (operation_id,)).fetchone()
            if row is None:
                raise LookupError("Operación no encontrada.")
            return _operation(row)

    def list_operations(self) -> list[Operation]:
        with connect(self.db_path) as db:
            return [_operation(row) for row in db.execute("SELECT * FROM operations ORDER BY id DESC")]

    def get_setting(self, key: str, default: str = "") -> str:
        with connect(self.db_path) as db:
            row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with connect(self.db_path) as db:
            db.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

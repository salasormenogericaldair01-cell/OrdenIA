"""Consultas y transacciones de OrdenIA."""

import sqlite3
import logging
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from ordenia.core.file_utils import path_key
from ordenia.core.exclusions import ExclusionPolicy
from ordenia.core.organizer import verify_transfer

from .connection import connect
from .content import ContentRepository
from .models import DetectedFile, IndexedEntry, Operation, WatchedFolder

logger = logging.getLogger(__name__)

_SORT_COLUMNS = {
    "name": "name COLLATE NOCASE", "category": "category COLLATE NOCASE",
    "extension": "extension COLLATE NOCASE", "size": "size",
    "source_directory": "source_directory COLLATE NOCASE",
    "detected_at": "detected_at", "status": "status COLLATE NOCASE",
    "modified_at": "modified_at",
}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _folder(row: sqlite3.Row) -> WatchedFolder:
    return WatchedFolder(row["id"], Path(row["path"]), bool(row["enabled"]), bool(row["include_subfolders"]), row["destination_strategy"], Path(row["custom_destination"]) if row["custom_destination"] else None)


def _file(row: sqlite3.Row) -> DetectedFile:
    return DetectedFile(row["id"], row["watched_folder_id"], Path(row["path"]), row["path_key"], Path(row["source_directory"]), row["name"], row["extension"], row["size"], row["category"], row["detected_at"], row["modified_at"], row["status"], row["index_state"], row["mtime_ns"])


def _operation(row: sqlite3.Row) -> Operation:
    return Operation(row["id"], row["file_id"], Path(row["original_path"]), Path(row["destination_path"]), row["created_at"], row["operation_type"], row["status"], row["error_message"], row["undone_at"], Path(row["restored_path"]) if row["restored_path"] else None)


_UPSERT_FILE = """
    INSERT INTO files(watched_folder_id, path, path_key, source_directory, name, extension,
                      size, category, detected_at, modified_at, mtime_ns, status)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pendiente')
    ON CONFLICT(path_key) DO UPDATE SET
        path = excluded.path, source_directory = excluded.source_directory,
        name = excluded.name, extension = excluded.extension,
        size = excluded.size, category = excluded.category, modified_at = excluded.modified_at,
        mtime_ns = excluded.mtime_ns,
        index_state = 'active'
"""


def _file_values(folder_id: int, entry: IndexedEntry, detected_at: str) -> tuple[object, ...]:
    path = Path(os.path.abspath(entry.path.expanduser()))
    mtime_ns = entry.mtime_ns
    if not mtime_ns:
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            pass
    return (folder_id, str(path), path_key(path), str(path.parent), path.name, path.suffix.lower(), entry.size, entry.category, detected_at, entry.modified_at, mtime_ns)


class Repository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.content = ContentRepository(db_path)
        self._initialize()

    def _initialize(self) -> None:
        with connect(self.db_path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS watched_folders (
                    id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                    removed INTEGER NOT NULL DEFAULT 0 CHECK(removed IN (0, 1)),
                    include_subfolders INTEGER NOT NULL DEFAULT 1 CHECK(include_subfolders IN (0, 1)),
                    destination_strategy TEXT NOT NULL DEFAULT 'inside',
                    custom_destination TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY,
                    watched_folder_id INTEGER NOT NULL REFERENCES watched_folders(id),
                    path TEXT NOT NULL UNIQUE,
                    path_key TEXT NOT NULL UNIQUE,
                    source_directory TEXT NOT NULL,
                    name TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    modified_at TEXT NOT NULL,
                    mtime_ns INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'Pendiente'
                        CHECK(status IN ('Pendiente', 'Organizado', 'Ignorado')),
                    index_state TEXT NOT NULL DEFAULT 'active'
                        CHECK(index_state IN ('active', 'missing', 'excluded'))
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
                CREATE TABLE IF NOT EXISTS managed_destinations (
                    path TEXT PRIMARY KEY,
                    added_at TEXT NOT NULL
                );
            """)
            db.execute("BEGIN IMMEDIATE")
            folder_columns = {row["name"] for row in db.execute("PRAGMA table_info(watched_folders)")}
            if "removed" not in folder_columns:
                db.execute("ALTER TABLE watched_folders ADD COLUMN removed INTEGER NOT NULL DEFAULT 0")
            if "include_subfolders" not in folder_columns:
                db.execute("ALTER TABLE watched_folders ADD COLUMN include_subfolders INTEGER NOT NULL DEFAULT 1")
            if "destination_strategy" not in folder_columns:
                db.execute("ALTER TABLE watched_folders ADD COLUMN destination_strategy TEXT NOT NULL DEFAULT 'inside'")
            if "custom_destination" not in folder_columns:
                db.execute("ALTER TABLE watched_folders ADD COLUMN custom_destination TEXT")
            file_columns = {row["name"] for row in db.execute("PRAGMA table_info(files)")}
            if "mtime_ns" not in file_columns:
                db.execute("ALTER TABLE files ADD COLUMN mtime_ns INTEGER NOT NULL DEFAULT 0")
            if "index_state" not in file_columns:
                db.execute("ALTER TABLE files ADD COLUMN index_state TEXT NOT NULL DEFAULT 'active' CHECK(index_state IN ('active', 'missing', 'excluded'))")
            if "source_directory" not in file_columns:
                db.execute("ALTER TABLE files ADD COLUMN source_directory TEXT NOT NULL DEFAULT ''")
                for row in db.execute("SELECT id, path FROM files"):
                    db.execute("UPDATE files SET source_directory = ? WHERE id = ?", (str(Path(row["path"]).parent), row["id"]))
            if "path_key" not in file_columns:
                db.execute("ALTER TABLE files ADD COLUMN path_key TEXT")
                known_keys: set[str] = set()
                for row in db.execute("SELECT id, path FROM files"):
                    key = path_key(Path(row["path"]))
                    if key in known_keys:
                        logger.warning("Ruta duplicada en la base antigua: archivo %s", row["id"])
                        key = f"{key}#legacy-{row['id']}"
                    known_keys.add(key)
                    db.execute("UPDATE files SET path_key = ? WHERE id = ?", (key, row["id"]))
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_files_path_key ON files(path_key)")
            operation_columns = {row["name"] for row in db.execute("PRAGMA table_info(operations)")}
            if "status" not in operation_columns:
                db.execute("ALTER TABLE operations ADD COLUMN status TEXT NOT NULL DEFAULT 'Completado' CHECK(status IN ('Completado', 'Deshecho', 'Fallido'))")
                # Legacy operations describe the event when it was recorded.
                # Current disk availability is classified during reconciliation.
                db.execute("UPDATE operations SET status = 'Deshecho' WHERE undone_at IS NOT NULL AND operation_type = 'move'")
            if "error_message" not in operation_columns:
                db.execute("ALTER TABLE operations ADD COLUMN error_message TEXT")

            db.execute("CREATE INDEX IF NOT EXISTS idx_files_index_state_folder ON files(index_state, watched_folder_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_operations_file_id ON operations(file_id)")
            self.content.initialize(db)

    def list_folders(self) -> list[WatchedFolder]:
        with connect(self.db_path) as db:
            return [_folder(row) for row in db.execute("SELECT * FROM watched_folders WHERE removed = 0 ORDER BY path COLLATE NOCASE")]

    def is_folder_listed(self, folder_id: int) -> bool:
        with connect(self.db_path) as db:
            return db.execute("SELECT 1 FROM watched_folders WHERE id = ? AND removed = 0", (folder_id,)).fetchone() is not None

    def add_folder(
        self, path: Path, include_subfolders: bool = True,
        destination_strategy: str = "inside", custom_destination: Path | None = None,
    ) -> WatchedFolder:
        normalized = path.resolve()
        if not normalized.is_dir():
            raise NotADirectoryError(normalized)
        with connect(self.db_path) as db:
            db.execute("""INSERT INTO watched_folders(path, enabled, removed, include_subfolders, destination_strategy, custom_destination, created_at)
                VALUES (?, 1, 0, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET
                enabled = 1, removed = 0, include_subfolders = excluded.include_subfolders,
                destination_strategy = excluded.destination_strategy, custom_destination = excluded.custom_destination""",
                (str(normalized), int(include_subfolders), destination_strategy, str(custom_destination.resolve()) if custom_destination else None, _now()))
            row = db.execute("SELECT * FROM watched_folders WHERE path = ?", (str(normalized),)).fetchone()
            assert row is not None
            return _folder(row)

    def update_folder(
        self, folder_id: int, include_subfolders: bool,
        destination_strategy: str, custom_destination: Path | None,
    ) -> WatchedFolder:
        with connect(self.db_path) as db:
            cursor = db.execute("""UPDATE watched_folders SET include_subfolders = ?, destination_strategy = ?, custom_destination = ?
                WHERE id = ? AND removed = 0""", (int(include_subfolders), destination_strategy, str(custom_destination.resolve()) if custom_destination else None, folder_id))
            if cursor.rowcount != 1:
                raise LookupError("Carpeta no encontrada.")
            row = db.execute("SELECT * FROM watched_folders WHERE id = ?", (folder_id,)).fetchone()
            assert row is not None
            return _folder(row)

    def register_managed_root(self, path: Path) -> None:
        with connect(self.db_path) as db:
            db.execute("INSERT OR IGNORE INTO managed_destinations(path, added_at) VALUES (?, ?)", (str(path.resolve()), _now()))

    def list_managed_roots(self) -> tuple[Path, ...]:
        with connect(self.db_path) as db:
            return tuple(Path(row["path"]) for row in db.execute("SELECT path FROM managed_destinations"))

    def set_folder_enabled(self, folder_id: int, enabled: bool) -> None:
        if enabled and not self.get_folder(folder_id).path.is_dir():
            raise NotADirectoryError("La carpeta ya no está disponible.")
        with connect(self.db_path) as db:
            db.execute("UPDATE watched_folders SET enabled = ? WHERE id = ? AND removed = 0", (int(enabled), folder_id))

    def remove_folder(self, folder_id: int) -> None:
        # Retain the row for the foreign keys and history, but hide it from the list.
        with connect(self.db_path) as db:
            db.execute("UPDATE watched_folders SET enabled = 0, removed = 1 WHERE id = ?", (folder_id,))

    def upsert_file(self, folder_id: int, path: Path, size: int, category: str, modified_at: str, mtime_ns: int = 0) -> DetectedFile:
        now = _now()
        entry = IndexedEntry(path, size, category, modified_at, mtime_ns)
        with connect(self.db_path) as db:
            db.execute(_UPSERT_FILE, _file_values(folder_id, entry, now))
            row = db.execute("SELECT * FROM files WHERE path_key = ?", (path_key(path),)).fetchone()
            assert row is not None
            return _file(row)

    def upsert_files_batch(self, folder_id: int, entries: list[IndexedEntry]) -> tuple[int, int]:
        if not entries:
            return (0, 0)
        now = _now()
        with connect(self.db_path) as db:
            keys = [path_key(entry.path) for entry in entries]
            existing = sum(db.execute("SELECT 1 FROM files WHERE path_key = ?", (key,)).fetchone() is not None for key in keys)
            db.executemany(_UPSERT_FILE, (_file_values(folder_id, entry, now) for entry in entries))
            return (len(entries) - existing, existing)

    def ids_for_paths(self, paths: list[Path]) -> list[int]:
        if not paths:
            return []
        keys = [path_key(path) for path in paths]
        placeholders = ",".join("?" for _ in keys)
        with connect(self.db_path) as db:
            return [row[0] for row in db.execute(f"SELECT id FROM files WHERE path_key IN ({placeholders}) AND index_state = 'active'", keys)]

    def list_files(self) -> list[DetectedFile]:
        with connect(self.db_path) as db:
            return [_file(row) for row in db.execute("SELECT * FROM files ORDER BY detected_at DESC, id DESC")]

    def reconcile_folder(self, folder: WatchedFolder, managed_roots: tuple[Path, ...], *, check_missing: bool = True) -> dict[str, int]:
        """Retain historical rows while classifying their current index state."""
        policy = ExclusionPolicy()
        with connect(self.db_path) as db:
            rows = db.execute("""SELECT f.id, f.path, f.path_key, f.status, f.index_state,
                EXISTS(SELECT 1 FROM operations o WHERE o.file_id = f.id AND o.operation_type = 'move'
                    AND o.status = 'Completado' AND o.destination_path = f.path) AS verified_move
                FROM files f WHERE f.watched_folder_id = ?""", (folder.id,)).fetchall()
            changes: list[tuple[str, int]] = []
            counts = {"active": 0, "missing": 0, "excluded": 0}
            canonical_keys = {row["path_key"] for row in rows}
            for row in rows:
                path = Path(row["path"])
                verified_organized = row["status"] == "Organizado" and bool(row["verified_move"])
                legacy_key = row["path_key"]
                duplicate_legacy = (legacy_key != path_key(path) and legacy_key.endswith(f"#legacy-{row['id']}")
                                    and legacy_key.rsplit("#legacy-", 1)[0] in canonical_keys)
                if duplicate_legacy or policy.excluded_name(path) or path.is_symlink():
                    state = "excluded"
                elif not verified_organized and not policy.eligible_path(
                    path, folder.path, folder.include_subfolders, managed_roots, assume_regular=True
                ):
                    state = "excluded" if path == folder.path or folder.path in path.parents else "missing"
                elif check_missing and not path.is_file():
                    state = "missing"
                else:
                    state = "active" if check_missing or row["index_state"] != "missing" else "missing"
                counts[state] += 1
                if state != row["index_state"]:
                    changes.append((state, row["id"]))
            db.executemany("UPDATE files SET index_state = ? WHERE id = ?", changes)
            counts["changed"] = len(changes)
            return counts

    def reconcile_external_move(self, folder: WatchedFolder, source: Path, destination: Path,
                                managed_roots: tuple[Path, ...], entry: IndexedEntry | None) -> bool:
        """Follow an external rename using the same row when history permits it."""
        with connect(self.db_path) as db:
            row = db.execute("SELECT * FROM files WHERE path_key = ?", (path_key(source),)).fetchone()
            if row is None:
                return False
            if row["status"] == "Organizado" and db.execute(
                "SELECT 1 FROM operations WHERE file_id = ? AND status = 'Completado' AND operation_type = 'move'", (row["id"],)
            ).fetchone():
                db.execute("UPDATE files SET index_state = 'missing' WHERE id = ?", (row["id"],))
                return True
            eligible_path = ExclusionPolicy().eligible_path(
                destination, folder.path, folder.include_subfolders, managed_roots, assume_regular=True)
            state = "active" if eligible_path and entry is not None else "missing" if eligible_path else "excluded" if folder.path in destination.parents else "missing"
            if db.execute("SELECT 1 FROM files WHERE path_key = ? AND id != ?", (path_key(destination), row["id"])).fetchone():
                db.execute("UPDATE files SET index_state = 'missing' WHERE id = ?", (row["id"],))
                return True
            if state == "missing":
                db.execute("UPDATE files SET index_state = 'missing' WHERE id = ?", (row["id"],))
                return True
            db.execute("""UPDATE files SET path = ?, path_key = ?, source_directory = ?, name = ?, extension = ?,
                size = ?, category = ?, modified_at = ?, mtime_ns = ?, index_state = ? WHERE id = ?""",
                (str(destination), path_key(destination), str(destination.parent), destination.name, destination.suffix.lower(),
                 entry.size if entry else row["size"], entry.category if entry else row["category"],
                 entry.modified_at if entry else row["modified_at"], entry.mtime_ns if entry else row["mtime_ns"], state, row["id"]))
            return True

    def _filter_clause(self, search: str, status: str, category: str,
                       search_name: bool = True, search_content: bool = False) -> tuple[str, list[str]]:
        clauses: list[str] = ["index_state = 'active'"]
        values: list[str] = []
        if search.strip():
            term = search.strip().casefold()
            search_parts: list[str] = []
            if search_name:
                search_parts.append("(INSTR(CASEFOLD(name), ?) > 0 OR INSTR(CASEFOLD(extension), ?) > 0 OR INSTR(CASEFOLD(category), ?) > 0 OR INSTR(CASEFOLD(path), ?) > 0 OR INSTR(CASEFOLD(source_directory), ?) > 0)")
                values.extend([term] * 5)
            if search_content:
                content_clause = self.content.search_clause(search)
                if content_clause:
                    search_parts.append(content_clause[0])
                    values.extend(content_clause[1])
            clauses.append("(" + " OR ".join(search_parts) + ")" if search_parts else "0")
        if status != "Todos":
            clauses.append("status = ?")
            values.append(status)
        if category != "Todas":
            clauses.append("category = ?")
            values.append(category)
        return (" WHERE " + " AND ".join(clauses) if clauses else "", values)

    def search_files(
        self, search: str = "", status: str = "Todos", category: str = "Todas",
        sort_by: str = "detected_at", descending: bool = True,
        limit: int = 200, offset: int = 0,
        search_name: bool = True, search_content: bool = False,
    ) -> tuple[list[DetectedFile], int]:
        where, values = self._filter_clause(search, status, category, search_name, search_content)
        column = _SORT_COLUMNS.get(sort_by, _SORT_COLUMNS["detected_at"])
        direction = "DESC" if descending else "ASC"
        with connect(self.db_path) as db:
            total = db.execute("SELECT COUNT(*) FROM files" + where, values).fetchone()[0]
            rows = db.execute(f"SELECT * FROM files{where} ORDER BY {column} {direction}, id DESC LIMIT ? OFFSET ?", (*values, max(1, limit), max(0, offset)))
            files = [_file(row) for row in rows]
        if search_content and search.strip():
            snippets = self.content.snippets([file.id for file in files], search)
            files = [replace(file, match_snippet=snippets.get(file.id, "")) for file in files]
        return files, total

    def matching_file_ids(self, search: str = "", status: str = "Todos", category: str = "Todas",
                          search_name: bool = True, search_content: bool = False) -> list[int]:
        where, values = self._filter_clause(search, status, category, search_name, search_content)
        with connect(self.db_path) as db:
            return [row[0] for row in db.execute("SELECT id FROM files" + where + " ORDER BY id", values)]

    def refresh_fingerprint(self, file_id: int, path: Path, size: int, mtime_ns: int, modified_at: str) -> bool:
        with connect(self.db_path) as db:
            updated = db.execute("""UPDATE files SET size = ?, mtime_ns = ?, modified_at = ?
                WHERE id = ? AND path = ? AND index_state = 'active'""",
                (size, mtime_ns, modified_at, file_id, str(path)))
            return updated.rowcount == 1

    def count_by_category(self) -> dict[str, int]:
        with connect(self.db_path) as db:
            return {row["category"]: row["count"] for row in db.execute("SELECT category, COUNT(*) AS count FROM files WHERE index_state = 'active' GROUP BY category")}

    def dashboard_counts(self) -> dict[str, int]:
        today = datetime.now().astimezone().date().isoformat()
        with connect(self.db_path) as db:
            row = db.execute("""SELECT COUNT(*) AS total,
                SUM(CASE WHEN detected_at LIKE ? THEN 1 ELSE 0 END) AS today,
                SUM(CASE WHEN status = 'Pendiente' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status = 'Organizado' THEN 1 ELSE 0 END) AS organized
                FROM files WHERE index_state = 'active'""", (today + "%",)).fetchone()
            return {"total": row["total"], "today": row["today"] or 0, "pending": row["pending"] or 0, "organized": row["organized"] or 0}

    def recent_files(self, limit: int = 6) -> list[DetectedFile]:
        with connect(self.db_path) as db:
            return [_file(row) for row in db.execute("SELECT * FROM files WHERE index_state = 'active' ORDER BY detected_at DESC, id DESC LIMIT ?", (limit,))]

    def registered_counts(self) -> dict[int, int]:
        with connect(self.db_path) as db:
            return {row["watched_folder_id"]: row["count"] for row in db.execute("SELECT watched_folder_id, COUNT(*) AS count FROM files WHERE index_state = 'active' GROUP BY watched_folder_id")}

    def index_counts(self, folder_id: int) -> dict[str, int]:
        with connect(self.db_path) as db:
            return {row["index_state"]: row["count"] for row in db.execute(
                "SELECT index_state, COUNT(*) AS count FROM files WHERE watched_folder_id = ? GROUP BY index_state", (folder_id,))}

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
            cursor = db.execute("UPDATE files SET path = ?, path_key = ?, name = ?, status = 'Organizado', index_state = 'active' WHERE id = ? AND path = ? AND status = 'Pendiente' AND index_state = 'active'", (str(destination), path_key(destination), destination.name, file_id, str(original)))
            if cursor.rowcount != 1:
                raise ValueError("El archivo ya no está pendiente en la ruta original.")
            db.execute("INSERT INTO operations(file_id, original_path, destination_path, created_at, operation_type, status) VALUES (?, ?, ?, ?, 'move', 'Completado')", (file_id, str(original), str(destination), _now()))

    def record_failed_move(self, file_id: int, original: Path, destination: Path, message: str) -> None:
        with connect(self.db_path) as db:
            db.execute("INSERT INTO operations(file_id, original_path, destination_path, created_at, operation_type, status, error_message) VALUES (?, ?, ?, ?, 'move', 'Fallido', ?)", (file_id, str(original), str(destination), _now(), message))

    def repair_pending_path(self, file_id: int, restored: Path) -> None:
        with connect(self.db_path) as db:
            db.execute("UPDATE files SET path = ?, path_key = ?, name = ? WHERE id = ? AND status = 'Pendiente'", (str(restored), path_key(restored), restored.name, file_id))

    def record_undo(self, operation: Operation, restored: Path) -> None:
        verify_transfer(operation.destination_path, restored)
        now = _now()
        with connect(self.db_path) as db:
            cursor = db.execute("UPDATE operations SET status = 'Deshecho', undone_at = ?, restored_path = ? WHERE id = ? AND status = 'Completado' AND undone_at IS NULL", (now, str(restored), operation.id))
            if cursor.rowcount != 1:
                raise ValueError("Esta operación ya fue deshecha.")
            updated = db.execute("UPDATE files SET path = ?, path_key = ?, name = ?, status = 'Pendiente', index_state = 'active' WHERE id = ? AND path = ? AND status = 'Organizado'", (str(restored), path_key(restored), restored.name, operation.file_id, str(operation.destination_path)))
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

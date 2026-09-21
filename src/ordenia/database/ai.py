"""Persistence for local AI suggestions, feedback and settings."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ordenia.ai.models import AIRecord, AISuggestion, AI_STATUSES, DEFAULT_AI_MODEL
from ordenia.ai.parser import validate_relative_path

from .connection import connect


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _record(row: sqlite3.Row) -> AIRecord:
    return AIRecord(
        file_id=row["file_id"], status=row["status"], provider=row["provider"] or "",
        model=row["model"] or "", document_type=row["document_type"] or "",
        topic=row["topic"] or "", tags=tuple(json.loads(row["tags_json"] or "[]")),
        suggested_path=row["suggested_path"] or "", confidence=float(row["confidence"] or 0),
        reason=row["reason"] or "", created_at=row["created_at"] or "",
        fingerprint_size=int(row["fingerprint_size"] or 0),
        fingerprint_mtime_ns=int(row["fingerprint_mtime_ns"] or 0), error=row["error"] or "",
    )


class AIRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def initialize(self, db: sqlite3.Connection) -> None:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS ai_suggestions (
                file_id INTEGER PRIMARY KEY REFERENCES files(id),
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN
                    ('pending','analyzing','ready','failed','stale','unavailable')),
                provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
                document_type TEXT NOT NULL DEFAULT '', topic TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]', suggested_path TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
                created_at TEXT, fingerprint_size INTEGER NOT NULL DEFAULT 0,
                fingerprint_mtime_ns INTEGER NOT NULL DEFAULT 0, error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_ai_suggestions_status ON ai_suggestions(status);
            CREATE TABLE IF NOT EXISTS ai_feedback (
                id INTEGER PRIMARY KEY,
                file_id INTEGER NOT NULL REFERENCES files(id),
                document_type TEXT NOT NULL, topic TEXT NOT NULL,
                tags_json TEXT NOT NULL DEFAULT '[]', suggested_path TEXT NOT NULL,
                chosen_path TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_feedback_topic ON ai_feedback(topic COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_ai_feedback_type ON ai_feedback(document_type COLLATE NOCASE);
            CREATE TABLE IF NOT EXISTS ai_settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS files_ai_stale AFTER UPDATE OF size, mtime_ns ON files
            WHEN OLD.size != NEW.size OR OLD.mtime_ns != NEW.mtime_ns
            BEGIN
                UPDATE ai_suggestions SET status = 'stale', error = 'El archivo cambió; vuelve a analizarlo.'
                WHERE file_id = NEW.id AND status = 'ready';
            END;
            CREATE TRIGGER IF NOT EXISTS files_ai_inactive AFTER UPDATE OF index_state ON files
            WHEN OLD.index_state != NEW.index_state AND NEW.index_state != 'active'
            BEGIN
                UPDATE ai_suggestions SET status = 'stale', error = 'El archivo ya no está activo en el índice.'
                WHERE file_id = NEW.id AND status = 'ready';
            END;
        """)
        db.execute("UPDATE ai_suggestions SET status = 'pending', error = NULL WHERE status = 'analyzing'")
        db.execute("INSERT OR IGNORE INTO ai_settings(key, value) VALUES ('provider', 'ollama')")
        db.execute("INSERT OR IGNORE INTO ai_settings(key, value) VALUES ('model', ?)", (DEFAULT_AI_MODEL,))

    def get(self, file_id: int) -> AIRecord:
        with connect(self.db_path) as db:
            row = db.execute("SELECT * FROM ai_suggestions WHERE file_id = ?", (file_id,)).fetchone()
            return _record(row) if row else AIRecord(file_id)

    def set_status(self, file_id: int, status: str, *, provider: str = "", model: str = "",
                   error: str = "") -> None:
        if status not in AI_STATUSES:
            raise ValueError(status)
        with connect(self.db_path) as db:
            db.execute("""INSERT INTO ai_suggestions(file_id,status,provider,model,error)
                VALUES (?,?,?,?,?) ON CONFLICT(file_id) DO UPDATE SET
                status=excluded.status, provider=CASE WHEN excluded.provider='' THEN provider ELSE excluded.provider END,
                model=CASE WHEN excluded.model='' THEN model ELSE excluded.model END, error=excluded.error""",
                (file_id, status, provider, model, error or None))

    def save(self, file_id: int, suggestion: AISuggestion, provider: str, model: str,
             size: int, mtime_ns: int) -> str:
        safe_path = validate_relative_path(suggestion.suggested_path)
        now = _now()
        with connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            file = db.execute("SELECT size, mtime_ns, index_state FROM files WHERE id = ?", (file_id,)).fetchone()
            stale = file is None or file["index_state"] != "active" or (
                int(file["size"]), int(file["mtime_ns"])) != (size, mtime_ns)
            status = "stale" if stale else "ready"
            error = "El archivo cambió durante el análisis inteligente; vuelve a analizarlo." if stale else None
            db.execute("""INSERT INTO ai_suggestions(
                file_id,status,provider,model,document_type,topic,tags_json,suggested_path,
                confidence,reason,created_at,fingerprint_size,fingerprint_mtime_ns,error)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(file_id) DO UPDATE SET
                status=excluded.status,provider=excluded.provider,model=excluded.model,
                document_type=excluded.document_type,topic=excluded.topic,tags_json=excluded.tags_json,
                suggested_path=excluded.suggested_path,confidence=excluded.confidence,
                reason=excluded.reason,created_at=excluded.created_at,
                fingerprint_size=excluded.fingerprint_size,
                fingerprint_mtime_ns=excluded.fingerprint_mtime_ns,error=excluded.error""",
                (file_id, status, provider, model, suggestion.document_type, suggestion.topic,
                 json.dumps(suggestion.tags, ensure_ascii=False), safe_path,
                 suggestion.confidence, suggestion.reason, now, size, mtime_ns, error))
        return status

    def save_feedback(self, file_id: int, suggestion: AISuggestion, chosen_path: str) -> None:
        suggested_path = validate_relative_path(suggestion.suggested_path)
        chosen_path = validate_relative_path(chosen_path)
        with connect(self.db_path) as db:
            db.execute("""INSERT INTO ai_feedback(file_id,document_type,topic,tags_json,
                suggested_path,chosen_path,created_at) VALUES (?,?,?,?,?,?,?)""",
                (file_id, suggestion.document_type, suggestion.topic,
                 json.dumps(suggestion.tags, ensure_ascii=False), suggested_path,
                 chosen_path, _now()))

    def relevant_feedback(self, topic: str, document_type: str, tags: tuple[str, ...],
                          limit: int = 5) -> list[dict[str, object]]:
        wanted_topic = topic.casefold()
        wanted_type = document_type.casefold()
        wanted_tags = {tag.casefold() for tag in tags}
        with connect(self.db_path) as db:
            rows = list(db.execute("SELECT * FROM ai_feedback ORDER BY id DESC LIMIT 200"))
        scored: list[tuple[int, int, dict[str, object]]] = []
        for row in rows:
            row_tags = tuple(json.loads(row["tags_json"] or "[]"))
            score = 0
            if wanted_topic and row["topic"].casefold() == wanted_topic:
                score += 4
            elif wanted_topic and (row["topic"].casefold() in wanted_topic or wanted_topic in row["topic"].casefold()):
                score += 2
            elif wanted_topic:
                wanted_words = {word for word in wanted_topic.replace("_", " ").split() if len(word) > 3}
                row_words = {word for word in row["topic"].casefold().replace("_", " ").split() if len(word) > 3}
                score += min(2, len(wanted_words & row_words))
            if wanted_type and row["document_type"].casefold() == wanted_type:
                score += 3
            score += len(wanted_tags & {tag.casefold() for tag in row_tags})
            if score:
                scored.append((score, int(row["id"]), {
                    "document_type": row["document_type"], "topic": row["topic"],
                    "tags": row_tags, "suggested_path": row["suggested_path"],
                    "chosen_path": row["chosen_path"],
                }))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in scored[:max(0, limit)]]

    def get_setting(self, key: str, default: str = "") -> str:
        with connect(self.db_path) as db:
            row = db.execute("SELECT value FROM ai_settings WHERE key = ?", (key,)).fetchone()
            return str(row[0]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with connect(self.db_path) as db:
            db.execute("INSERT INTO ai_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, value))

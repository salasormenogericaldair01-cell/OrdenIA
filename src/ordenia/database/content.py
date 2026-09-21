"""SQLite content records and an external-content FTS5 index."""

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ordenia.analysis.models import AnalysisOutcome, AnalysisRecord
from ordenia.analysis.text_utils import snippet

from .connection import connect

logger = logging.getLogger(__name__)


def _record(row: sqlite3.Row) -> AnalysisRecord:
    return AnalysisRecord(
        row["file_id"], row["status"], row["extractor"] or "", row["analyzed_at"] or "",
        row["title"] or "", row["author"] or "", row["subject"] or "",
        row["page_count"], row["slide_count"], row["character_count"], row["content_text"] or "",
        tuple(json.loads(row["keywords_json"] or "[]")), row["error"] or "",
        row["fingerprint_size"] or 0, row["fingerprint_mtime_ns"] or 0,
        bool(row["truncated"]),
    )


class ContentRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.fts_enabled = False

    def initialize(self, db: sqlite3.Connection) -> None:
        db.execute("""CREATE TABLE IF NOT EXISTS file_analysis (
            file_id INTEGER PRIMARY KEY REFERENCES files(id),
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN
                ('pending','analyzing','indexed','unsupported','no_text','failed','stale','skipped')),
            analyzed_at TEXT, extractor TEXT, title TEXT, author TEXT, subject TEXT,
            page_count INTEGER, slide_count INTEGER, character_count INTEGER NOT NULL DEFAULT 0,
            content_text TEXT NOT NULL DEFAULT '', keywords_json TEXT NOT NULL DEFAULT '[]',
            error TEXT, fingerprint_size INTEGER, fingerprint_mtime_ns INTEGER,
            truncated INTEGER NOT NULL DEFAULT 0
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_file_analysis_status ON file_analysis(status)")
        db.execute("""CREATE TRIGGER IF NOT EXISTS files_content_stale AFTER UPDATE OF size, mtime_ns ON files
            WHEN OLD.size != NEW.size OR OLD.mtime_ns != NEW.mtime_ns
            BEGIN UPDATE file_analysis SET status = 'stale'
                WHERE file_id = NEW.id AND status IN ('indexed','no_text','unsupported','failed','skipped'); END""")
        try:
            created = db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'file_analysis_fts'").fetchone() is None
            db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS file_analysis_fts USING fts5(
                content_text, title, author, subject,
                content='file_analysis', content_rowid='file_id', tokenize='unicode61 remove_diacritics 2')""")
        except sqlite3.OperationalError as exc:
            if "fts5" not in str(exc).lower() and "no such module" not in str(exc).lower():
                raise
            self.fts_enabled = False
            logger.warning("SQLite no incluye FTS5; se usará la búsqueda textual local: %s", exc)
            return
        self.fts_enabled = True
        db.execute("""CREATE TRIGGER IF NOT EXISTS file_analysis_fts_insert AFTER INSERT ON file_analysis
            WHEN NEW.status = 'indexed' BEGIN
                INSERT INTO file_analysis_fts(rowid, content_text, title, author, subject)
                VALUES (NEW.file_id, NEW.content_text, NEW.title, NEW.author, NEW.subject);
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS file_analysis_fts_delete AFTER DELETE ON file_analysis
            WHEN OLD.status = 'indexed' BEGIN
                INSERT INTO file_analysis_fts(file_analysis_fts, rowid, content_text, title, author, subject)
                VALUES ('delete', OLD.file_id, OLD.content_text, OLD.title, OLD.author, OLD.subject);
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS file_analysis_fts_update AFTER UPDATE ON file_analysis BEGIN
                INSERT INTO file_analysis_fts(file_analysis_fts, rowid, content_text, title, author, subject)
                SELECT 'delete', OLD.file_id, OLD.content_text, OLD.title, OLD.author, OLD.subject
                WHERE OLD.status = 'indexed';
                INSERT INTO file_analysis_fts(rowid, content_text, title, author, subject)
                SELECT NEW.file_id, NEW.content_text, NEW.title, NEW.author, NEW.subject
                WHERE NEW.status = 'indexed';
            END""")
        if created:
            db.execute("""INSERT INTO file_analysis_fts(rowid, content_text, title, author, subject)
                SELECT file_id, content_text, title, author, subject FROM file_analysis WHERE status = 'indexed'""")
        db.execute("UPDATE file_analysis SET status = 'stale' WHERE status = 'analyzing'")

    def get(self, file_id: int) -> AnalysisRecord:
        with connect(self.db_path) as db:
            row = db.execute("SELECT * FROM file_analysis WHERE file_id = ?", (file_id,)).fetchone()
            return _record(row) if row else AnalysisRecord(file_id)

    def mark_analyzing(self, file_id: int) -> None:
        with connect(self.db_path) as db:
            db.execute("""INSERT INTO file_analysis(file_id, status) VALUES (?, 'analyzing')
                ON CONFLICT(file_id) DO UPDATE SET status = 'analyzing', error = NULL""", (file_id,))

    def save(self, file_id: int, outcome: AnalysisOutcome, size: int, mtime_ns: int, *,
             stale: bool = False, expected_path: Path | None = None) -> None:
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with connect(self.db_path) as db:
            if expected_path is not None:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT path, size, mtime_ns, index_state FROM files WHERE id = ?", (file_id,)).fetchone()
                stale = stale or row is None or (row["path"], row["size"], row["mtime_ns"], row["index_state"]) != (
                    str(expected_path), size, mtime_ns, "active")
            status = "stale" if stale else outcome.status
            if stale:
                outcome = AnalysisOutcome("stale")
            db.execute("""INSERT INTO file_analysis(
                file_id,status,analyzed_at,extractor,title,author,subject,page_count,slide_count,
                character_count,content_text,keywords_json,error,fingerprint_size,fingerprint_mtime_ns,truncated)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(file_id) DO UPDATE SET
                status=excluded.status, analyzed_at=excluded.analyzed_at, extractor=excluded.extractor,
                title=excluded.title, author=excluded.author, subject=excluded.subject,
                page_count=excluded.page_count, slide_count=excluded.slide_count,
                character_count=excluded.character_count, content_text=excluded.content_text,
                keywords_json=excluded.keywords_json, error=excluded.error,
                fingerprint_size=excluded.fingerprint_size,
                fingerprint_mtime_ns=excluded.fingerprint_mtime_ns, truncated=excluded.truncated""",
                (file_id, status, now, outcome.extractor, outcome.title, outcome.author, outcome.subject,
                 outcome.page_count, outcome.slide_count, len(outcome.text), outcome.text,
                 json.dumps(outcome.keywords, ensure_ascii=False),
                 "El archivo cambió durante el análisis; vuelve a analizarlo." if stale else outcome.error,
                 size, mtime_ns, int(outcome.truncated)))

    def counts(self) -> dict[str, int]:
        with connect(self.db_path) as db:
            rows = db.execute("""SELECT COALESCE(a.status, 'pending') AS status, COUNT(*) AS amount
                FROM files f LEFT JOIN file_analysis a ON a.file_id = f.id
                WHERE f.index_state = 'active' GROUP BY COALESCE(a.status, 'pending')""")
            return {row["status"]: row["amount"] for row in rows}

    @staticmethod
    def fts_query(search: str) -> str:
        words = re.findall(r"\w+", search, re.UNICODE)
        return " AND ".join('"' + word + '"' for word in words)

    def search_clause(self, search: str) -> tuple[str, list[str]] | None:
        if self.fts_enabled:
            query = self.fts_query(search)
            if query:
                return ("files.id IN (SELECT rowid FROM file_analysis_fts WHERE file_analysis_fts MATCH ?)", [query])
        words = re.findall(r"\w+", search, re.UNICODE)
        if not words:
            return None
        conditions = " AND ".join("INSTR(CASEFOLD(content_text), ?) > 0" for _ in words)
        return ("files.id IN (SELECT file_id FROM file_analysis WHERE status = 'indexed' AND "
                + conditions + ")", [word.casefold() for word in words])

    def snippets(self, file_ids: list[int], query: str) -> dict[int, str]:
        if not file_ids or not query.strip():
            return {}
        placeholders = ",".join("?" for _ in file_ids)
        with connect(self.db_path) as db:
            if self.fts_enabled and self.fts_query(query):
                rows = db.execute(
                    f"""SELECT rowid, snippet(file_analysis_fts, 0, '', '', '…', 18) AS excerpt
                        FROM file_analysis_fts WHERE file_analysis_fts MATCH ? AND rowid IN ({placeholders})""",
                    (self.fts_query(query), *file_ids),
                )
                return {row["rowid"]: row["excerpt"] or "" for row in rows}
            rows = db.execute(f"SELECT file_id, content_text FROM file_analysis WHERE status = 'indexed' AND file_id IN ({placeholders})", file_ids)
            return {row["file_id"]: snippet(row["content_text"], query) for row in rows
                    if any(word.casefold() in row["content_text"].casefold() for word in re.findall(r"\w+", query))}

"""Batch-only SQLite reads used by the candidate finder."""

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from ordenia.analysis.text_utils import snippet
from ordenia.automation.candidate_models import CandidateQuery
from ordenia.database.models import DetectedFile, WatchedFolder

from .connection import connect
from .content import ContentRepository


@dataclass(frozen=True)
class CandidateSource:
    file: DetectedFile
    folder: WatchedFolder
    title: str
    keywords: tuple[str, ...]
    topic: str
    tags: tuple[str, ...]
    content_match: bool
    content_excerpt: str


def _bound(value: date | datetime, *, upper: bool) -> tuple[str, str]:
    if isinstance(value, datetime):
        return ("<= ?" if upper else ">= ?", value.isoformat())
    if upper:
        return ("< ?", (value + timedelta(days=1)).isoformat())
    return (">= ?", value.isoformat())


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in normalized if not unicodedata.combining(character))


def _file(row: sqlite3.Row) -> DetectedFile:
    return DetectedFile(
        row["id"], row["watched_folder_id"], Path(row["path"]), row["path_key"],
        Path(row["source_directory"]), row["name"], row["extension"], row["size"],
        row["category"], row["detected_at"], row["modified_at"], row["status"],
        row["index_state"], row["mtime_ns"],
    )


def _folder(row: sqlite3.Row) -> WatchedFolder:
    return WatchedFolder(
        row["watched_folder_id"], Path(row["folder_path"]), bool(row["folder_enabled"]),
        bool(row["include_subfolders"]), row["destination_strategy"],
        Path(row["custom_destination"]) if row["custom_destination"] else None,
    )


class CandidateRepository:
    def __init__(self, db_path: Path, content: ContentRepository) -> None:
        self.db_path = db_path
        self.content = content

    @staticmethod
    def _fts_query(text: str) -> str:
        """Use safe token prefixes for simple singular/plural variations."""
        words = re.findall(r"\w+", text, re.UNICODE)
        return " AND ".join(f'"{word}"*' if len(word) >= 4 else f'"{word}"' for word in words)

    def _content_matches(self, db: sqlite3.Connection, query: CandidateQuery) -> dict[int, str]:
        if not query.text or not query.include_content:
            return {}
        fts_query = self._fts_query(query.text)
        if self.content.fts_enabled and fts_query:
            rows = db.execute("""SELECT rowid,
                snippet(file_analysis_fts, 0, '', '', '…', 18) AS excerpt
                FROM file_analysis_fts WHERE file_analysis_fts MATCH ?""", (fts_query,))
            return {int(row["rowid"]): row["excerpt"] or "" for row in rows}
        words = re.findall(r"\w+", query.text, re.UNICODE)
        if not words:
            return {}
        conditions = " AND ".join("INSTR(FOLD(content_text), ?) > 0" for _ in words)
        rows = db.execute(
            "SELECT file_id,content_text FROM file_analysis WHERE status='indexed' AND " + conditions,
            tuple(_fold(word) for word in words),
        )
        return {int(row["file_id"]): snippet(row["content_text"], query.text) for row in rows}

    def search(self, query: CandidateQuery) -> tuple[CandidateSource, ...]:
        clauses: list[str] = ["wf.removed = 0"]
        values: list[object] = []
        if query.active_only:
            clauses.append("f.index_state = 'active'")
        if query.statuses:
            placeholders = ",".join("?" for _ in query.statuses)
            clauses.append(f"f.status IN ({placeholders})")
            values.extend(query.statuses)
        if query.watched_folder_ids:
            placeholders = ",".join("?" for _ in query.watched_folder_ids)
            clauses.append(f"f.watched_folder_id IN ({placeholders})")
            values.extend(query.watched_folder_ids)
        if query.categories:
            placeholders = ",".join("?" for _ in query.categories)
            clauses.append(f"CASEFOLD(f.category) IN ({placeholders})")
            values.extend(category.casefold() for category in query.categories)
        if query.extensions:
            placeholders = ",".join("?" for _ in query.extensions)
            clauses.append(f"LTRIM(CASEFOLD(f.extension), '.') IN ({placeholders})")
            values.extend(query.extensions)
        if query.detected_from is not None:
            operator, value = _bound(query.detected_from, upper=False)
            clauses.append(f"f.detected_at {operator}")
            values.append(value)
        if query.detected_to is not None:
            operator, value = _bound(query.detected_to, upper=True)
            clauses.append(f"f.detected_at {operator}")
            values.append(value)

        metadata_values: list[object] = []
        if query.text:
            words = re.findall(r"\w+", _fold(query.text), re.UNICODE)
            haystack = "FOLD(f.name || ' ' || f.path || ' ' || f.category || ' ' || COALESCE(a.title,'') || ' ' || COALESCE(a.keywords_json,'') || ' ' || COALESCE(ai.topic,'') || ' ' || COALESCE(ai.tags_json,''))"
            metadata = " AND ".join(f"INSTR({haystack}, ?) > 0" for _ in words) if words else "0"
            metadata_values.extend(words)
            text_parts = [f"({metadata})"]
            if query.include_content and words:
                if self.content.fts_enabled:
                    text_parts.append("f.id IN (SELECT rowid FROM file_analysis_fts WHERE file_analysis_fts MATCH ?)")
                    metadata_values.append(self._fts_query(query.text))
                else:
                    content_conditions = " AND ".join("INSTR(FOLD(content_text), ?) > 0" for _ in words)
                    text_parts.append("f.id IN (SELECT file_id FROM file_analysis WHERE status='indexed' AND " + content_conditions + ")")
                    metadata_values.extend(words)
            # Protected extensions remain visible as diagnostics even when
            # their content is irrelevant to the textual query.
            text_parts.append("CASEFOLD(f.extension) IN ('.iso','.img','.vhd','.vhdx','.dll','.sys')")
            clauses.append("(" + " OR ".join(text_parts) + ")")
            values.extend(metadata_values)

        sql = """SELECT f.*,
            wf.path AS folder_path,wf.enabled AS folder_enabled,wf.include_subfolders,
            wf.destination_strategy,wf.custom_destination,
            COALESCE(a.title,'') AS analysis_title,COALESCE(a.keywords_json,'[]') AS keywords_json,
            COALESCE(ai.topic,'') AS ai_topic,COALESCE(ai.tags_json,'[]') AS ai_tags_json
            FROM files f
            JOIN watched_folders wf ON wf.id=f.watched_folder_id
            LEFT JOIN file_analysis a ON a.file_id=f.id AND a.status='indexed'
            LEFT JOIN ai_suggestions ai ON ai.file_id=f.id AND ai.status='ready'
            WHERE """ + " AND ".join(clauses) + " ORDER BY f.detected_at DESC,f.id DESC"

        with connect(self.db_path) as db:
            content_matches = self._content_matches(db, query)
            rows = db.execute(sql, values).fetchall()
        return tuple(CandidateSource(
            _file(row), _folder(row), row["analysis_title"],
            tuple(json.loads(row["keywords_json"] or "[]")), row["ai_topic"],
            tuple(json.loads(row["ai_tags_json"] or "[]")), row["id"] in content_matches,
            content_matches.get(row["id"], ""),
        ) for row in rows)

    def organization_vocabulary(self, limit: int = 500) -> tuple[str, ...]:
        """Return bounded existing topics, tags and keywords in one read."""
        values: list[str] = []
        seen: set[str] = set()
        with connect(self.db_path) as db:
            rows = db.execute("""SELECT keywords_json,'' AS topic,'[]' AS tags_json
                FROM file_analysis WHERE status='indexed' AND keywords_json != '[]'
                UNION ALL
                SELECT '[]',topic,tags_json FROM ai_suggestions
                WHERE status='ready' AND (topic != '' OR tags_json != '[]')
                LIMIT ?""", (max(1, limit),))
            for row in rows:
                candidates = (*json.loads(row["keywords_json"] or "[]"), row["topic"],
                              *json.loads(row["tags_json"] or "[]"))
                for value in candidates:
                    clean = " ".join(str(value).split())
                    key = clean.casefold()
                    if clean and key not in seen:
                        seen.add(key)
                        values.append(clean)
                        if len(values) >= limit:
                            return tuple(values)
        return tuple(values)

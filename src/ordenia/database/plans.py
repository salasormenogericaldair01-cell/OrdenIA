"""SQLite persistence for organization plans and protected paths."""

import json
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ordenia.automation.models import (
    OrganizationPlan,
    PlanItem,
    PlanStatus,
    PlanWarning,
    PlanningMetrics,
    RiskLevel,
    SkippedItem,
)

from .connection import connect


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path.expanduser()))


def _skipped_json(items: tuple[SkippedItem, ...]) -> str:
    return json.dumps([
        {"source": str(item.source), "code": item.code, "reason": item.reason, "file_id": item.file_id}
        for item in items
    ], ensure_ascii=False)


def _metrics_json(metrics: PlanningMetrics) -> str:
    return json.dumps(asdict(metrics), ensure_ascii=False)


def _plan(db: sqlite3.Connection, row: sqlite3.Row) -> OrganizationPlan:
    items = tuple(PlanItem(
        file_id=item["file_id"], source=Path(item["source_path"]),
        relative_destination=item["relative_destination"],
        fingerprint_size=item["fingerprint_size"],
        fingerprint_mtime_ns=item["fingerprint_mtime_ns"],
        reason=item["reason"], risk_level=RiskLevel(item["risk_level"]),
        evidence=tuple(json.loads(item["evidence_json"] or "[]")),
    ) for item in db.execute(
        "SELECT * FROM organization_plan_items WHERE plan_id = ? ORDER BY item_order", (row["id"],)
    ))
    skipped = tuple(SkippedItem(
        source=Path(item["source"]), code=item["code"], reason=item["reason"], file_id=item.get("file_id")
    ) for item in json.loads(row["skipped_json"] or "[]"))
    warnings = tuple(PlanWarning(
        code=warning["code"], message=warning["message"], file_id=warning["file_id"]
    ) for warning in db.execute(
        "SELECT * FROM plan_warnings WHERE plan_id = ? ORDER BY warning_order", (row["id"],)
    ))
    raw_metrics = json.loads(row["metrics_json"] or "{}")
    metrics = PlanningMetrics(**{
        name: int(raw_metrics.get(name, 0)) for name in PlanningMetrics.__dataclass_fields__
    })
    return OrganizationPlan(
        id=row["id"], revision=row["revision"], request_text=row["request_text"],
        status=PlanStatus(row["status"]), items=items, skipped=skipped,
        warnings=warnings, metrics=metrics, schema_version=row["schema_version"],
    )


class PlanRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def initialize(self, db: sqlite3.Connection) -> None:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS organization_plans (
                id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL CHECK(revision > 0),
                request_text TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('draft','validated','invalid')),
                schema_version INTEGER NOT NULL,
                skipped_json TEXT NOT NULL DEFAULT '[]',
                metrics_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS organization_plan_items (
                id INTEGER PRIMARY KEY,
                plan_id TEXT NOT NULL REFERENCES organization_plans(id) ON DELETE CASCADE,
                item_order INTEGER NOT NULL,
                file_id INTEGER NOT NULL REFERENCES files(id),
                source_path TEXT NOT NULL,
                relative_destination TEXT NOT NULL,
                fingerprint_size INTEGER NOT NULL,
                fingerprint_mtime_ns INTEGER NOT NULL,
                reason TEXT NOT NULL,
                risk_level TEXT NOT NULL CHECK(risk_level IN ('normal','review_required','protected')),
                evidence_json TEXT NOT NULL DEFAULT '[]',
                UNIQUE(plan_id, item_order),
                UNIQUE(plan_id, file_id)
            );
            CREATE INDEX IF NOT EXISTS idx_plan_items_file ON organization_plan_items(file_id);
            CREATE TABLE IF NOT EXISTS plan_warnings (
                id INTEGER PRIMARY KEY,
                plan_id TEXT NOT NULL REFERENCES organization_plans(id) ON DELETE CASCADE,
                warning_order INTEGER NOT NULL,
                code TEXT NOT NULL,
                message TEXT NOT NULL,
                file_id INTEGER REFERENCES files(id),
                UNIQUE(plan_id, warning_order)
            );
            CREATE TABLE IF NOT EXISTS protected_paths (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL,
                path_key TEXT NOT NULL UNIQUE,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
        """)
        item_columns = {row["name"] for row in db.execute("PRAGMA table_info(organization_plan_items)")}
        if "evidence_json" not in item_columns:
            db.execute("ALTER TABLE organization_plan_items ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '[]'")

    @staticmethod
    def _write_children(db: sqlite3.Connection, plan: OrganizationPlan) -> None:
        db.executemany("""INSERT INTO organization_plan_items(
            plan_id,item_order,file_id,source_path,relative_destination,
            fingerprint_size,fingerprint_mtime_ns,reason,risk_level,evidence_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", (
            (plan.id, order, item.file_id, str(item.source), item.relative_destination,
             item.fingerprint_size, item.fingerprint_mtime_ns, item.reason, item.risk_level.value,
             json.dumps(item.evidence, ensure_ascii=False))
            for order, item in enumerate(plan.items)
        ))
        db.executemany("""INSERT INTO plan_warnings(
            plan_id,warning_order,code,message,file_id) VALUES (?,?,?,?,?)""", (
            (plan.id, order, warning.code, warning.message, warning.file_id)
            for order, warning in enumerate(plan.warnings)
        ))

    def create(self, plan: OrganizationPlan) -> OrganizationPlan:
        if plan.revision != 1:
            raise ValueError("Un plan nuevo debe comenzar en la revisión 1.")
        now = _now()
        with connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT INTO organization_plans(
                id,revision,request_text,status,schema_version,skipped_json,metrics_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (plan.id, plan.revision, plan.request_text, plan.status.value, plan.schema_version,
                 _skipped_json(plan.skipped), _metrics_json(plan.metrics), now, now))
            self._write_children(db, plan)
        return plan

    def replace(self, plan: OrganizationPlan, *, expected_revision: int) -> OrganizationPlan:
        if plan.revision != expected_revision + 1:
            raise ValueError("La edición debe incrementar exactamente una revisión.")
        with connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            updated = db.execute("""UPDATE organization_plans SET revision=?,request_text=?,status=?,
                schema_version=?,skipped_json=?,metrics_json=?,updated_at=? WHERE id=? AND revision=?""",
                (plan.revision, plan.request_text, plan.status.value, plan.schema_version,
                 _skipped_json(plan.skipped), _metrics_json(plan.metrics), _now(), plan.id, expected_revision))
            if updated.rowcount != 1:
                raise ValueError("El plan cambió; vuelve a cargar la revisión más reciente.")
            db.execute("DELETE FROM organization_plan_items WHERE plan_id = ?", (plan.id,))
            db.execute("DELETE FROM plan_warnings WHERE plan_id = ?", (plan.id,))
            self._write_children(db, plan)
        return plan

    def get(self, plan_id: str) -> OrganizationPlan:
        with connect(self.db_path) as db:
            row = db.execute("SELECT * FROM organization_plans WHERE id = ?", (plan_id,)).fetchone()
            if row is None:
                raise LookupError("Plan no encontrado.")
            return _plan(db, row)

    def current_revision(self, plan_id: str) -> int | None:
        with connect(self.db_path) as db:
            row = db.execute("SELECT revision FROM organization_plans WHERE id = ?", (plan_id,)).fetchone()
            return int(row[0]) if row else None

    def add_protected_path(self, path: Path, reason: str = "Protegida por el usuario") -> Path:
        absolute = Path(os.path.abspath(path.expanduser()))
        with connect(self.db_path) as db:
            db.execute("""INSERT INTO protected_paths(path,path_key,reason,created_at) VALUES (?,?,?,?)
                ON CONFLICT(path_key) DO UPDATE SET path=excluded.path,reason=excluded.reason""",
                (str(absolute), _path_key(absolute), reason, _now()))
        return absolute

    def remove_protected_path(self, path: Path) -> None:
        with connect(self.db_path) as db:
            db.execute("DELETE FROM protected_paths WHERE path_key = ?", (_path_key(path),))

    def list_protected_paths(self) -> tuple[Path, ...]:
        with connect(self.db_path) as db:
            return tuple(Path(row["path"]) for row in db.execute(
                "SELECT path FROM protected_paths ORDER BY path_key"
            ))

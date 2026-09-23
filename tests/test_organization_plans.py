"""V0.5-A plan contracts, validation, persistence and migration."""

import sqlite3
from pathlib import Path

import pytest

from ordenia.ai.models import AISuggestion
from ordenia.analysis.models import AnalysisOutcome
from ordenia.automation.models import (
    OrganizationPlan,
    PlanItem,
    PlanStatus,
    PlanWarning,
    PlanningMetrics,
    RiskLevel,
    SkippedItem,
)
from ordenia.automation.policy import AutomationPolicy, LARGE_FILE_THRESHOLD
from ordenia.automation.validator import PlanValidationError, PlanValidator
from ordenia.core.destinations import validate_relative_destination
from ordenia.database.connection import connect
from ordenia.database.repositories import Repository


def _repository_file(tmp_path: Path, name: str = "report.pdf", content: bytes = b"safe") -> tuple[Repository, Path, int]:
    root = tmp_path / "watched"
    root.mkdir(exist_ok=True)
    path = root / name
    path.write_bytes(content)
    repository = Repository(tmp_path / "ordenia.sqlite3")
    folder = repository.add_folder(root)
    stat = path.stat()
    record = repository.upsert_file(folder.id, path, stat.st_size, "Documentos", "2026-01-01", stat.st_mtime_ns)
    return repository, path, record.id


def _policy(tmp_path: Path, **changes: object) -> AutomationPolicy:
    options: dict[str, object] = {
        "system_roots": (),
        "local_data_root": tmp_path / "guard" / "data",
        "workspace_root": tmp_path / "guard" / "workspace",
    }
    options.update(changes)
    return AutomationPolicy(**options)


def _item(repository: Repository, file_id: int, destination: str = "Estudios/Base de Datos") -> PlanItem:
    record = repository.get_file(file_id)
    return PlanItem(file_id, record.path, destination, record.size, record.mtime_ns, "Material del curso")


def _plan(item: PlanItem, *, plan_id: str = "plan-1") -> OrganizationPlan:
    return OrganizationPlan(
        plan_id, 1, "Ordena este material", PlanStatus.DRAFT, (item,),
        metrics=PlanningMetrics(candidates_considered=1, resolved_by_rules=1),
    )


@pytest.mark.parametrize("value", ["../Secretos", "C:/Windows", r"C:\\Windows", r"\\server\share", "%APPDATA%/x", "CON/files"])
def test_unsafe_relative_destinations_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        validate_relative_destination(value)


def test_valid_relative_destination_is_canonical() -> None:
    assert validate_relative_destination(r"Estudios\Base de Datos") == "Estudios/Base de Datos"


def test_validator_accepts_safe_plan_without_moving_file(tmp_path: Path) -> None:
    repository, path, file_id = _repository_file(tmp_path)
    validated = PlanValidator(repository, _policy(tmp_path)).validate(_plan(_item(repository, file_id)))
    assert validated.status is PlanStatus.VALIDATED
    assert path.exists()
    assert repository.list_operations() == []


@pytest.mark.parametrize("file_id", [0, 999_999])
def test_validator_rejects_invalid_or_unknown_file_id(tmp_path: Path, file_id: int) -> None:
    repository, _path, valid_id = _repository_file(tmp_path)
    item = _item(repository, valid_id)
    invalid = PlanItem(file_id, item.source, item.relative_destination,
                       item.fingerprint_size, item.fingerprint_mtime_ns, item.reason)
    with pytest.raises(PlanValidationError) as error:
        PlanValidator(repository, _policy(tmp_path)).validate(_plan(invalid))
    expected = "invalid_file_id" if file_id == 0 else "unknown_file"
    assert expected in {issue.code for issue in error.value.issues}


def test_validator_rejects_stale_fingerprint_and_changed_path(tmp_path: Path) -> None:
    repository, _path, file_id = _repository_file(tmp_path)
    item = _item(repository, file_id)
    stale = PlanItem(item.file_id, item.source.with_name("other.pdf"), item.relative_destination,
                     item.fingerprint_size + 1, item.fingerprint_mtime_ns, item.reason)
    with pytest.raises(PlanValidationError) as error:
        PlanValidator(repository, _policy(tmp_path)).validate(_plan(stale))
    assert {issue.code for issue in error.value.issues} >= {"changed_path", "stale_fingerprint"}


def test_validator_detects_physical_change_after_indexing(tmp_path: Path) -> None:
    repository, path, file_id = _repository_file(tmp_path)
    plan = _plan(_item(repository, file_id))
    path.write_bytes(b"changed and longer")
    with pytest.raises(PlanValidationError) as error:
        PlanValidator(repository, _policy(tmp_path)).validate(plan)
    assert "stale_fingerprint" in {issue.code for issue in error.value.issues}


def test_validator_rejects_duplicate_file_id(tmp_path: Path) -> None:
    repository, _path, file_id = _repository_file(tmp_path)
    item = _item(repository, file_id)
    plan = OrganizationPlan("duplicates", 1, "x", PlanStatus.DRAFT, (item, item))
    with pytest.raises(PlanValidationError) as error:
        PlanValidator(repository, _policy(tmp_path)).validate(plan)
    assert "duplicate_file" in {issue.code for issue in error.value.issues}


def test_validator_rejects_destination_collision(tmp_path: Path) -> None:
    repository, first, first_id = _repository_file(tmp_path)
    central = tmp_path / "central"
    repository.set_setting("central_destination", str(central))
    repository.update_folder(1, True, "central", None)
    second_root = tmp_path / "other-watched"
    second_root.mkdir()
    second_folder = repository.add_folder(second_root, destination_strategy="central")
    second = second_root / first.name
    second.write_text("second")
    stat = second.stat()
    second_id = repository.upsert_file(second_folder.id, second, stat.st_size, "Documentos", "2026-01-01", stat.st_mtime_ns).id
    plan = OrganizationPlan("collision", 1, "x", PlanStatus.DRAFT,
                            (_item(repository, first_id), _item(repository, second_id)))
    with pytest.raises(PlanValidationError) as error:
        PlanValidator(repository, _policy(tmp_path)).validate(plan)
    assert "destination_collision" in {issue.code for issue in error.value.issues}


def test_large_file_remains_valid_with_review_warning(tmp_path: Path) -> None:
    repository, path, file_id = _repository_file(tmp_path)
    with path.open("wb") as stream:
        stream.truncate(LARGE_FILE_THRESHOLD + 1)
    stat = path.stat()
    repository.upsert_file(1, path, stat.st_size, "Documentos", "2026-01-02", stat.st_mtime_ns)
    validated = PlanValidator(repository, _policy(tmp_path)).validate(_plan(_item(repository, file_id)))
    assert validated.status is PlanStatus.VALIDATED
    assert validated.warnings[0].code == "large_file"
    assert validated.items[0].risk_level is RiskLevel.REVIEW_REQUIRED


def test_validator_rejects_configured_destination_inside_protected_root(tmp_path: Path) -> None:
    repository, _path, file_id = _repository_file(tmp_path)
    protected = tmp_path / "protected"
    repository.update_folder(1, True, "custom", protected)
    policy = _policy(tmp_path, protected_paths=(protected,))
    with pytest.raises(PlanValidationError) as error:
        PlanValidator(repository, policy).validate(_plan(_item(repository, file_id)))
    assert "protected_destination_root" in {issue.code for issue in error.value.issues}


def test_revision_is_incremented_and_checked_optimistically(tmp_path: Path) -> None:
    repository, _path, file_id = _repository_file(tmp_path)
    original = _plan(_item(repository, file_id))
    repository.plans.create(original)
    edited = original.edited(request_text="Nueva instrucción")
    repository.plans.replace(edited, expected_revision=1)
    assert repository.plans.get(original.id).revision == 2
    with pytest.raises(ValueError, match="cambió"):
        repository.plans.replace(edited, expected_revision=1)
    with pytest.raises(PlanValidationError) as error:
        PlanValidator(repository, _policy(tmp_path)).validate(original)
    assert "stale_revision" in {issue.code for issue in error.value.issues}


def test_plan_round_trip_persists_contracts_and_protected_paths(tmp_path: Path) -> None:
    repository, path, file_id = _repository_file(tmp_path)
    plan = OrganizationPlan(
        "persistent", 1, "Ordena", PlanStatus.DRAFT, (_item(repository, file_id),),
        (SkippedItem(path.with_name("skip.iso"), "protected_extension", "Imagen protegida"),),
        (PlanWarning("review", "Revisar destino", file_id),),
        PlanningMetrics(candidates_considered=2, discarded_by_policy=1),
    )
    repository.plans.create(plan)
    protected = repository.plans.add_protected_path(path.parent / "private", "Documentos privados")
    reopened = Repository(repository.db_path)
    loaded = reopened.plans.get(plan.id)
    assert loaded == plan
    assert reopened.plans.list_protected_paths() == (protected,)


def test_custom_protected_path_from_repository_is_applied(tmp_path: Path) -> None:
    repository, path, file_id = _repository_file(tmp_path)
    repository.plans.add_protected_path(path.parent)
    with pytest.raises(PlanValidationError) as error:
        PlanValidator(repository).validate(_plan(_item(repository, file_id)))
    assert "protected_path" in {issue.code for issue in error.value.issues}


def test_v04_migration_preserves_existing_data_and_adds_only_plan_tables(tmp_path: Path) -> None:
    repository, path, file_id = _repository_file(tmp_path, "legacy.txt", b"contenido")
    repository.set_setting("legacy", "kept")
    stat = path.stat()
    repository.content.save(file_id, AnalysisOutcome("indexed", "Texto", "contenido"), stat.st_size, stat.st_mtime_ns)
    repository.ai.save(file_id, AISuggestion("nota", "tema", ("tag",), "Estudios", 0.8, "Razón"),
                       "ollama", "modelo-local", stat.st_size, stat.st_mtime_ns)
    with connect(repository.db_path) as db:
        db.execute("""INSERT INTO operations(file_id,original_path,destination_path,created_at,operation_type,status)
            VALUES (?,?,?,?,?,?)""", (file_id, str(path), str(path.with_name("moved.txt")),
                                      "2026-01-01", "move", "Fallido"))
        db.execute("DROP TABLE plan_warnings")
        db.execute("DROP TABLE organization_plan_items")
        db.execute("DROP TABLE organization_plans")
        db.execute("DROP TABLE protected_paths")

    migrated = Repository(repository.db_path)
    assert migrated.get_file(file_id).name == "legacy.txt"
    assert migrated.get_setting("legacy") == "kept"
    assert migrated.content.get(file_id).text == "contenido"
    assert migrated.ai.get(file_id).suggested_path == "Estudios"
    assert migrated.get_operation(1).status == "Fallido"
    with sqlite3.connect(repository.db_path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"organization_plans", "organization_plan_items", "plan_warnings", "protected_paths"} <= tables
    assert "execution_batches" not in tables
    assert "organization_memory" not in tables

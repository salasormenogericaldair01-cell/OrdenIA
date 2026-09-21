"""V0.4 migration, suggestion state and preference persistence."""

import sqlite3
from pathlib import Path

from ordenia.ai.models import AISuggestion, DEFAULT_AI_MODEL
from ordenia.ai.context import ContentContextBuilder
from ordenia.analysis.models import AnalysisOutcome
from ordenia.database.repositories import Repository
from ordenia.services.file_service import FileService


def _indexed(tmp_path: Path) -> tuple[Repository, Path, int]:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "course.txt"
    path.write_text("SQL y normalización", encoding="utf-8")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    stat = path.stat()
    file_id = repository.upsert_file(folder.id, path, stat.st_size, "Documentos", "2026-01-01", stat.st_mtime_ns).id
    repository.content.save(file_id, AnalysisOutcome(
        "indexed", "Texto", "SQL y normalización", keywords=("SQL", "normalización")
    ), stat.st_size, stat.st_mtime_ns)
    return repository, path, file_id


def _suggestion(path: str = "Estudios/Informática") -> AISuggestion:
    return AISuggestion("material_academico", "bases de datos", ("SQL", "normalización"), path, 0.9, "Material de estudio.")


def test_v031_database_migrates_without_losing_data(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "legacy.txt"
    path.write_text("legacy")
    with sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE watched_folders(id INTEGER PRIMARY KEY,path TEXT UNIQUE,enabled INTEGER,removed INTEGER,
              include_subfolders INTEGER,destination_strategy TEXT,custom_destination TEXT,created_at TEXT);
            CREATE TABLE files(id INTEGER PRIMARY KEY,watched_folder_id INTEGER,path TEXT UNIQUE,path_key TEXT UNIQUE,
              source_directory TEXT,name TEXT,extension TEXT,size INTEGER,category TEXT,detected_at TEXT,
              modified_at TEXT,mtime_ns INTEGER,status TEXT,index_state TEXT);
            CREATE TABLE operations(id INTEGER PRIMARY KEY,file_id INTEGER,original_path TEXT,destination_path TEXT,
              created_at TEXT,operation_type TEXT,status TEXT,error_message TEXT,undone_at TEXT,restored_path TEXT);
            CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE managed_destinations(path TEXT PRIMARY KEY,added_at TEXT);
            CREATE TABLE file_analysis(file_id INTEGER PRIMARY KEY,status TEXT,analyzed_at TEXT,extractor TEXT,title TEXT,
              author TEXT,subject TEXT,page_count INTEGER,slide_count INTEGER,character_count INTEGER,content_text TEXT,
              keywords_json TEXT,error TEXT,fingerprint_size INTEGER,fingerprint_mtime_ns INTEGER,truncated INTEGER);
        """)
        db.execute("INSERT INTO watched_folders VALUES(1,?,1,0,1,'inside',NULL,'2026-01-01')", (str(root),))
        db.execute("""INSERT INTO files(id,watched_folder_id,path,path_key,source_directory,name,extension,size,
                   category,detected_at,modified_at,mtime_ns,status,index_state)
                   VALUES(1,1,?,?,?,?,?,?,'Documentos','2026-01-01','2026-01-01',?,'Pendiente','active')""",
                   (str(path), str(path).casefold(), str(root), path.name, ".txt",
                    path.stat().st_size, path.stat().st_mtime_ns))
        db.execute("INSERT INTO settings VALUES('legacy','kept')")
    repository = Repository(database)
    assert repository.get_setting("legacy") == "kept"
    assert repository.get_file(1).name == "legacy.txt"
    assert repository.ai.get(1).status == "pending"
    with sqlite3.connect(database) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"ai_suggestions", "ai_feedback", "ai_settings"} <= tables


def test_suggestion_persists_and_file_change_marks_it_stale(tmp_path: Path) -> None:
    repository, path, file_id = _indexed(tmp_path)
    stat = path.stat()
    assert repository.ai.save(file_id, _suggestion(), "ollama", "qwen3:4b", stat.st_size, stat.st_mtime_ns) == "ready"
    reopened = Repository(repository.db_path)
    assert reopened.ai.get(file_id).suggested_path == "Estudios/Informática"
    path.write_text("contenido modificado y más largo", encoding="utf-8")
    changed = path.stat()
    reopened.upsert_file(1, path, changed.st_size, "Documentos", "2026-01-02", changed.st_mtime_ns)
    assert reopened.ai.get(file_id).status == "stale"


def test_missing_file_reconciliation_invalidates_but_preserves_suggestion(tmp_path: Path) -> None:
    repository, path, file_id = _indexed(tmp_path)
    stat = path.stat()
    repository.ai.save(file_id, _suggestion(), "ollama", "qwen3:4b", stat.st_size, stat.st_mtime_ns)
    path.unlink()
    folder = repository.get_folder(1)
    repository.reconcile_folder(folder, ())
    record = repository.ai.get(file_id)
    assert repository.get_file(file_id).index_state == "missing"
    assert record.status == "stale"
    assert record.suggested_path == "Estudios/Informática"


def test_feedback_and_relevant_preferences_persist(tmp_path: Path) -> None:
    repository, _path, file_id = _indexed(tmp_path)
    repository.ai.save_feedback(file_id, _suggestion(), "Estudios/Base de Datos")
    reopened = Repository(repository.db_path)
    feedback = reopened.ai.relevant_feedback("bases de datos", "material_academico", ("SQL",), 3)
    assert feedback[0]["suggested_path"] == "Estudios/Informática"
    assert feedback[0]["chosen_path"] == "Estudios/Base de Datos"
    stat = _path.stat()
    reopened.ai.save(file_id, _suggestion(), "ollama", "qwen3:4b", stat.st_size, stat.st_mtime_ns)
    context = ContentContextBuilder(reopened).build(file_id)
    assert "Estudios/Base de Datos" in context.preferences[0]


def test_ai_settings_are_independent_and_persistent(tmp_path: Path) -> None:
    repository, _path, _file_id = _indexed(tmp_path)
    assert repository.ai.get_setting("model") == DEFAULT_AI_MODEL
    repository.ai.set_setting("model", "otro-modelo")
    assert Repository(repository.db_path).ai.get_setting("model") == "otro-modelo"


def test_existing_model_setting_is_never_replaced_by_new_recommendation(tmp_path: Path) -> None:
    repository, _path, _file_id = _indexed(tmp_path)
    repository.ai.set_setting("model", "qwen3:4b")
    reopened = Repository(repository.db_path)
    assert reopened.ai.get_setting("model") == "qwen3:4b"


def test_organize_and_undo_preserve_ready_suggestion(tmp_path: Path) -> None:
    repository, path, file_id = _indexed(tmp_path)
    stat = path.stat()
    repository.ai.save(file_id, _suggestion("Estudios/Base de Datos"), "ollama", "qwen3:4b", stat.st_size, stat.st_mtime_ns)
    service = FileService(repository)
    try:
        service._organize(file_id, "Estudios/Base de Datos", "Estudios/Base de Datos")
        operation = repository.list_operations()[0]
        assert repository.ai.get(file_id).status == "ready"
        service._undo(operation.id)
        assert repository.ai.get(file_id).status == "ready"
    finally:
        service.close()


def test_changed_ai_path_is_feedback_only_after_successful_move(tmp_path: Path) -> None:
    repository, path, file_id = _indexed(tmp_path)
    stat = path.stat()
    repository.ai.save(file_id, _suggestion(), "ollama", "qwen3:4b", stat.st_size, stat.st_mtime_ns)
    service = FileService(repository)
    try:
        service._organize(file_id, "Estudios/Base de Datos", "Estudios/Informática")
        feedback = repository.ai.relevant_feedback("bases de datos", "material_academico", ("SQL",), 5)
        assert feedback[0]["chosen_path"] == "Estudios/Base de Datos"
    finally:
        service.close()


def test_ai_relative_path_uses_user_configured_root(tmp_path: Path) -> None:
    repository, _path, file_id = _indexed(tmp_path)
    central = tmp_path / "central-library"
    repository.set_setting("central_destination", str(central))
    repository.update_folder(1, True, "central", None)
    service = FileService(repository)
    try:
        proposal = service.proposal(repository.get_file(file_id), "Estudios/Base de Datos")
        assert proposal == central.resolve() / "Estudios" / "Base de Datos" / "course.txt"
    finally:
        service.close()

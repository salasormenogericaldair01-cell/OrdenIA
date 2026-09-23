"""Structured, explainable retrieval from the local SQLite index."""

from datetime import date
from pathlib import Path

from ordenia.ai.models import AISuggestion
from ordenia.analysis.models import AnalysisOutcome
from ordenia.automation.candidate_finder import CandidateFinder
from ordenia.automation.candidate_models import CandidateQuery
from ordenia.automation.grouping import RelationshipGrouper
from ordenia.automation.models import RiskLevel
from ordenia.automation.policy import AutomationPolicy, LARGE_FILE_THRESHOLD
from ordenia.database.connection import connect
from ordenia.database.repositories import Repository


def _policy(tmp_path: Path) -> AutomationPolicy:
    return AutomationPolicy(
        system_roots=(), local_data_root=tmp_path / "guard-data",
        workspace_root=tmp_path / "guard-workspace",
    )


def _add(
    repository: Repository, root: Path, name: str, *, category: str = "Documentos",
    content: str = "", keywords: tuple[str, ...] = (), detected_at: str = "2026-09-20T10:00:00",
) -> int:
    path = root / name
    path.write_text(content or name, encoding="utf-8")
    stat = path.stat()
    record = repository.upsert_file(1, path, stat.st_size, category, detected_at, stat.st_mtime_ns)
    with connect(repository.db_path) as db:
        db.execute("UPDATE files SET detected_at=?,modified_at=? WHERE id=?", (detected_at, detected_at, record.id))
    if content:
        repository.content.save(record.id, AnalysisOutcome("indexed", "Texto", content, keywords=keywords),
                                stat.st_size, stat.st_mtime_ns)
    return record.id


def _repo(tmp_path: Path) -> tuple[Repository, Path, CandidateFinder]:
    root = tmp_path / "Downloads"
    root.mkdir()
    repository = Repository(tmp_path / "ordenia.sqlite3")
    repository.add_folder(root)
    return repository, root, CandidateFinder(repository, _policy(tmp_path))


def test_term_in_name_is_found_without_content_search(tmp_path: Path) -> None:
    repository, root, finder = _repo(tmp_path)
    wanted = _add(repository, root, "base_de_datos_apuntes.pdf")
    _add(repository, root, "vacaciones.jpg", category="Imágenes")
    result = finder.find(CandidateQuery(text="base de datos", include_content=False))
    assert [item.file_id for item in result.eligible] == [wanted]
    assert result.eligible[0].matched_by == ("name", "path")


def test_term_only_in_fts_content_is_found(tmp_path: Path) -> None:
    repository, root, finder = _repo(tmp_path)
    wanted = _add(repository, root, "documento1.pdf", content="El curso estudia bases de datos distribuidas.")
    result = finder.find(CandidateQuery(text="base de datos"))
    assert [item.file_id for item in result.eligible] == [wanted]
    assert "content" in result.eligible[0].matched_by
    assert "bases de datos" in " ".join(result.eligible[0].evidence).casefold()


def test_ready_ai_topic_and_tags_are_batch_searchable(tmp_path: Path) -> None:
    repository, root, finder = _repo(tmp_path)
    topic_id = _add(repository, root, "documento.pdf")
    tag_id = _add(repository, root, "otro.pdf")
    for file_id, topic, tags in ((topic_id, "inventario institucional", ("almacén",)),
                                 (tag_id, "logística", ("sensor ultrasónico",))):
        record = repository.get_file(file_id)
        repository.ai.save(file_id, AISuggestion("documento", topic, tags, "Otros", 0.8, "Clasificación"),
                           "ollama", "modelo-local", record.size, record.mtime_ns)
    topic_result = finder.find(CandidateQuery(text="inventario institucional", include_content=False))
    tag_result = finder.find(CandidateQuery(text="sensor ultrasonico", include_content=False))
    assert topic_result.eligible[0].file_id == topic_id
    assert topic_result.eligible[0].matched_by == ("topic",)
    assert tag_result.eligible[0].file_id == tag_id
    assert tag_result.eligible[0].matched_by == ("tags",)


def test_extension_category_date_and_folder_filters_combine(tmp_path: Path) -> None:
    repository, root, finder = _repo(tmp_path)
    wanted = _add(repository, root, "lesson.pdf", detected_at="2026-09-20T10:00:00")
    _add(repository, root, "old.pdf", detected_at="2026-08-01T10:00:00")
    _add(repository, root, "lesson.txt", detected_at="2026-09-20T10:00:00")
    result = finder.find(CandidateQuery(
        watched_folder_ids=(1,), categories=("Documentos",), extensions=(".PDF",),
        detected_from=date(2026, 9, 20), detected_to=date(2026, 9, 20),
    ))
    assert [item.file_id for item in result.eligible] == [wanted]
    assert any("periodo" in evidence for evidence in result.eligible[0].evidence)


def test_inactive_and_ignored_are_excluded_by_default(tmp_path: Path) -> None:
    repository, root, finder = _repo(tmp_path)
    ignored = _add(repository, root, "ignored.pdf")
    inactive = _add(repository, root, "missing.pdf")
    repository.set_file_status(ignored, "Ignorado")
    with connect(repository.db_path) as db:
        db.execute("UPDATE files SET index_state='missing' WHERE id=?", (inactive,))
    assert finder.find(CandidateQuery()).eligible == ()


def test_protected_and_large_files_are_separated(tmp_path: Path) -> None:
    repository, root, finder = _repo(tmp_path)
    iso_id = _add(repository, root, "ubuntu.iso", category="Otros")
    large_path = root / "base_de_datos_grande.pdf"
    with large_path.open("wb") as stream:
        stream.truncate(LARGE_FILE_THRESHOLD + 1)
    stat = large_path.stat()
    large_id = repository.upsert_file(1, large_path, stat.st_size, "Documentos", "2026-09-20", stat.st_mtime_ns).id
    result = finder.find(CandidateQuery(text="base de datos", include_content=False))
    assert [item.file_id for item in result.review_required] == [large_id]
    assert result.review_required[0].policy_level is RiskLevel.REVIEW_REQUIRED
    assert [item.file_id for item in result.skipped] == [iso_id]
    assert result.skipped[0].policy_code == "protected_extension"


def test_ranking_is_explainable_and_deterministic(tmp_path: Path) -> None:
    repository, root, finder = _repo(tmp_path)
    name_id = _add(repository, root, "inventario_anual.pdf")
    content_id = _add(repository, root, "documento.pdf", content="Este es el inventario anual de la institución.")
    query = CandidateQuery(text="inventario anual")
    first = finder.find(query)
    second = finder.find(query)
    assert first == second
    assert [item.file_id for item in first.eligible] == [name_id, content_id]
    assert first.eligible[0].score > first.eligible[1].score


def test_five_database_pdfs_and_protected_iso_real_case(tmp_path: Path) -> None:
    repository, root, finder = _repo(tmp_path)
    names = (
        "S06_SQL_Avanzado.pdf", "S07_Normalizacion.pdf", "S08_Modelo_Relacional.pdf",
        "S09_Stored_Procedures.pdf", "S10_Indices.pdf",
    )
    wanted = []
    for name in names:
        wanted.append(_add(
            repository, root, name,
            content="Material de base de datos sobre SQL, normalización y modelo relacional.",
            keywords=("base de datos", "SQL", "normalización", "modelo relacional"),
        ))
    photo_id = _add(repository, root, "foto_random.jpg", category="Imágenes")
    iso_id = _add(repository, root, "ubuntu.iso", category="Otros")
    result = finder.find(CandidateQuery(text="base de datos"))
    assert {item.file_id for item in result.eligible} == set(wanted)
    assert [item.file_id for item in result.skipped] == [iso_id]
    assert all("content" in item.matched_by for item in result.eligible)
    assert photo_id not in {item.file_id for item in result.reviewable_candidates + result.skipped}
    groups = RelationshipGrouper().group(result.reviewable_candidates)
    assert len(groups) == 1
    assert {item.file_id for item in groups[0].files} == set(wanted)
    assert any("S06–S10" in evidence for evidence in groups[0].evidence)

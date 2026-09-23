"""Deterministic grouping by sequences, content metadata and time."""

from pathlib import Path

from ordenia.automation.candidate_models import CandidateFile
from ordenia.automation.grouping import RelationshipGrouper


def _candidate(
    file_id: int, name: str, *, category: str = "Documentos",
    detected: str = "2026-09-20T10:00:00", topic: str = "",
    tags: tuple[str, ...] = (), keywords: tuple[str, ...] = (),
    matched_by: tuple[str, ...] = ("content",),
) -> CandidateFile:
    root = Path("C:/Users/Test/Downloads")
    return CandidateFile(
        file_id, 1, root / name, root, name, Path(name).suffix.lower(), category,
        100, 1, detected, detected, matched_by, 7.0, ("Coincide",), topic, tags, keywords,
    )


def test_numbered_database_sequence_forms_one_group_and_ignores_photo() -> None:
    keywords = ("base de datos", "SQL", "normalización", "modelo relacional")
    files = tuple(_candidate(index, name, keywords=keywords) for index, name in enumerate((
        "S06_SQL_Avanzado.pdf", "S07_Normalizacion.pdf", "S08_Modelo_Relacional.pdf",
        "S09_Stored_Procedures.pdf", "S10_Indices.pdf",
    ), 1)) + (_candidate(6, "foto_random.jpg", category="Imágenes", keywords=(), matched_by=("name",)),)
    groups = RelationshipGrouper().group(files)
    assert len(groups) == 1
    assert [file.name for file in groups[0].files] == sorted(file.name for file in files[:5])
    assert "foto_random.jpg" not in {file.name for file in groups[0].files}
    assert any("secuencia" in item.casefold() for item in groups[0].evidence)
    assert groups[0].date_range == ("2026-09-20T10:00:00", "2026-09-20T10:00:00")


def test_files_group_by_shared_content_signals_without_sequence() -> None:
    common = ("residuos", "sensor", "recolección")
    files = (
        _candidate(1, "alpha.pdf", keywords=common),
        _candidate(2, "beta.docx", keywords=common),
        _candidate(3, "holiday.jpg", category="Imágenes", keywords=(), matched_by=("name",)),
    )
    groups = RelationshipGrouper().group(files)
    assert len(groups) == 1
    assert {file.file_id for file in groups[0].files} == {1, 2}
    assert groups[0].group_label == "residuos · sensor · recolección"
    assert any("palabras clave" in item.casefold() for item in groups[0].evidence)


def test_same_topic_and_tags_group_across_directories() -> None:
    first = _candidate(1, "one.pdf", topic="Bases de datos", tags=("SQL", "curso"))
    second = _candidate(2, "two.pdf", topic="Bases de datos", tags=("SQL", "práctica"))
    second = CandidateFile(**{**second.__dict__, "source_directory": Path("D:/Studies")})
    group = RelationshipGrouper().group((first, second))[0]
    assert group.group_label == "Bases de datos"
    assert group.common_topics == ("Bases de datos",)
    assert group.common_tags == ("SQL",)


def test_grouping_is_idempotent() -> None:
    files = tuple(_candidate(index, f"clase_{index:02d}.pdf") for index in range(1, 6))
    grouper = RelationshipGrouper()
    assert grouper.group(files) == grouper.group(files)

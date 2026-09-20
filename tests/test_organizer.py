from pathlib import Path

import pytest

from ordenia.core.file_utils import available_path
from ordenia.core.organizer import Organizer


def test_available_path_keeps_compound_extension(tmp_path: Path) -> None:
    (tmp_path / "backup.tar.gz").write_text("one")
    (tmp_path / "backup (1).tar.gz").write_text("two")
    assert available_path(tmp_path / "backup.tar.gz").name == "backup (2).tar.gz"


def test_move_requires_watched_root_and_preserves_collision(tmp_path: Path) -> None:
    source = tmp_path / "archivo.pdf"
    source.write_text("new")
    occupied = tmp_path / "OrdenIA" / "Documentos" / source.name
    occupied.parent.mkdir(parents=True)
    occupied.write_text("old")
    destination = Organizer().move(source, tmp_path, "Documentos")
    assert destination.name == "archivo (1).pdf"
    assert destination.read_text() == "new"
    assert occupied.read_text() == "old"
    assert not source.exists()


def test_move_rejects_source_outside_watched_root(tmp_path: Path) -> None:
    source = tmp_path / "outside.pdf"
    source.write_text("data")
    watched = tmp_path / "watched"
    watched.mkdir()
    with pytest.raises(ValueError):
        Organizer().move(source, watched, "Documentos")
    assert source.read_text() == "data"


def test_undo_restores_with_collision(tmp_path: Path) -> None:
    source = tmp_path / "archivo.pdf"
    source.write_text("new")
    organizer = Organizer()
    destination = organizer.move(source, tmp_path, "Documentos")
    source.write_text("other")
    restored = organizer.undo(destination, source)
    assert restored.name == "archivo (1).pdf"
    assert restored.read_text() == "new"
    assert source.read_text() == "other"
    assert not destination.exists()


def test_missing_destination_cannot_be_undone(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Organizer().undo(tmp_path / "missing.pdf", tmp_path / "original.pdf")

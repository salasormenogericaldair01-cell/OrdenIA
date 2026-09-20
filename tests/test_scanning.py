from pathlib import Path

import pytest

from ordenia.core.exclusions import ExclusionPolicy
from ordenia.database.repositories import Repository
from ordenia.scanning.scanner import FileScanner
from ordenia.services.file_service import FileService


@pytest.mark.parametrize("name", ["desktop.ini", "Thumbs.db", "ehthumbs.db", ".DS_Store", "~$document.docx", "copy.tmp", "copy.temp", "copy.part", "copy.crdownload"])
def test_system_and_temporary_files_are_excluded(tmp_path: Path, name: str) -> None:
    assert not ExclusionPolicy().eligible_path(tmp_path / name, tmp_path, True)


@pytest.mark.parametrize("directory", ["$RECYCLE.BIN", "System Volume Information", "__pycache__", ".git", ".venv", "node_modules"])
def test_system_directories_are_excluded(tmp_path: Path, directory: str) -> None:
    assert not ExclusionPolicy().eligible_path(tmp_path / directory / "file.pdf", tmp_path, True)


def _sample_tree(root: Path) -> None:
    (root / "informe.pdf").write_text("pdf")
    (root / "foto.jpg").write_text("jpg")
    (root / "desktop.ini").write_text("system")
    (root / "archivo.tmp").write_text("temp")
    (root / "proyecto").mkdir()
    (root / "proyecto" / "codigo.py").write_text("print(1)")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("ignored")
    (root / "OrdenIA" / "Documentos").mkdir(parents=True)
    (root / "OrdenIA" / "Documentos" / "old.pdf").write_text("managed")


def test_initial_recursive_scan_and_rescan_are_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "TestOrdenIA"
    root.mkdir()
    _sample_tree(root)
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root, include_subfolders=True)
    service = FileService(repository)
    try:
        assert service.count_existing(folder.id) == 3
        service._scan_folder(folder.id)
        files = repository.list_files()
        assert {file.name: file.category for file in files} == {"informe.pdf": "Documentos", "foto.jpg": "Imágenes", "codigo.py": "Código"}
        assert all(file.status == "Pendiente" for file in files)
        assert all(file.path.is_file() for file in files)
        first_ids = {file.path: file.id for file in files}
        service._scan_folder(folder.id)
        assert {file.path: file.id for file in repository.list_files()} == first_ids
        assert repository.list_operations() == []
    finally:
        service.close()


def test_nonrecursive_scan_sees_only_direct_files(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    _sample_tree(root)
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root, include_subfolders=False)
    service = FileService(repository)
    try:
        assert service.count_existing(folder.id) == 2
        service._scan_folder(folder.id)
        assert {file.name for file in repository.list_files()} == {"informe.pdf", "foto.jpg"}
    finally:
        service.close()


def test_scanner_excludes_custom_managed_root_inside_watched_folder(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    managed = root / "MyLibrary"
    managed.mkdir(parents=True)
    (managed / "already.pdf").write_text("managed")
    (root / "fresh.pdf").write_text("fresh")
    paths = list(FileScanner().iter_paths(root, True, (managed,)))
    assert paths == [root / "fresh.pdf"]


def test_scanner_does_not_reinsert_a_file_moved_before_batch_commit(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    source = root / "report.pdf"
    source.write_text("content")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    file = repository.upsert_file(folder.id, source, 7, "Documentos", "2026-01-01T00:00:00+00:00")
    service = FileService(repository)

    def paths_then_move(*_args):
        yield source
        service._organize(file.id)

    monkeypatch.setattr(service.scanner, "count", lambda *_args: 1)
    monkeypatch.setattr(service.scanner, "iter_paths", paths_then_move)
    try:
        service._scan_folder(folder.id)
        files = repository.list_files()
        assert len(files) == 1
        assert files[0].id == file.id
        assert files[0].status == "Organizado"
        assert files[0].path.is_file()
    finally:
        service.close()


def test_watcher_and_scanner_share_one_record(tmp_path: Path) -> None:
    import time

    root = tmp_path / "watched"
    root.mkdir()
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    service = FileService(repository)
    try:
        (root / "new.pdf").write_text("content")
        service._scan_folder(folder.id)
        time.sleep(1)
        assert len(repository.list_files()) == 1
    finally:
        service.close()

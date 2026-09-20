from pathlib import Path

from ordenia.database.repositories import Repository
from ordenia.database.models import IndexedEntry


def test_search_filters_sort_and_pagination(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(tmp_path)
    paths = [tmp_path / "Proyecto Uno.pdf", tmp_path / "proyecto dos.pdf", tmp_path / "proyecto.png", tmp_path / "otro.pdf"]
    for index, path in enumerate(paths, start=1):
        path.write_bytes(b"x" * index)
        category = "Imágenes" if path.suffix == ".png" else "Documentos"
        repository.upsert_file(folder.id, path, index, category, "2026-01-01T00:00:00+00:00")
    repository.set_file_status(repository.list_files()[0].id, "Ignorado")
    matches, total = repository.search_files("PROYECTO", "Pendiente", "Documentos", "size", False)
    assert total == 2
    assert [file.name for file in matches] == ["Proyecto Uno.pdf", "proyecto dos.pdf"]
    matches, total = repository.search_files(".PNG", "Todos", "Todas")
    assert total == 1 and matches[0].name == "proyecto.png"
    matches, total = repository.search_files("", "Todos", "Todas", "size", False, limit=2, offset=2)
    assert total == 4 and [file.size for file in matches] == [3, 4]


def test_path_search_uses_indexed_data(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(tmp_path)
    path = tmp_path / "Informe.PDF"
    path.write_text("a")
    repository.upsert_file(folder.id, path, 1, "Documentos", "2026-01-01T00:00:00+00:00")
    path.unlink()
    results, total = repository.search_files("INFORME.PDF")
    assert total == 1 and results[0].name == path.name


def test_large_index_is_queried_in_pages(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(tmp_path)
    entries = [IndexedEntry(tmp_path / f"file-{index:04}.pdf", index, "Documentos", "2026-01-01T00:00:00+00:00") for index in range(1001)]
    for start in range(0, len(entries), 200):
        repository.upsert_files_batch(folder.id, entries[start:start + 200])
    first, total = repository.search_files(limit=200)
    last, last_total = repository.search_files(limit=200, offset=1000)
    assert total == last_total == 1001
    assert len(first) == 200
    assert len(last) == 1

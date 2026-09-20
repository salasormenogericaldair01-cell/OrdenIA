from pathlib import Path

import pytest

from ordenia.database.repositories import Repository
from ordenia.services.file_service import FileService


@pytest.mark.parametrize("strategy", ["inside", "central", "custom"])
def test_organize_and_undo_all_destination_strategies(tmp_path: Path, strategy: str) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    source = watched / "sample.pdf"
    source.write_text("content")
    central = tmp_path / "central"
    custom = tmp_path / "custom"
    custom.mkdir()
    repository = Repository(tmp_path / "data.sqlite3")
    repository.set_setting("central_destination", str(central))
    folder = repository.add_folder(watched, destination_strategy=strategy, custom_destination=custom if strategy == "custom" else None)
    file = repository.upsert_file(folder.id, source, source.stat().st_size, "Documentos", "2026-01-01T00:00:00+00:00")
    service = FileService(repository)
    try:
        root = watched / "OrdenIA" if strategy == "inside" else central if strategy == "central" else custom
        assert service.destination_for(folder) == root
        service._organize(file.id)
        destination = root / "Documentos" / source.name
        assert destination.is_file()
        assert not source.exists()
        assert repository.get_file(file.id).path == destination
        operation = repository.list_operations()[0]
        assert operation.status == "Completado"
        service._undo(operation.id)
        assert source.is_file()
        assert not destination.exists()
        assert repository.get_operation(operation.id).status == "Deshecho"
    finally:
        service.close()


def test_folder_options_and_preferences_persist(tmp_path: Path) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    custom = tmp_path / "custom"
    custom.mkdir()
    database = tmp_path / "data.sqlite3"
    repository = Repository(database)
    folder = repository.add_folder(watched, False, "custom", custom)
    repository.set_setting("show_notifications", "0")
    repository.set_setting("ask_before_scan", "0")
    repository.set_setting("central_destination", str(tmp_path / "central"))
    reopened = Repository(database)
    assert reopened.get_folder(folder.id).include_subfolders is False
    assert reopened.get_folder(folder.id).destination_strategy == "custom"
    assert reopened.get_folder(folder.id).custom_destination == custom
    assert reopened.get_setting("show_notifications") == "0"
    assert reopened.get_setting("ask_before_scan") == "0"
    assert reopened.get_setting("central_destination") == str(tmp_path / "central")
    updated = reopened.update_folder(folder.id, True, "inside", None)
    assert Repository(database).get_folder(updated.id).include_subfolders is True


def test_custom_destination_cannot_contain_entire_watched_root(tmp_path: Path) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    service = FileService(Repository(tmp_path / "data.sqlite3"))
    try:
        with pytest.raises(ValueError):
            service.add_folder(watched, destination_strategy="custom", custom_destination=tmp_path, offer_scan=False)
        assert service.repository.list_folders() == []
    finally:
        service.close()

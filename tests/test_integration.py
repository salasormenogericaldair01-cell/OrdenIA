import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt

from ordenia.core.classifier import ExtensionClassifier
from ordenia.database.repositories import Repository
from ordenia.monitoring.watcher import FolderWatcher


def test_watcher_detects_new_file_and_skips_temporary(tmp_path: Path) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    repository = Repository(tmp_path / "data.sqlite3")
    repository.add_folder(watched)
    detected = threading.Event()
    watcher = FolderWatcher(repository, ExtensionClassifier(), detected.set)
    watcher.start()
    try:
        time.sleep(0.3)
        (watched / "download.part").write_text("temporary")
        (watched / "desktop.ini").write_text("system")
        (watched / "report.pdf").write_text("ready")
        assert detected.wait(6), "watchdog no notificó el archivo nuevo"
        files = repository.list_files()
        assert len(files) == 1
        assert files[0].name == "report.pdf"
        assert files[0].category == "Documentos"
    finally:
        watcher.stop()


def test_window_can_open_and_close_offscreen(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ordenia.services.file_service import FileService
    from ordenia.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    repository = Repository(tmp_path / "data.sqlite3")
    service = FileService(repository)
    window = MainWindow(repository, service)
    try:
        window.show()
        app.processEvents()
        assert window.windowTitle() == "OrdenIA · V0.4.0"
        assert window.stack.count() == 5
    finally:
        window.close()


def test_service_moves_and_undoes_with_database(tmp_path: Path) -> None:
    from ordenia.services.file_service import FileService

    watched = tmp_path / "watched"
    watched.mkdir()
    original = watched / "inventory.csv"
    original.write_text("sku,count\nA,2\n")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(watched)
    file = repository.upsert_file(folder.id, original, original.stat().st_size, "Hojas de cálculo", "2026-01-01T00:00:00+00:00")
    service = FileService(repository)
    try:
        service._organize(file.id)
        operation = repository.list_operations()[0]
        assert operation.operation_type == "move"
        assert operation.status == "Completado"
        assert operation.destination_path.read_text() == "sku,count\nA,2\n"
        assert not original.exists()
        assert repository.get_file(file.id).status == "Organizado"
        service._undo(operation.id)
        assert original.read_text() == "sku,count\nA,2\n"
        assert repository.get_file(file.id).status == "Pendiente"
        assert not operation.destination_path.exists()
        assert repository.get_operation(operation.id).status == "Deshecho"
    finally:
        service.close()


def test_failed_move_is_pending_and_never_completed(tmp_path: Path, monkeypatch) -> None:
    from ordenia.services.file_service import FileService

    watched = tmp_path / "watched"
    watched.mkdir()
    original = watched / "example.pdf"
    original.write_text("untouched")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(watched)
    file = repository.upsert_file(folder.id, original, original.stat().st_size, "Documentos", "2026-01-01T00:00:00+00:00")
    service = FileService(repository)
    destination = watched / "OrdenIA" / "Documentos" / original.name
    monkeypatch.setattr(service.organizer, "move", lambda *_: destination)
    try:
        service._organize(file.id)
        assert original.read_text() == "untouched"
        assert not destination.exists()
        assert repository.get_file(file.id).status == "Pendiente"
        operations = repository.list_operations()
        assert len(operations) == 1
        assert operations[0].status == "Fallido"
        assert operations[0].error_message
    finally:
        service.close()


def test_copy_without_removing_source_cannot_complete(tmp_path: Path, monkeypatch) -> None:
    from ordenia.services.file_service import FileService

    watched = tmp_path / "watched"
    watched.mkdir()
    source = watched / "example.pdf"
    source.write_text("original")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(watched)
    file = repository.upsert_file(folder.id, source, source.stat().st_size, "Documentos", "2026-01-01T00:00:00+00:00")
    service = FileService(repository)
    destination = watched / "OrdenIA" / "Documentos" / source.name

    def copy_instead_of_move(*_args) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text())
        return destination

    monkeypatch.setattr(service.organizer, "move", copy_instead_of_move)
    try:
        service._organize(file.id)
        assert source.is_file()
        assert destination.is_file()
        assert repository.get_file(file.id).status == "Pendiente"
        assert repository.list_operations()[0].status == "Fallido"
    finally:
        service.close()


def test_undo_watcher_does_not_duplicate_restored_file(tmp_path: Path, monkeypatch) -> None:
    from ordenia.services.file_service import FileService

    watched = tmp_path / "watched"
    watched.mkdir()
    original = watched / "example.pdf"
    original.write_text("one file")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(watched)
    file = repository.upsert_file(folder.id, original, original.stat().st_size, "Documentos", "2026-01-01T00:00:00+00:00")
    service = FileService(repository)
    try:
        service._organize(file.id)
        operation = repository.list_operations()[0]
        real_record_undo = repository.record_undo

        def delayed_record_undo(*args) -> None:
            time.sleep(1.2)
            real_record_undo(*args)

        monkeypatch.setattr(repository, "record_undo", delayed_record_undo)
        service._undo(operation.id)
        time.sleep(1)
        files = repository.list_files()
        assert len(files) == 1
        assert files[0].id == file.id
        assert files[0].path == original
        assert repository.get_operation(operation.id).status == "Deshecho"
    finally:
        service.close()


def test_ui_paths_copy_full_value_and_refresh_after_move(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ordenia.services.file_service import FileService
    from ordenia.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    watched = tmp_path / "TestOrdenIA"
    watched.mkdir()
    source = watched / "Documento Hola Mundo.pdf"
    source.write_text("content")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(watched)
    file = repository.upsert_file(folder.id, source, source.stat().st_size, "Documentos", "2026-01-01T00:00:00+00:00")
    service = FileService(repository)
    window = MainWindow(repository, service)
    try:
        files_page = window.pages[1]
        origin_item = files_page.table.item(0, 4)
        assert origin_item.toolTip() == str(watched)
        assert files_page.table.item(0, 0).toolTip() == source.name
        files_page.table.cellClicked.emit(0, 4)
        assert app.clipboard().text() == str(watched)
        assert window.statusBar().currentMessage() == "Ruta copiada al portapapeles"

        service.organize(file.id)
        deadline = time.monotonic() + 8
        while files_page.table.item(0, 6).text() != "Organizado" and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        app.processEvents()
        destination = repository.get_file(file.id).path
        assert files_page.table.item(0, 6).text() == "Organizado"
        assert window.pages[0].values["Archivos organizados"].text() == "1"
        history = window.pages[3]
        assert history.table.rowCount() == 1
        assert history.table.item(0, 4).text() == "Completado"
        assert history.table.item(0, 2).toolTip() == str(source)
        assert history.table.item(0, 3).toolTip() == str(destination)
        history.table.cellClicked.emit(0, 3)
        assert app.clipboard().text() == str(destination)

        service.undo(repository.list_operations()[0].id)
        deadline = time.monotonic() + 8
        while files_page.table.item(0, 6).text() != "Pendiente" and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        app.processEvents()
        assert files_page.table.item(0, 6).text() == "Pendiente"
        assert window.pages[0].values["Archivos organizados"].text() == "0"
        assert history.table.item(1, 4).text() == "Deshecho"
    finally:
        window.close()

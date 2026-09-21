from pathlib import Path
import time

from ordenia.database.repositories import Repository


def test_ui_search_filters_and_details(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ordenia.services.file_service import FileService
    from ordenia.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    root = tmp_path / "watched"
    root.mkdir()
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    for name, category in (("Proyecto A.pdf", "Documentos"), ("proyecto B.jpg", "Imágenes"), ("Otro.pdf", "Documentos")):
        path = root / name
        path.write_text(name)
        repository.upsert_file(folder.id, path, path.stat().st_size, category, "2026-01-01T00:00:00+00:00")
    service = FileService(repository)
    window = MainWindow(repository, service)
    try:
        page = window.pages[1]
        page.search_box.setText("PROYECTO")
        page.category_filter.setCurrentText("Documentos")
        page.status_filter.setCurrentText("Pendientes")
        assert page.table.rowCount() == 1
        assert page.table.item(0, 0).text() == "Proyecto A.pdf"
        page.table.setCurrentCell(0, 0)
        assert page.detail_values["Nombre completo"].text() == "Proyecto A.pdf"
        assert page.detail_values["Destino sugerido"].text().endswith("Proyecto A.pdf")
        page.clear_filters()
        assert page.table.rowCount() == 3
        page.table.horizontalHeader().sectionClicked.emit(0)
        assert page.table.item(0, 0).text() == "Otro.pdf"
        assert window.windowTitle() == "OrdenIA · V0.3.1"
        app.processEvents()
    finally:
        window.close()


def test_adding_folder_can_scan_existing_files_asynchronously(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ordenia.services.file_service import FileService
    from ordenia.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    root = tmp_path / "watched"
    root.mkdir()
    (root / "before.pdf").write_text("old")
    repository = Repository(tmp_path / "data.sqlite3")
    repository.set_setting("ask_before_scan", "0")
    service = FileService(repository)
    window = MainWindow(repository, service)
    try:
        assert not repository.list_folders()
        service.add_folder(root)
        deadline = time.monotonic() + 8
        while (len(repository.list_files()) < 1 or window.pages[1].table.rowCount() < 1) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        app.processEvents()
        assert len(repository.list_files()) == 1
        assert repository.list_files()[0].status == "Pendiente"
        assert (root / "before.pdf").exists()
        assert window.pages[1].table.rowCount() == 1
        assert window.pages[0].values["Pendientes de revisar"].text() == "1"
    finally:
        window.close()


def test_first_use_button_opens_real_add_folder_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ordenia.services.file_service import FileService
    from ordenia.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    repository = Repository(tmp_path / "data.sqlite3")
    service = FileService(repository)
    window = MainWindow(repository, service)
    calls: list[str] = []
    monkeypatch.setattr(window.pages[2], "_add", lambda: calls.append("add"))
    try:
        window.pages[0].add_folder_requested.emit()
        assert window.navigation.currentRow() == 2
        assert calls == ["add"]
        app.processEvents()
    finally:
        window.close()


def test_installer_double_click_requires_confirmation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from ordenia.services.file_service import FileService
    from ordenia.ui.main_window import MainWindow
    from ordenia.ui.pages import files as files_module

    app = QApplication.instance() or QApplication([])
    root = tmp_path / "watched"
    root.mkdir()
    installer = root / "setup.exe"
    installer.write_text("placeholder")
    repository = Repository(tmp_path / "data.sqlite3")
    folder = repository.add_folder(root)
    repository.upsert_file(folder.id, installer, installer.stat().st_size, "Instaladores", "2026-01-01T00:00:00+00:00")
    service = FileService(repository)
    window = MainWindow(repository, service)
    calls: list[Path] = []
    monkeypatch.setattr(files_module, "open_file", calls.append)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.No)
    try:
        page = window.pages[1]
        page.table.setCurrentCell(0, 0)
        page._open_selected_file()
        assert calls == []
        app.processEvents()
    finally:
        window.close()

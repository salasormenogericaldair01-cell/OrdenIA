"""Dashboard sizing and visible destination choices in the desktop UI."""

from pathlib import Path

from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QScrollArea

from ordenia.analysis.models import AnalysisOutcome
from ordenia.core.destinations import CENTRAL, CUSTOM, INSIDE
from ordenia.database.repositories import Repository
from ordenia.services.file_service import FileService
from ordenia.ui.main_window import MainWindow
from ordenia.ui.widgets.folder_options import FolderOptionsDialog


def test_home_content_is_not_compressed_at_common_window_sizes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "watched"
    root.mkdir()
    repository = Repository(tmp_path / "index.sqlite3")
    folder = repository.add_folder(root)
    for index, category in enumerate(("Documentos", "Imágenes", "Videos", "Hojas de cálculo", "Comprimidos", "Otros")):
        path = root / f"sample-{index}.txt"
        path.write_text("demo")
        file = repository.upsert_file(folder.id, path, 4, category, "2026-09-20", path.stat().st_mtime_ns)
        repository.content.save(file.id, AnalysisOutcome("indexed", text="demo"), 4, path.stat().st_mtime_ns)

    service = FileService(repository)
    window = MainWindow(repository, service)
    try:
        home = window.pages[0]
        scroll = home.findChild(QScrollArea)
        assert scroll is not None
        for width, height in ((820, 480), (1366, 768), (1600, 900), (1920, 1080)):
            window.resize(width, height)
            window.show()
            app.processEvents()
            labels = (*home.values.values(), *home.category_values.values(),
                      *home.content_values.values(), home.activity)
            assert all(label.height() >= label.sizeHint().height() for label in labels)
            assert scroll.widget().height() >= scroll.viewport().height()
            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
            app.processEvents()
            assert home.activity.isVisible()
            settings = window.pages[4]
            settings_scroll = settings.findChild(QScrollArea)
            assert settings_scroll is not None
            window.navigation.setCurrentRow(4)
            app.processEvents()
            assert settings_scroll.horizontalScrollBar().maximum() == 0
            short_labels = [(label.text(), label.height(), label.sizeHint().height())
                            for label in settings.findChildren(QLabel)
                            if label.isVisible() and not label.wordWrap()
                            and label.height() < label.sizeHint().height()]
            assert not short_labels
            window.navigation.setCurrentRow(0)
            for page_index in (1, 2):
                page = window.pages[page_index]
                assert page.minimumSizeHint().width() <= page.width()
                assert page.minimumSizeHint().height() <= page.height()
    finally:
        window.close()


def test_folder_dialog_shows_all_destination_paths_and_keeps_saved_choice(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    watched = tmp_path / "watched"
    watched.mkdir()
    central = tmp_path / "central"
    custom = tmp_path / "custom"
    custom.mkdir()
    repository = Repository(tmp_path / "index.sqlite3")
    existing = repository.add_folder(watched, False, CUSTOM, custom)
    dialog = FolderOptionsDialog(watched, existing, central_root=central)
    try:
        dialog.show()
        app.processEvents()
        assert len(dialog.strategy_buttons) == 3
        assert dialog.strategy_buttons[CUSTOM].isChecked()
        assert dialog.values() == (False, CUSTOM, custom)
        assert str(custom / "Documentos" / "archivo.pdf") in dialog.preview.text()

        dialog.strategy_buttons[CENTRAL].setChecked(True)
        assert dialog.values() == (False, CENTRAL, None)
        assert str(central / "Documentos" / "archivo.pdf") in dialog.preview.text()
        dialog.strategy_buttons[INSIDE].setChecked(True)
        assert str(watched / "OrdenIA" / "Documentos" / "archivo.pdf") in dialog.preview.text()
        dialog.reject()
        assert repository.get_folder(existing.id).destination_strategy == CUSTOM
    finally:
        dialog.close()


def test_add_and_edit_folder_use_the_visible_central_and_custom_choices(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    watched = tmp_path / "Downloads"
    watched.mkdir()
    central = tmp_path / "Documents" / "OrdenIA"
    custom = tmp_path / "custom"
    custom.mkdir()
    repository = Repository(tmp_path / "index.sqlite3")
    repository.set_setting("central_destination", str(central))
    repository.set_setting("ask_before_scan", "0")
    service = FileService(repository)
    window = MainWindow(repository, service)
    attempts = 0

    def choose_destination(dialog: FolderOptionsDialog) -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            dialog.strategy_buttons[CENTRAL].setChecked(True)
            assert str(central) in dialog.preview.text()
        else:
            assert dialog.strategy_buttons[CENTRAL].isChecked()
            dialog.strategy_buttons[CUSTOM].setChecked(True)
            dialog.custom_path.setText(str(custom))
        return 1

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: str(watched))
    monkeypatch.setattr(FolderOptionsDialog, "exec", choose_destination)
    try:
        assert window.pages[4].central_path.text() == str(central)
        folder_page = window.pages[2]
        folder_page._add()
        folder = repository.list_folders()[0]
        assert folder.destination_strategy == CENTRAL
        assert service.destination_for(folder) == central
        folder_page.refresh()
        folder_page.table.setCurrentCell(0, 0)
        folder_page._edit()
        updated = repository.get_folder(folder.id)
        assert updated.destination_strategy == CUSTOM
        assert updated.custom_destination == custom
        assert service.destination_for(updated) == custom
        assert attempts == 2
        app.processEvents()
    finally:
        window.close()

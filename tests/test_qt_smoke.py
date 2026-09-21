"""The application window starts and stops its workers cleanly."""

from pathlib import Path


def test_main_window_open_close_offscreen(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from ordenia.database.repositories import Repository
    from ordenia.services.file_service import FileService
    from ordenia.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    repository = Repository(tmp_path / "app.sqlite3")
    service = FileService(repository)
    window = MainWindow(repository, service)
    try:
        window.show()
        app.processEvents()
        assert window.isVisible()
    finally:
        window.close()
        app.processEvents()
    assert not window.isVisible()

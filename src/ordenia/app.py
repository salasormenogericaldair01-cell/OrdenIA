"""Configuración e inicio de la aplicación de escritorio."""

import logging
import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ordenia import __version__
from ordenia.database.repositories import Repository
from ordenia.services.file_service import FileService
from ordenia.ui.main_window import MainWindow


def data_directory() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / "AppData" / "Local") / "OrdenIA"


def run() -> int:
    directory = data_directory()
    directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=directory / "ordenia.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = QApplication(sys.argv)
    app.setApplicationName("OrdenIA")
    app.setApplicationVersion(__version__)
    repository = Repository(directory / "ordenia.sqlite3")
    service = FileService(repository)
    window = MainWindow(repository, service)
    window.show()
    try:
        return app.exec()
    finally:
        if window.isVisible():
            window.close()
        logging.shutdown()

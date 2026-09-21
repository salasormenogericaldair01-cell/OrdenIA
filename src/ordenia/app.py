"""Configuración e inicio de la aplicación de escritorio."""

import logging
import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ordenia import __version__
from ordenia.database.repositories import Repository
from ordenia.platform.runtime import data_directory
from ordenia.services.file_service import FileService
from ordenia.smoke import prepare_legacy_database, run_smoke
from ordenia.ui.main_window import MainWindow


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--smoke-report", type=Path)
    parser.add_argument("--data-dir", type=Path)
    options, qt_arguments = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    if options.smoke_test and options.smoke_report is None:
        raise ValueError("--smoke-report es obligatorio con --smoke-test")
    directory = options.data_dir or data_directory()
    directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=directory / "ordenia.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = QApplication([sys.argv[0], *qt_arguments])
    app.setApplicationName("OrdenIA")
    app.setApplicationVersion(__version__)
    database = directory / "ordenia.sqlite3"
    if options.smoke_test:
        prepare_legacy_database(database, directory)
    repository = Repository(database)
    service = FileService(repository)
    window = MainWindow(repository, service)
    window.show()
    try:
        if options.smoke_test:
            assert options.smoke_report is not None
            return run_smoke(app, window, repository, directory, options.smoke_report)
        return app.exec()
    finally:
        if window.isVisible():
            window.close()
        logging.shutdown()

"""Coordina UI, SQLite, watcher y movimientos sin bloquear la ventana."""

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ordenia.core.classifier import ExtensionClassifier
from ordenia.core.organizer import Organizer, verify_transfer
from ordenia.database.models import DetectedFile, Operation
from ordenia.database.repositories import Repository
from ordenia.monitoring.watcher import FolderWatcher

logger = logging.getLogger(__name__)


class FileService(QObject):
    changed = Signal()
    error = Signal(str)
    operation_finished = Signal(str)

    def __init__(self, repository: Repository) -> None:
        super().__init__()
        self.repository = repository
        self.organizer = Organizer()
        self.watcher = FolderWatcher(repository, ExtensionClassifier(), self.changed.emit)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="OrdenIA moves")
        self.watcher.start()

    def add_folder(self, path: Path) -> None:
        self.repository.add_folder(path)
        self.watcher.refresh()
        self.changed.emit()

    def remove_folder(self, folder_id: int) -> None:
        self.repository.remove_folder(folder_id)
        self.watcher.refresh()
        self.changed.emit()

    def set_folder_enabled(self, folder_id: int, enabled: bool) -> None:
        self.repository.set_folder_enabled(folder_id, enabled)
        self.watcher.refresh()
        self.changed.emit()

    def proposal(self, file: DetectedFile) -> Path:
        folder = self.repository.get_folder(file.watched_folder_id)
        return self.organizer.proposed_destination(file.path, folder.path, file.category)

    def organize(self, file_id: int) -> None:
        self.executor.submit(self._organize, file_id)

    def _organize(self, file_id: int) -> None:
        file: DetectedFile | None = None
        planned: Path | None = None
        destination: Path | None = None
        failure: str | None = None
        # Watchdog may queue events, but cannot save them between the physical
        # move and the matching SQLite transaction.
        with self.watcher.hold_events():
            try:
                file = self.repository.get_file(file_id)
                if file.status != "Pendiente":
                    raise ValueError("Solo se pueden organizar archivos pendientes.")
                folder = self.repository.get_folder(file.watched_folder_id)
                planned = self.organizer.proposed_destination(file.path, folder.path, file.category)
                destination = self.organizer.move(file.path, folder.path, file.category)
                verify_transfer(file.path, destination)
                self.repository.record_move(file.id, file.path, destination)
            except Exception as exc:
                logger.exception("Error al organizar el archivo %s", file_id)
                failure = f"No se pudo organizar el archivo: {exc}"
                if file is not None and planned is not None:
                    if destination is not None and destination.is_file() and not file.path.exists():
                        try:
                            restored = self.organizer.undo(destination, file.path)
                            verify_transfer(destination, restored)
                            if restored != file.path:
                                self.repository.repair_pending_path(file.id, restored)
                        except Exception:
                            logger.exception("No se pudo revertir el movimiento tras el error")
                            failure += " Revisa el destino: la reversión también falló."
                    try:
                        self.repository.record_failed_move(file.id, file.path, destination or planned, str(exc))
                    except Exception:
                        logger.exception("No se pudo registrar la operación fallida")
                        failure += " No se pudo guardar el error en el historial."
        self.changed.emit()
        if failure is not None:
            self.error.emit(failure)
        else:
            assert destination is not None
            self.operation_finished.emit(f"Archivo organizado en {destination}")

    def undo(self, operation_id: int) -> None:
        self.executor.submit(self._undo, operation_id)

    def _undo(self, operation_id: int) -> None:
        operation: Operation | None = None
        restored: Path | None = None
        failure: str | None = None
        with self.watcher.hold_events():
            try:
                operation = self.repository.get_operation(operation_id)
                if operation.operation_type != "move" or operation.status != "Completado":
                    raise ValueError("Esta operación no puede deshacerse.")
                file = self.repository.get_file(operation.file_id)
                if file.path != operation.destination_path:
                    raise ValueError("El archivo ya no está en el destino de esta operación.")
                restored = self.organizer.undo(operation.destination_path, operation.original_path)
                verify_transfer(operation.destination_path, restored)
                self.repository.record_undo(operation, restored)
            except Exception as exc:
                logger.exception("Error al deshacer la operación %s", operation_id)
                failure = f"No se pudo deshacer el movimiento: {exc}"
                if operation is not None and operation.status == "Completado":
                    if restored is not None and restored.is_file() and not operation.destination_path.exists():
                        try:
                            returned = self.organizer.undo(restored, operation.destination_path)
                            verify_transfer(restored, returned)
                        except Exception:
                            logger.exception("No se pudo revertir el deshacer tras el error")
                            failure += " Revisa ambas ubicaciones: la reversión también falló."
                    try:
                        self.repository.record_failed_undo(operation, str(exc))
                    except Exception:
                        logger.exception("No se pudo registrar el deshacer fallido")
                        failure += " No se pudo guardar el error en el historial."
        self.changed.emit()
        if failure is not None:
            self.error.emit(failure)
        else:
            assert restored is not None
            self.operation_finished.emit(f"Archivo restaurado en {restored}")

    def set_ignored(self, file_id: int) -> None:
        self.repository.set_file_status(file_id, "Ignorado")
        self.changed.emit()

    def close(self) -> None:
        self.watcher.stop()
        self.executor.shutdown(wait=True)

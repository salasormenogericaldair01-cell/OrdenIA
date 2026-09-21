"""Coordina UI, SQLite, watcher y movimientos sin bloquear la ventana."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ordenia.core.classifier import ExtensionClassifier
from ordenia.core.destinations import destination_root
from ordenia.core.organizer import Organizer, verify_transfer
from ordenia.database.models import DetectedFile, IndexedEntry, Operation, ScanSummary, WatchedFolder
from ordenia.database.repositories import Repository
from ordenia.monitoring.watcher import FolderWatcher
from ordenia.platform.actions import default_central_root
from ordenia.scanning.scanner import FileScanner
from ordenia.services.content_service import ContentService
from ordenia.services.file_locks import FileOperationLocks

logger = logging.getLogger(__name__)


class FileService(QObject):
    changed = Signal()
    error = Signal(str)
    operation_finished = Signal(str)
    scan_offer = Signal(int, int, bool)
    scan_count_finished = Signal(int, int)
    scan_progress = Signal(int, int, int)
    scan_finished = Signal(int, object)
    scan_failed = Signal(int, str)

    def __init__(self, repository: Repository) -> None:
        super().__init__()
        self.repository = repository
        self.organizer = Organizer()
        self.classifier = ExtensionClassifier()
        self.scanner = FileScanner()
        self._closing = threading.Event()
        self.scan_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="OrdenIA scans")
        self.file_locks = FileOperationLocks()
        self.content = ContentService(repository, operation_locks=self.file_locks)
        self.content.changed.connect(self.changed.emit)
        self.watcher = FolderWatcher(repository, ExtensionClassifier(), self.changed.emit, self._on_watcher_indexed)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="OrdenIA moves")
        for folder in repository.list_folders():
            try:
                repository.register_managed_root(self.destination_for(folder))
            except ValueError:
                logger.warning("Destino no válido para %s", folder.path, exc_info=True)
        for folder in repository.list_folders():
            repository.reconcile_folder(folder, self._scan_roots(folder), check_missing=False)
        self.watcher.start()

    def central_destination(self) -> Path:
        return Path(self.repository.get_setting("central_destination", str(default_central_root()))).expanduser().resolve()

    def destination_for(self, folder: WatchedFolder) -> Path:
        return destination_root(folder.path, folder.destination_strategy, self.central_destination(), folder.custom_destination)

    def add_folder(
        self, path: Path, include_subfolders: bool = True,
        destination_strategy: str = "inside", custom_destination: Path | None = None,
        offer_scan: bool = True,
    ) -> WatchedFolder:
        root = path.expanduser().resolve()
        destination = destination_root(root, destination_strategy, self.central_destination(), custom_destination)
        folder = self.repository.add_folder(root, include_subfolders, destination_strategy, custom_destination)
        self.repository.register_managed_root(destination)
        self.watcher.refresh()
        self.changed.emit()
        if offer_scan:
            self.offer_existing_scan(folder.id, remove_on_cancel=True)
        return folder

    def update_folder(
        self, folder_id: int, include_subfolders: bool,
        destination_strategy: str, custom_destination: Path | None,
    ) -> WatchedFolder:
        current = self.repository.get_folder(folder_id)
        destination = destination_root(current.path, destination_strategy, self.central_destination(), custom_destination)
        folder = self.repository.update_folder(folder_id, include_subfolders, destination_strategy, custom_destination)
        self.repository.register_managed_root(destination)
        self.watcher.refresh()
        self.changed.emit()
        return folder

    def set_central_destination(self, path: Path) -> None:
        root = path.expanduser().resolve()
        for folder in self.repository.list_folders():
            if folder.destination_strategy == "central":
                destination_root(folder.path, "central", root)
        self.repository.set_setting("central_destination", str(root))
        self.repository.register_managed_root(root)
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
        return self.organizer.proposed_destination(file.path, folder.path, file.category, self.destination_for(folder))

    def _scan_roots(self, folder: WatchedFolder) -> tuple[Path, ...]:
        return self.repository.list_managed_roots() + tuple(item.path / "OrdenIA" for item in self.repository.list_folders()) + (folder.path / "OrdenIA",)

    def count_existing(self, folder_id: int) -> int:
        folder = self.repository.get_folder(folder_id)
        return self.scanner.count(folder.path, folder.include_subfolders, self._scan_roots(folder))

    def offer_existing_scan(self, folder_id: int, remove_on_cancel: bool = False) -> None:
        self.scan_executor.submit(self._offer_existing_scan, folder_id, remove_on_cancel)

    def _offer_existing_scan(self, folder_id: int, remove_on_cancel: bool) -> None:
        try:
            count = self.count_existing(folder_id)
            if self._closing.is_set():
                return
            if not self.repository.is_folder_listed(folder_id):
                return
            self.scan_count_finished.emit(folder_id, count)
            if not count:
                return
            if self.repository.get_setting("ask_before_scan", "1") == "1":
                self.scan_offer.emit(folder_id, count, remove_on_cancel)
            else:
                self.scan_folder(folder_id)
        except Exception as exc:
            logger.exception("No se pudo contar archivos existentes")
            self.scan_failed.emit(folder_id, str(exc))
            self.error.emit(f"No se pudieron contar los archivos: {exc}")

    def scan_folder(self, folder_id: int) -> None:
        self.scan_executor.submit(self._scan_folder, folder_id)

    def _save_scan_batch(self, folder_id: int, entries: list[IndexedEntry]) -> tuple[int, int] | None:
        # An entry can disappear after stat() and before this batch is written.
        # Share the movement lock, then recheck before touching SQLite.
        with self.watcher.hold_events():
            if not self.repository.is_folder_listed(folder_id):
                return None
            folder = self.repository.get_folder(folder_id)
            roots = self._scan_roots(folder)
            valid = [entry for entry in entries if entry.path.is_file()
                     and self.scanner.policy.eligible_path(entry.path, folder.path, folder.include_subfolders, roots)]
            result = self.repository.upsert_files_batch(folder_id, valid)
            if self.repository.get_setting("auto_analyze_content", "0") == "1":
                self.content.request(self.repository.ids_for_paths([entry.path for entry in valid]), automatic=True)
            return result

    def _on_watcher_indexed(self, file_id: int) -> None:
        self.content.request([file_id], automatic=True)

    def analyze_content(self, file_ids: list[int], *, force: bool = False) -> int:
        return self.content.request(file_ids, force=force)

    def cancel_content(self, job_id: int) -> None:
        self.content.cancel(job_id)

    def _scan_folder(self, folder_id: int) -> None:
        try:
            folder = self.repository.get_folder(folder_id)
            if not self.repository.is_folder_listed(folder_id):
                return
            roots = self._scan_roots(folder)
            total = self.scanner.count(folder.path, folder.include_subfolders, roots)
            batch_size = 50 if total <= 500 else 200
            self.scan_progress.emit(folder_id, 0, total)
            processed = 0
            new = existing = 0
            batch: list[IndexedEntry] = []
            for path in self.scanner.iter_paths(folder.path, folder.include_subfolders, roots):
                if self._closing.is_set():
                    return
                try:
                    stat = path.stat()
                    modified = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
                    batch.append(IndexedEntry(path, stat.st_size, self.classifier.classify(path), modified, stat.st_mtime_ns))
                except OSError:
                    logger.warning("Archivo omitido durante el escaneo: %s", path, exc_info=True)
                    continue
                processed += 1
                if len(batch) >= batch_size:
                    result = self._save_scan_batch(folder_id, batch)
                    if result is None:
                        return
                    new += result[0]
                    existing += result[1]
                    batch.clear()
                    self.scan_progress.emit(folder_id, processed, total)
                    if total <= 500 or processed % 1000 == 0:
                        self.changed.emit()
            if batch:
                result = self._save_scan_batch(folder_id, batch)
                if result is None:
                    return
                new += result[0]
                existing += result[1]
                self.changed.emit()
            with self.watcher.hold_events():
                counts = self.repository.reconcile_folder(folder, self._scan_roots(folder))
            self.scan_progress.emit(folder_id, processed, total)
            self.scan_finished.emit(folder_id, ScanSummary(processed, new, existing,
                                                          counts["active"], counts["missing"], counts["excluded"]))
            self.changed.emit()
        except Exception as exc:
            logger.exception("Error al analizar carpeta %s", folder_id)
            self.scan_failed.emit(folder_id, str(exc))
            self.error.emit(f"No se pudo analizar la carpeta: {exc}")

    def organize(self, file_id: int) -> None:
        self.executor.submit(self._organize, file_id)

    def _organize(self, file_id: int) -> None:
        with self.file_locks.hold(file_id):
            self._organize_locked(file_id)

    def _organize_locked(self, file_id: int) -> None:
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
                root = self.destination_for(folder)
                planned = self.organizer.proposed_destination(file.path, folder.path, file.category, root)
                destination = self.organizer.move(file.path, folder.path, file.category, root)
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
        try:
            operation = self.repository.get_operation(operation_id)
        except Exception as exc:
            logger.exception("No se pudo cargar la operación %s para deshacer", operation_id)
            self.error.emit(f"No se pudo deshacer el movimiento: {exc}")
            return
        with self.file_locks.hold(operation.file_id):
            self._undo_locked(operation_id)

    def _undo_locked(self, operation_id: int) -> None:
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
        self._closing.set()
        self.watcher.stop()
        self.scan_executor.shutdown(wait=True)
        self.executor.shutdown(wait=True)
        self.content.close()

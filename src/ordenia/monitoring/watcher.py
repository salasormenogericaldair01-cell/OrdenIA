"""watchdog en segundo plano; el trabajo de análisis se hace fuera de Qt."""

import logging
import queue
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import ObservedWatch

from ordenia.core.classifier import Classifier
from ordenia.core.exclusions import ExclusionPolicy
from ordenia.database.models import IndexedEntry, WatchedFolder
from ordenia.database.repositories import Repository

logger = logging.getLogger(__name__)


class _Handler(FileSystemEventHandler):
    def __init__(self, folder: WatchedFolder, enqueue: Callable[[WatchedFolder, Path], None],
                 enqueue_move: Callable[[WatchedFolder, Path, Path], None]) -> None:
        self.folder = folder
        self.enqueue = enqueue
        self.enqueue_move = enqueue_move

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.enqueue(self.folder, Path(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.enqueue(self.folder, Path(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.enqueue_move(self.folder, Path(event.src_path), Path(event.dest_path))


class FolderWatcher:
    def __init__(self, repository: Repository, classifier: Classifier, on_file: Callable[[], None]) -> None:
        self.repository = repository
        self.classifier = classifier
        self.policy = ExclusionPolicy()
        self.on_file = on_file
        self.observer = Observer()
        self._watches: dict[int, tuple[ObservedWatch, WatchedFolder]] = {}
        self._managed_roots: tuple[Path, ...] = ()
        self._queue: queue.Queue[tuple[WatchedFolder, Path] | tuple[WatchedFolder, Path, Path] | None] = queue.Queue()
        self._pending: set[Path] = set()
        self._lock = threading.Lock()
        self._processing_lock = threading.RLock()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, name="OrdenIA file analyzer", daemon=True)

    def start(self) -> None:
        self.observer.start()
        self._worker.start()
        self.refresh()

    def refresh(self) -> None:
        all_folders = self.repository.list_folders()
        self._managed_roots = self.repository.list_managed_roots() + tuple(folder.path / "OrdenIA" for folder in all_folders)
        folders = {folder.id: folder for folder in all_folders if folder.enabled and folder.path.is_dir()}
        for folder_id, (watch, previous) in list(self._watches.items()):
            if folder_id not in folders or folders[folder_id] != previous:
                self.observer.unschedule(watch)
                del self._watches[folder_id]
        for folder_id, folder in folders.items():
            if folder_id not in self._watches:
                watch = self.observer.schedule(_Handler(folder, self._enqueue, self._enqueue_move), str(folder.path), recursive=folder.include_subfolders)
                self._watches[folder_id] = (watch, folder)

    def _enqueue(self, folder: WatchedFolder, path: Path) -> None:
        try:
            if path.is_symlink():
                return
            path = path.resolve()
        except OSError:
            logger.warning("No se pudo normalizar el evento %s", path, exc_info=True)
            return
        if not self.policy.eligible_path(path, folder.path, folder.include_subfolders, self._managed_roots):
            return
        with self._lock:
            if path in self._pending:
                return
            self._pending.add(path)
        self._queue.put((folder, path))

    def _enqueue_move(self, folder: WatchedFolder, source: Path, destination: Path) -> None:
        # Keep both ends: a rename must update the old row, not create a second one.
        self._queue.put((folder, source.absolute(), destination.absolute()))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            if item is None:
                break
            folder, path = item[:2]
            try:
                with self._processing_lock:
                    current = self.repository.get_folder(folder.id)
                    if not current.enabled:
                        continue
                    if len(item) == 3:
                        self._process_move(current, path, item[2])
                    elif self.policy.eligible_path(path, current.path, current.include_subfolders, self._managed_roots) and self._wait_stable(path):
                        stat = path.stat()
                        modified = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
                        self.repository.upsert_file(folder.id, path, stat.st_size, self.classifier.classify(path), modified)
                        self.on_file()
            except (OSError, LookupError, ValueError):
                logger.exception("No se pudo analizar %s", path)
            finally:
                if len(item) == 2:
                    with self._lock:
                        self._pending.discard(path)

    def _process_move(self, folder: WatchedFolder, source: Path, destination: Path) -> None:
        eligible = self.policy.eligible_path(destination, folder.path, folder.include_subfolders, self._managed_roots)
        entry = None
        if eligible and self._wait_stable(destination):
            stat = destination.stat()
            modified = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
            entry = IndexedEntry(destination, stat.st_size, self.classifier.classify(destination), modified)
        handled = self.repository.reconcile_external_move(folder, source, destination, self._managed_roots, entry)
        if entry is not None:
            self.repository.upsert_file(folder.id, entry.path, entry.size, entry.category, entry.modified_at)
        if handled or entry is not None:
            self.on_file()

    @contextmanager
    def hold_events(self) -> Iterator[None]:
        """Keep queued events from being persisted during an internal move."""
        with self._processing_lock:
            yield

    def _wait_stable(self, path: Path) -> bool:
        previous: tuple[int, int] | None = None
        stable_checks = 0
        for _ in range(25):
            if self._stop.wait(0.25):
                return False
            try:
                if not path.is_file() or path.is_symlink():
                    return False
                stat = path.stat()
            except OSError:
                return False
            current = (stat.st_size, stat.st_mtime_ns)
            stable_checks = stable_checks + 1 if current == previous else 0
            if stable_checks >= 2:
                return True
            previous = current
        logger.warning("Archivo aún en escritura; se omitió por ahora: %s", path)
        return False

    def stop(self) -> None:
        self._stop.set()
        self.observer.stop()
        self._queue.put(None)
        if self.observer.is_alive():
            self.observer.join(timeout=5)
        if self._worker.is_alive():
            self._worker.join(timeout=5)

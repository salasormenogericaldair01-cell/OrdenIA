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
from ordenia.core.file_utils import is_inside, is_temporary
from ordenia.database.models import WatchedFolder
from ordenia.database.repositories import Repository

logger = logging.getLogger(__name__)


class _Handler(FileSystemEventHandler):
    def __init__(self, folder: WatchedFolder, enqueue: Callable[[WatchedFolder, Path], None]) -> None:
        self.folder = folder
        self.enqueue = enqueue

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.enqueue(self.folder, Path(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.enqueue(self.folder, Path(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.enqueue(self.folder, Path(event.dest_path))


class FolderWatcher:
    def __init__(self, repository: Repository, classifier: Classifier, on_file: Callable[[], None]) -> None:
        self.repository = repository
        self.classifier = classifier
        self.on_file = on_file
        self.observer = Observer()
        self._watches: dict[int, ObservedWatch] = {}
        self._queue: queue.Queue[tuple[WatchedFolder, Path] | None] = queue.Queue()
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
        folders = {folder.id: folder for folder in self.repository.list_folders() if folder.enabled and folder.path.is_dir()}
        for folder_id, watch in list(self._watches.items()):
            if folder_id not in folders:
                self.observer.unschedule(watch)
                del self._watches[folder_id]
        for folder_id, folder in folders.items():
            if folder_id not in self._watches:
                self._watches[folder_id] = self.observer.schedule(_Handler(folder, self._enqueue), str(folder.path), recursive=True)

    def _enqueue(self, folder: WatchedFolder, path: Path) -> None:
        if is_temporary(path) or is_inside(path, folder.path / "OrdenIA"):
            return
        with self._lock:
            if path in self._pending:
                return
            self._pending.add(path)
        self._queue.put((folder, path))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            if item is None:
                break
            folder, path = item
            try:
                with self._processing_lock:
                    current = self.repository.get_folder(folder.id)
                    if current.enabled and self._wait_stable(path):
                        stat = path.stat()
                        modified = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
                        self.repository.upsert_file(folder.id, path, stat.st_size, self.classifier.classify(path), modified)
                        self.on_file()
            except (OSError, LookupError, ValueError):
                logger.exception("No se pudo analizar %s", path)
            finally:
                with self._lock:
                    self._pending.discard(path)

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

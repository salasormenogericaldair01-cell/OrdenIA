"""watchdog en segundo plano; el trabajo de análisis se hace fuera de Qt."""

import logging
import heapq
import queue
import threading
import time
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

STABILITY_RETRY_DELAYS = (2.0, 5.0, 10.0, 20.0)


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
    def __init__(self, repository: Repository, classifier: Classifier, on_file: Callable[[], None],
                 on_indexed: Callable[[int], None] | None = None,
                 retry_delays: tuple[float, ...] = STABILITY_RETRY_DELAYS) -> None:
        self.repository = repository
        self.classifier = classifier
        self.policy = ExclusionPolicy()
        self.on_file = on_file
        self.on_indexed = on_indexed
        self.observer = Observer()
        self._watches: dict[int, tuple[ObservedWatch, WatchedFolder]] = {}
        self._managed_roots: tuple[Path, ...] = ()
        self._queue: queue.Queue[tuple[WatchedFolder, Path] | tuple[WatchedFolder, Path, Path] | None] = queue.Queue()
        self._pending: set[Path] = set()
        self._pending_moves: set[tuple[Path, Path]] = set()
        self._retry_attempts: dict[Path | tuple[Path, Path], int] = {}
        self._retry_delays = retry_delays
        self._retry_heap: list[tuple[float, int, WatchedFolder, Path, Path | None]] = []
        self._retry_sequence = 0
        self._lock = threading.Lock()
        self._retry_condition = threading.Condition(self._lock)
        self._processing_lock = threading.RLock()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, name="OrdenIA file analyzer", daemon=True)
        self._retry_worker = threading.Thread(target=self._run_retries, name="OrdenIA stability retries", daemon=True)

    def start(self) -> None:
        # Register directories before observers begin consuming filesystem events.
        self.refresh()
        self.observer.start()
        self._retry_worker.start()
        self._worker.start()

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

    def _schedule_retry(self, folder: WatchedFolder, path: Path, source: Path | None = None) -> bool:
        key: Path | tuple[Path, Path] = path if source is None else (source, path)
        with self._retry_condition:
            attempt = self._retry_attempts.get(key, 0)
            if self._stop.is_set() or attempt >= len(self._retry_delays):
                return False
            delay = self._retry_delays[attempt]
            self._retry_attempts[key] = attempt + 1
            self._retry_sequence += 1
            heapq.heappush(self._retry_heap, (time.monotonic() + delay, self._retry_sequence,
                                              folder, path, source))
            self._retry_condition.notify()
        logger.info("Archivo aún en escritura; reintento %s/%s en %.1fs: %s",
                    attempt + 1, len(self._retry_delays), delay, path)
        return True

    def _run_retries(self) -> None:
        while not self._stop.is_set():
            with self._retry_condition:
                while not self._retry_heap and not self._stop.is_set():
                    self._retry_condition.wait()
                if self._stop.is_set():
                    return
                due, _, folder, path, source = self._retry_heap[0]
                remaining = due - time.monotonic()
                if remaining > 0:
                    self._retry_condition.wait(remaining)
                    continue
                heapq.heappop(self._retry_heap)
            self._queue.put((folder, path) if source is None else (folder, source, path))

    def _enqueue_move(self, folder: WatchedFolder, source: Path, destination: Path) -> None:
        # Keep both ends: a rename must update the old row, not create a second one.
        key = (source.absolute(), destination.absolute())
        with self._lock:
            if key in self._pending_moves:
                return
            self._pending_moves.add(key)
        self._queue.put((folder, *key))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            if item is None:
                break
            folder, path = item[:2]
            retry_scheduled = False
            try:
                with self._processing_lock:
                    current = self.repository.get_folder(folder.id)
                    if not current.enabled:
                        continue
                    if len(item) == 3:
                        destination = item[2]
                        if not self._process_move(current, path, destination):
                            retry_scheduled = self._schedule_retry(current, destination, path)
                            if not retry_scheduled:
                                logger.warning("Movimiento omitido tras agotar reintentos de estabilidad: %s", destination)
                    elif self.policy.eligible_path(path, current.path, current.include_subfolders, self._managed_roots):
                        if self._wait_stable(path):
                            stat = path.stat()
                            modified = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
                            indexed = self.repository.upsert_file(folder.id, path, stat.st_size, self.classifier.classify(path), modified, stat.st_mtime_ns)
                            self.on_file()
                            if self.on_indexed:
                                self.on_indexed(indexed.id)
                        elif path.is_file() and not path.is_symlink():
                            retry_scheduled = self._schedule_retry(current, path)
                            if not retry_scheduled:
                                logger.warning("Archivo omitido tras agotar reintentos de estabilidad: %s", path)
            except (OSError, LookupError, ValueError):
                logger.exception("No se pudo analizar %s", path)
            finally:
                if len(item) == 2 and not retry_scheduled:
                    with self._lock:
                        self._pending.discard(path)
                        self._retry_attempts.pop(path, None)
                elif len(item) == 3 and not retry_scheduled:
                    key = (path, item[2])
                    with self._lock:
                        self._pending_moves.discard(key)
                        self._retry_attempts.pop(key, None)

    def _process_move(self, folder: WatchedFolder, source: Path, destination: Path) -> bool:
        eligible = self.policy.eligible_path(destination, folder.path, folder.include_subfolders, self._managed_roots)
        entry = None
        if eligible:
            if not self._wait_stable(destination):
                if destination.is_file() and not destination.is_symlink():
                    return False
            else:
                stat = destination.stat()
                modified = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
                entry = IndexedEntry(destination, stat.st_size, self.classifier.classify(destination), modified, stat.st_mtime_ns)
        handled = self.repository.reconcile_external_move(folder, source, destination, self._managed_roots, entry)
        if entry is not None:
            indexed = self.repository.upsert_file(folder.id, entry.path, entry.size, entry.category, entry.modified_at, entry.mtime_ns)
            if self.on_indexed:
                self.on_indexed(indexed.id)
        if handled or entry is not None:
            self.on_file()
        return True

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
        logger.debug("Archivo aún en escritura: %s", path)
        return False

    def stop(self) -> None:
        self._stop.set()
        self.observer.stop()
        with self._retry_condition:
            self._retry_heap.clear()
            self._retry_condition.notify_all()
        self._queue.put(None)
        if self.observer.is_alive():
            self.observer.join(timeout=5)
        if self._worker.is_alive():
            self._worker.join(timeout=5)
        if self._retry_worker.is_alive():
            self._retry_worker.join(timeout=5)
        with self._lock:
            self._pending.clear()
            self._pending_moves.clear()
            self._retry_attempts.clear()

"""Two bounded workers coordinate manual and optional automatic extraction."""

import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ordenia.analysis import ContentAnalyzer
from ordenia.analysis.models import AnalysisOutcome
from ordenia.core.exclusions import ExclusionPolicy
from ordenia.database.repositories import Repository

logger = logging.getLogger(__name__)

_Task = tuple[int, int, bool, bool]


class ContentService(QObject):
    changed = Signal()
    progress = Signal(int, int, int)  # job, finished, total
    finished = Signal(int, int, int)

    def __init__(self, repository: Repository, workers: int = 2) -> None:
        super().__init__()
        self.repository = repository
        self.analyzer = ContentAnalyzer()
        self._queue: queue.Queue[_Task | None] = queue.Queue()
        self._lock = threading.Lock()
        self._pending: set[int] = set()
        self._jobs: dict[int, dict[str, object]] = {}
        self._auto_retries: dict[int, int] = {}
        self._next_job = 1
        self._last_changed = 0.0
        self._stop = threading.Event()
        self._threads = [threading.Thread(target=self._run, name=f"OrdenIA content {index + 1}", daemon=True)
                         for index in range(workers)]
        for thread in self._threads:
            thread.start()

    def request(self, file_ids: list[int], *, force: bool = False, automatic: bool = False) -> int:
        if automatic and self.repository.get_setting("auto_analyze_content", "0") != "1":
            return 0
        accepted: list[int] = []
        with self._lock:
            for file_id in dict.fromkeys(file_ids):
                if file_id not in self._pending:
                    self._pending.add(file_id)
                    accepted.append(file_id)
            if not accepted:
                return 0
            job_id = self._next_job
            self._next_job += 1
            self._jobs[job_id] = {"total": len(accepted), "done": 0, "cancel": threading.Event(), "last_progress": 0.0}
        for file_id in accepted:
            self._queue.put((file_id, job_id, force, automatic))
        self.progress.emit(job_id, 0, len(accepted))
        return job_id

    def cancel(self, job_id: int) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                event = job["cancel"]
                assert isinstance(event, threading.Event)
                event.set()

    def _emit_changed(self, *, force: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            emit = force or now - self._last_changed >= 0.2
            if emit:
                self._last_changed = now
        if emit:
            self.changed.emit()

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            if task is None:
                return
            file_id, job_id, force, automatic = task
            with self._lock:
                job = self._jobs.get(job_id)
                cancelled = self._stop.is_set() or job is None or bool(job["cancel"].is_set())  # type: ignore[union-attr]
            if automatic and self.repository.get_setting("auto_analyze_content", "0") != "1":
                cancelled = True
            try:
                if not cancelled:
                    self._process(file_id, force, automatic)
            except Exception:
                logger.exception("Error inesperado al analizar el archivo %s", file_id)
                try:
                    self.repository.content.save(file_id, AnalysisOutcome("failed", error="Error interno durante el análisis."), 0, 0)
                except Exception:
                    logger.exception("No se pudo registrar el fallo de análisis")
                self._emit_changed()
            finally:
                retry = False
                with self._lock:
                    self._pending.discard(file_id)
                    job = self._jobs.get(job_id)
                    if job:
                        job["done"] = int(job["done"]) + 1
                        done, total = int(job["done"]), int(job["total"])
                        now = time.monotonic()
                        send_progress = done == total or now - float(job["last_progress"]) >= 0.2
                        if send_progress:
                            job["last_progress"] = now
                        if done == total:
                            del self._jobs[job_id]
                    else:
                        done = total = 0
                        send_progress = False
                    if automatic and not cancelled and not self._stop.is_set():
                        analysis = self.repository.content.get(file_id)
                        if analysis.status == "stale" and self.repository.get_file(file_id).index_state == "active":
                            attempts = self._auto_retries.get(file_id, 0)
                            if attempts < 2:
                                self._auto_retries[file_id] = attempts + 1
                                retry = True
                        elif analysis.status == "indexed":
                            self._auto_retries.pop(file_id, None)
                if total and send_progress:
                    self.progress.emit(job_id, done, total)
                if total and done == total:
                    self._emit_changed(force=True)
                    self.finished.emit(job_id, done, total)
                if retry:
                    self.request([file_id], automatic=True)

    def _process(self, file_id: int, force: bool, automatic: bool) -> None:
        file = self.repository.get_file(file_id)
        if file.index_state != "active":
            return
        if automatic and not self.analyzer.registry.supports(file.path):
            return
        path = file.path
        try:
            stat = path.stat()
            if not path.is_file() or path.is_symlink():
                raise FileNotFoundError(path)
        except OSError:
            self.repository.content.save(file_id, AnalysisOutcome("failed", error="El archivo ya no está disponible."), 0, 0)
            self._emit_changed()
            return
        modified = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
        if not self.repository.refresh_fingerprint(file_id, path, stat.st_size, stat.st_mtime_ns, modified):
            return
        current = self.repository.content.get(file_id)
        folder = self.repository.get_folder(file.watched_folder_id)
        managed_roots = self.repository.list_managed_roots() + tuple(
            item.path / "OrdenIA" for item in self.repository.list_folders()
        )
        if not ExclusionPolicy().eligible_path(
            path, folder.path, folder.include_subfolders, managed_roots, assume_regular=True
        ):
            if (file.status == "Organizado" and current.status in {"indexed", "no_text"}
                    and (current.fingerprint_size, current.fingerprint_mtime_ns) == (stat.st_size, stat.st_mtime_ns)):
                return
            self.repository.content.save(file_id, AnalysisOutcome(
                "skipped", error="Archivo excluido por la política de OrdenIA."
            ), stat.st_size, stat.st_mtime_ns)
            self._emit_changed()
            return
        if (not force and current.status in {"indexed", "no_text", "unsupported", "skipped"}
                and (current.fingerprint_size, current.fingerprint_mtime_ns) == (stat.st_size, stat.st_mtime_ns)):
            return
        self.repository.content.mark_analyzing(file_id)
        self._emit_changed()
        automatic_limit = max(1, min(50, int(self.repository.get_setting("auto_analysis_max_mb", "50")))) * 1024 * 1024
        if automatic and stat.st_size > automatic_limit:
            outcome = AnalysisOutcome("skipped", error="Archivo demasiado grande para análisis automático.")
        else:
            outcome = self.analyzer.analyze(path)
        latest = self.repository.get_file(file_id)
        try:
            latest_stat = latest.path.stat()
            stale = latest.index_state != "active" or (latest_stat.st_size, latest_stat.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns)
        except OSError:
            stale = True
        self.repository.content.save(file_id, outcome, stat.st_size, stat.st_mtime_ns, stale=stale)
        self._emit_changed()

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            for job in self._jobs.values():
                job["cancel"].set()  # type: ignore[union-attr]
        for _ in self._threads:
            self._queue.put(None)
        for thread in self._threads:
            thread.join()

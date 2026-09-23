"""Single-worker orchestration for optional local AI suggestions."""

import logging
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QObject, Signal

from ordenia.ai.context import ContentContextBuilder
from ordenia.ai.models import DEFAULT_AI_MODEL, ProviderStatus
from ordenia.ai.parser import InvalidSuggestion, parse_suggestion
from ordenia.ai.prompts import SYSTEM_PROMPT, user_prompt
from ordenia.ai.providers import AIProvider, ModelNotInstalled, OllamaProvider, ProviderTimeout, ProviderUnavailable
from ordenia.database.repositories import Repository
from ordenia.services.content_service import ContentService
from ordenia.services.file_locks import FileOperationLocks
from ordenia.services.local_inference import LOCAL_INFERENCE_GATE

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _AnalyzeTask:
    file_id: int
    job_id: int
    force: bool


@dataclass(frozen=True)
class _HealthTask:
    pass


_Task = _AnalyzeTask | _HealthTask | None


class AIService(QObject):
    """Coordinates content preparation and one local inference at a time."""

    changed = Signal(int)
    progress = Signal(int, int, int)
    finished = Signal(int, int, int)
    provider_status = Signal(object)

    def __init__(self, repository: Repository, content: ContentService,
                 provider: AIProvider | None = None,
                 operation_locks: FileOperationLocks | None = None) -> None:
        super().__init__()
        self.repository = repository
        self.content = content
        self.provider = provider or OllamaProvider()
        self.operation_locks = operation_locks or FileOperationLocks()
        self.context_builder = ContentContextBuilder(repository)
        self._queue: queue.Queue[_Task] = queue.Queue()
        self._guard = threading.Lock()
        self._pending: set[int] = set()
        self._jobs: dict[int, dict[str, object]] = {}
        self._next_job = 1
        self._health_pending = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="OrdenIA local AI", daemon=True)
        self._thread.start()

    def request(self, file_ids: list[int], *, force: bool = False) -> int:
        accepted: list[int] = []
        with self._guard:
            for file_id in dict.fromkeys(file_ids):
                if file_id not in self._pending:
                    self._pending.add(file_id)
                    accepted.append(file_id)
            if not accepted:
                return 0
            job_id = self._next_job
            self._next_job += 1
            self._jobs[job_id] = {"total": len(accepted), "done": 0, "cancel": threading.Event()}
        for file_id in accepted:
            self.repository.ai.set_status(file_id, "pending")
            self._queue.put(_AnalyzeTask(file_id, job_id, force))
        self.progress.emit(job_id, 0, len(accepted))
        return job_id

    def cancel(self, job_id: int) -> None:
        with self._guard:
            job = self._jobs.get(job_id)
            if job is not None:
                cancel = job["cancel"]
                assert isinstance(cancel, threading.Event)
                cancel.set()

    def check_connection(self) -> None:
        with self._guard:
            if self._health_pending:
                return
            self._health_pending = True
        self._queue.put(_HealthTask())

    def _cancelled(self, job_id: int) -> bool:
        with self._guard:
            job = self._jobs.get(job_id)
            if job is None:
                return True
            event = job["cancel"]
            assert isinstance(event, threading.Event)
            return self._stop.is_set() or event.is_set()

    def _prepare_content(self, task: _AnalyzeTask) -> None:
        with self.operation_locks.hold(task.file_id):
            file = self.repository.get_file(task.file_id)
            try:
                stat = file.path.stat()
            except OSError as exc:
                raise FileNotFoundError("El archivo ya no está disponible.") from exc
            modified = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
            self.repository.refresh_fingerprint(task.file_id, file.path, stat.st_size, stat.st_mtime_ns, modified)
        analysis = self.repository.content.get(task.file_id)
        if analysis.status in {"pending", "stale", "failed", "analyzing"}:
            before = (analysis.status, analysis.analyzed_at)
            requested = self.content.request(
                [task.file_id], force=task.force or analysis.status in {"stale", "failed"}
            )
            deadline = time.monotonic() + 180
            observed_work = requested == 0
            while True:
                if self._cancelled(task.job_id):
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError("El análisis de contenido tardó demasiado.")
                self._stop.wait(0.1)
                analysis = self.repository.content.get(task.file_id)
                observed_work = observed_work or analysis.status == "analyzing" or (
                    analysis.status, analysis.analyzed_at
                ) != before
                if observed_work and analysis.status not in {"pending", "stale", "analyzing"}:
                    break

    def _process(self, task: _AnalyzeTask) -> None:
        self._prepare_content(task)
        if self._cancelled(task.job_id):
            self.repository.ai.set_status(task.file_id, "pending", error="Análisis cancelado.")
            return
        model = self.repository.ai.get_setting("model", DEFAULT_AI_MODEL).strip()
        if not model or len(model) > 120 or any(ord(char) < 32 for char in model):
            raise ValueError("El nombre del modelo configurado no es válido.")
        provider_name = self.repository.ai.get_setting("provider", "ollama")
        if provider_name != self.provider.name:
            raise ProviderUnavailable("El proveedor de IA configurado no está disponible.")
        with self.operation_locks.hold(task.file_id):
            file = self.repository.get_file(task.file_id)
            if file.index_state != "active":
                raise FileNotFoundError("El archivo ya no está disponible.")
            try:
                stat = file.path.stat()
            except OSError as exc:
                raise FileNotFoundError("El archivo ya no está disponible.") from exc
            size, mtime_ns = stat.st_size, stat.st_mtime_ns
            analysis = self.repository.content.get(task.file_id)
            if (analysis.fingerprint_size, analysis.fingerprint_mtime_ns) != (size, mtime_ns):
                self.repository.ai.set_status(task.file_id, "stale", error="El contenido cambió; vuelve a analizarlo.")
                return
            context = self.context_builder.build(task.file_id)
        self.repository.ai.set_status(task.file_id, "analyzing", provider=self.provider.name, model=model)
        self.changed.emit(task.file_id)
        with LOCAL_INFERENCE_GATE.hold():
            response = self.provider.generate(SYSTEM_PROMPT, user_prompt(context), model)
        suggestion = parse_suggestion(response.content)
        if self._cancelled(task.job_id):
            self.repository.ai.set_status(task.file_id, "pending", error="Análisis cancelado.")
            return
        with self.operation_locks.hold(task.file_id):
            latest = self.repository.get_file(task.file_id)
            try:
                latest_stat = latest.path.stat()
            except OSError:
                self.repository.ai.set_status(task.file_id, "stale", error="El archivo cambió durante el análisis inteligente.")
                return
            if latest.index_state != "active" or (latest_stat.st_size, latest_stat.st_mtime_ns) != (size, mtime_ns):
                modified = datetime.fromtimestamp(latest_stat.st_mtime).astimezone().isoformat(timespec="seconds")
                self.repository.refresh_fingerprint(
                    task.file_id, latest.path, latest_stat.st_size, latest_stat.st_mtime_ns, modified
                )
                self.repository.ai.set_status(task.file_id, "stale", error="El archivo cambió durante el análisis inteligente.")
                return
            self.repository.ai.save(task.file_id, suggestion, self.provider.name, model, size, mtime_ns)

    def _complete(self, task: _AnalyzeTask) -> None:
        with self._guard:
            self._pending.discard(task.file_id)
            job = self._jobs.get(task.job_id)
            if job is None:
                return
            job["done"] = int(job["done"]) + 1
            done, total = int(job["done"]), int(job["total"])
            if done == total:
                del self._jobs[task.job_id]
        self.progress.emit(task.job_id, done, total)
        self.changed.emit(task.file_id)
        if done == total:
            self.finished.emit(task.job_id, done, total)

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            if task is None:
                return
            if isinstance(task, _HealthTask):
                try:
                    self.provider_status.emit(self.provider.check())
                except Exception as exc:
                    logger.exception("No se pudo comprobar el proveedor local")
                    self.provider_status.emit(ProviderStatus(self.provider.name, False, (), str(exc)))
                finally:
                    with self._guard:
                        self._health_pending = False
                continue
            try:
                if self._cancelled(task.job_id):
                    self.repository.ai.set_status(task.file_id, "pending", error="Análisis cancelado.")
                else:
                    self._process(task)
            except (ProviderUnavailable, ModelNotInstalled) as exc:
                self.repository.ai.set_status(task.file_id, "unavailable", provider=self.provider.name,
                                              model=self.repository.ai.get_setting("model", DEFAULT_AI_MODEL), error=str(exc))
            except (ProviderTimeout, TimeoutError) as exc:
                self.repository.ai.set_status(task.file_id, "failed", provider=self.provider.name,
                                              model=self.repository.ai.get_setting("model", DEFAULT_AI_MODEL), error=str(exc))
            except (InvalidSuggestion, FileNotFoundError, LookupError, ValueError) as exc:
                self.repository.ai.set_status(task.file_id, "failed", provider=self.provider.name,
                                              model=self.repository.ai.get_setting("model", DEFAULT_AI_MODEL), error=str(exc))
            except Exception as exc:
                logger.exception("Error inesperado durante el análisis inteligente del archivo %s", task.file_id)
                self.repository.ai.set_status(task.file_id, "failed", provider=self.provider.name,
                                              model=self.repository.ai.get_setting("model", DEFAULT_AI_MODEL),
                                              error=f"Error interno durante el análisis inteligente: {exc}")
            finally:
                self._complete(task)

    def close(self) -> None:
        self._stop.set()
        with self._guard:
            for job in self._jobs.values():
                cancel = job["cancel"]
                assert isinstance(cancel, threading.Event)
                cancel.set()
        self._queue.put(None)
        self._thread.join(timeout=2.0)

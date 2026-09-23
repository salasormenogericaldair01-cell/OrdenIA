"""Process-wide coordination for local model inference."""

import threading
from contextlib import contextmanager
from collections.abc import Iterator


class LocalInferenceGate:
    """Allow one local inference at a time across all OrdenIA services."""

    def __init__(self, maximum: int = 1) -> None:
        if maximum < 1:
            raise ValueError("Debe permitirse al menos una inferencia local.")
        self._semaphore = threading.BoundedSemaphore(maximum)

    @contextmanager
    def hold(self) -> Iterator[None]:
        self._semaphore.acquire()
        try:
            yield
        finally:
            self._semaphore.release()


LOCAL_INFERENCE_GATE = LocalInferenceGate(1)

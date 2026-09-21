"""Reference-counted locks for operations on a single indexed file."""

import threading
from _thread import LockType
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class _Entry:
    lock: LockType = field(default_factory=threading.Lock)
    users: int = 0


class FileOperationLocks:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._entries: dict[int, _Entry] = {}

    @contextmanager
    def hold(self, file_id: int) -> Iterator[None]:
        with self._guard:
            entry = self._entries.setdefault(file_id, _Entry())
            entry.users += 1
        entry.lock.acquire()
        try:
            yield
        finally:
            entry.lock.release()
            with self._guard:
                entry.users -= 1
                if entry.users == 0:
                    del self._entries[file_id]

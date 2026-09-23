"""Conexiones cortas: cada hilo abre su propia conexión SQLite."""

import logging
import os
import sqlite3
import threading
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

_configured_paths: set[str] = set()
_configuration_lock = threading.Lock()


def _fold(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value).casefold())
    return "".join(character for character in normalized if not unicodedata.combining(character))


def initialize_database(db_path: Path) -> str:
    """Enable WAL once per database path and return the effective journal mode."""
    normalized = os.path.normcase(os.path.abspath(db_path.expanduser()))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _configuration_lock:
        if normalized in _configured_paths:
            with sqlite3.connect(db_path, timeout=10) as connection:
                return str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        with sqlite3.connect(db_path, timeout=10) as connection:
            connection.execute("PRAGMA busy_timeout = 10000")
            mode = str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
            connection.execute("PRAGMA synchronous=NORMAL")
        _configured_paths.add(normalized)
    if mode != "wal":
        logger.warning("SQLite no pudo habilitar WAL para %s; modo efectivo: %s", db_path, mode)
    return mode


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.create_function("CASEFOLD", 1, lambda value: str(value).casefold(), deterministic=True)
    connection.create_function("FOLD", 1, _fold, deterministic=True)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    # synchronous is connection-scoped; setting it is cheap and does not
    # renegotiate journal_mode on every short-lived connection.
    connection.execute("PRAGMA synchronous = NORMAL")
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()

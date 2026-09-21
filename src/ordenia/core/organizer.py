"""Movimientos aprobados y reversión de movimientos."""

import logging
import os
import shutil
import tempfile
from pathlib import Path

from .file_utils import available_path, is_inside
from .destinations import destination_directory

logger = logging.getLogger(__name__)


def verify_transfer(source: Path, destination: Path) -> None:
    """Check the final path transition; copy integrity is checked before unlink."""
    if not destination.is_file() or destination.is_symlink():
        raise OSError(f"El archivo no existe en el destino: {destination}")
    if source.exists() or source.is_symlink():
        raise OSError(f"El archivo todavía existe en el origen: {source}")


def _same_source(path: Path, original: os.stat_result) -> bool:
    try:
        current = path.stat()
        return (path.is_file() and not path.is_symlink()
                and (current.st_size, current.st_mtime_ns, current.st_dev, current.st_ino)
                == (original.st_size, original.st_mtime_ns, original.st_dev, original.st_ino))
    except OSError:
        return False


def _verify_copy(path: Path, expected_size: int) -> None:
    if not path.is_file() or path.is_symlink() or path.stat().st_size != expected_size:
        raise OSError(f"La copia no está completa: {path}")


def _copy_to_stage(source: Path, staged: Path) -> None:
    with source.open("rb") as incoming, staged.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    shutil.copystat(source, staged)


def _move_without_replacing(source: Path, target: Path) -> Path:
    """Keep source intact until a verified copy is published on the target volume."""
    if not source.is_file() or source.is_symlink():
        raise FileNotFoundError(source)
    original = source.stat()
    target.parent.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix=".ordenia-", dir=target.parent))
    staged = stage_dir / source.name
    published: Path | None = None
    published_identity: tuple[int, int] | None = None
    completed = False
    try:
        try:
            # Same volume: a hard link avoids copying while retaining source.
            os.link(source, staged)
        except OSError:
            # Different volume (or no hard-link support): copy on the destination
            # volume. A partial copy never removes the original.
            _copy_to_stage(source, staged)
        _verify_copy(staged, original.st_size)
        if not _same_source(source, original):
            raise OSError("El archivo de origen cambió durante la copia.")

        while True:
            candidate = available_path(target)
            try:
                # Staging and candidate are on the same volume. link() publishes
                # atomically and fails instead of replacing an occupied name.
                os.link(staged, candidate)
            except FileExistsError:
                continue
            except OSError:
                if os.name != "nt":
                    raise
                # On Windows rename() is atomic and raises if destination exists.
                try:
                    os.rename(staged, candidate)
                except FileExistsError:
                    continue
            published = candidate
            stat = candidate.stat()
            published_identity = (stat.st_dev, stat.st_ino)
            break

        _verify_copy(published, original.st_size)
        if not _same_source(source, original):
            raise OSError("El archivo de origen cambió antes de completar el movimiento.")
        try:
            source.unlink()
        except OSError:
            if source.exists() or source.is_symlink():
                raise
            # A filesystem may report an error after deleting the entry. The
            # verified destination is already recoverable in that case.
        completed = True
        return published
    finally:
        if completed:
            # Never remove staging if the destination unexpectedly vanished.
            if published is not None and published.is_file() and staged.exists():
                try:
                    staged.unlink()
                except OSError:
                    logger.warning("No se pudo limpiar el staging %s", staged, exc_info=True)
        elif _same_source(source, original):
            # The original is still complete. Remove only the publication we
            # created, and retain anything whose identity has changed.
            if published is not None and published_identity is not None:
                try:
                    stat = published.stat()
                    if (stat.st_dev, stat.st_ino) == published_identity:
                        published.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    logger.warning("No se pudo limpiar la publicación %s", published, exc_info=True)
            if staged.exists():
                try:
                    staged.unlink()
                except OSError:
                    logger.warning("No se pudo limpiar el staging %s", staged, exc_info=True)
        else:
            logger.error("Origen alterado o ausente; se conserva staging en %s", staged)
        try:
            stage_dir.rmdir()
        except OSError:
            pass


class Organizer:
    def proposed_destination(self, source: Path, watched_root: Path, category: str, destination_root: Path | None = None) -> Path:
        if not is_inside(source, watched_root):
            raise ValueError("El archivo no pertenece a la carpeta vigilada.")
        if is_inside(source, watched_root / "OrdenIA"):
            raise ValueError("El archivo ya está dentro de OrdenIA.")
        base = destination_root or watched_root / "OrdenIA"
        if is_inside(source, base):
            raise ValueError("El archivo ya está dentro del destino de OrdenIA.")
        destination = destination_directory(base, category) / source.name
        return available_path(destination)

    def move(self, source: Path, watched_root: Path, category: str, destination_root: Path | None = None) -> Path:
        if not source.is_file() or source.is_symlink():
            raise FileNotFoundError(source)
        destination = self.proposed_destination(source, watched_root, category, destination_root)
        return _move_without_replacing(source, destination)

    def undo(self, destination: Path, original: Path) -> Path:
        if not destination.is_file() or destination.is_symlink():
            raise FileNotFoundError(destination)
        return _move_without_replacing(destination, original)

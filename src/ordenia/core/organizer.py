"""Movimientos aprobados y reversión de movimientos."""

import logging
import os
import shutil
import tempfile
from pathlib import Path

from .file_utils import available_path, is_inside

logger = logging.getLogger(__name__)


def verify_transfer(source: Path, destination: Path) -> None:
    """Require the real file at destination and no entry at its old path."""
    if not destination.is_file() or destination.is_symlink():
        raise OSError(f"El archivo no existe en el destino: {destination}")
    if source.exists() or source.is_symlink():
        raise OSError(f"El archivo todavía existe en el origen: {source}")


def _move_without_replacing(source: Path, target: Path) -> Path:
    """Use shutil.move for staging, then publish under an exclusive name."""
    target.parent.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix=".ordenia-", dir=target.parent))
    staged = stage_dir / source.name
    moved_to_stage = False
    published: Path | None = None
    try:
        shutil.move(str(source), str(staged))
        moved_to_stage = True
        while True:
            candidate = available_path(target)
            try:
                # Hard-link creation is atomic and fails if a name appeared meanwhile.
                os.link(staged, candidate)
            except FileExistsError:
                continue
            except OSError:
                # Some Windows volumes do not support hard links. 'xb' is exclusive.
                try:
                    with staged.open("rb") as incoming, candidate.open("xb") as outgoing:
                        shutil.copyfileobj(incoming, outgoing)
                    shutil.copystat(staged, candidate)
                except FileExistsError:
                    continue
                except Exception:
                    if candidate.exists():
                        candidate.unlink()
                    raise
            published = candidate
            moved_to_stage = False
            return candidate
    finally:
        if published is not None and staged.exists():
            try:
                staged.unlink()
            except OSError:
                logger.warning("El archivo se movió, pero no se pudo limpiar el temporal %s", staged, exc_info=True)
        elif moved_to_stage and staged.exists():
            rollback = available_path(source)
            try:
                shutil.move(str(staged), str(rollback))
                logger.warning("Movimiento revertido a %s", rollback)
            except OSError:
                logger.exception("No se pudo restaurar %s; quedó en %s", source, staged)
        elif published is None and staged.exists():
            # A failed cross-volume copy may leave only a partial staging file.
            staged.unlink()
        if not any(stage_dir.iterdir()):
            stage_dir.rmdir()


class Organizer:
    def proposed_destination(self, source: Path, watched_root: Path, category: str, destination_root: Path | None = None) -> Path:
        if not is_inside(source, watched_root):
            raise ValueError("El archivo no pertenece a la carpeta vigilada.")
        if is_inside(source, watched_root / "OrdenIA"):
            raise ValueError("El archivo ya está dentro de OrdenIA.")
        base = destination_root or watched_root / "OrdenIA"
        if is_inside(source, base):
            raise ValueError("El archivo ya está dentro del destino de OrdenIA.")
        destination = base / category / source.name
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

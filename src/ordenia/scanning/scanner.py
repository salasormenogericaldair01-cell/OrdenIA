"""Filesystem traversal without reading file content or changing files."""

import logging
import os
from collections.abc import Iterator
from pathlib import Path

from ordenia.core.exclusions import ExclusionPolicy

logger = logging.getLogger(__name__)


class FileScanner:
    def __init__(self, policy: ExclusionPolicy | None = None) -> None:
        self.policy = policy or ExclusionPolicy()

    def iter_paths(
        self, root: Path, include_subfolders: bool,
        managed_roots: tuple[Path, ...] = (),
    ) -> Iterator[Path]:
        stack = [root]
        while stack:
            directory = stack.pop()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        path = Path(entry.path)
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if include_subfolders and not self.policy.excluded_directory(path, managed_roots):
                                    stack.append(path)
                            elif entry.is_file(follow_symlinks=False) and self.policy.eligible_path(path, root, include_subfolders, managed_roots, assume_regular=True):
                                yield path
                        except OSError:
                            logger.warning("No se pudo inspeccionar %s", path, exc_info=True)
            except OSError:
                logger.warning("No se pudo abrir la carpeta %s", directory, exc_info=True)

    def count(self, root: Path, include_subfolders: bool, managed_roots: tuple[Path, ...] = ()) -> int:
        return sum(1 for _ in self.iter_paths(root, include_subfolders, managed_roots))

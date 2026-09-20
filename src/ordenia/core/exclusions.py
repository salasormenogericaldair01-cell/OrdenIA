"""One policy for filesystem events and existing-file scans."""

from pathlib import Path

SYSTEM_FILES = frozenset({"desktop.ini", "thumbs.db", "ehthumbs.db", ".ds_store"})
SYSTEM_DIRECTORIES = frozenset({"$recycle.bin", "system volume information", "__pycache__", ".git", ".venv", "node_modules"})
TEMPORARY_SUFFIXES = frozenset({".tmp", ".temp", ".part", ".crdownload", ".download", ".swp", ".swo", ".bak"})


class ExclusionPolicy:
    @staticmethod
    def _under(path: Path, root: Path) -> bool:
        # Watched and managed roots are normalized when configured. A lexical
        # check avoids thousands of filesystem resolve() calls during a scan.
        return path == root or root in path.parents

    def excluded_name(self, path: Path) -> bool:
        name = path.name.casefold()
        return (
            name in SYSTEM_FILES
            or name.startswith("~$")
            or name.endswith("~")
            or path.suffix.casefold() in TEMPORARY_SUFFIXES
            or name.startswith(".ordenia-")
        )

    def excluded_directory(self, path: Path, managed_roots: tuple[Path, ...]) -> bool:
        return (
            path.name.casefold() in SYSTEM_DIRECTORIES
            or path.name.casefold().startswith(".ordenia-")
            or path.is_symlink()
            or any(self._under(path, managed) for managed in managed_roots)
        )

    def eligible_path(
        self, path: Path, watched_root: Path, include_subfolders: bool,
        managed_roots: tuple[Path, ...] = (), assume_regular: bool = False,
    ) -> bool:
        if not self._under(path, watched_root) or path == watched_root:
            return False
        if not include_subfolders and path.parent != watched_root:
            return False
        if self.excluded_name(path) or (not assume_regular and path.is_symlink()):
            return False
        if any(self._under(path, managed) for managed in managed_roots):
            return False
        try:
            relative = path.relative_to(watched_root)
        except ValueError:
            return False
        return not any(part.casefold() in SYSTEM_DIRECTORIES or part.casefold().startswith(".ordenia-") for part in relative.parts[:-1])

"""Deterministic safety policy for filesystem automation actions."""

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from ordenia.database.models import DetectedFile, WatchedFolder
from ordenia.platform.runtime import application_directory, data_directory

from .models import RiskLevel


LARGE_FILE_THRESHOLD = 500 * 1024 * 1024
PROTECTED_EXTENSIONS = frozenset({".iso", ".img", ".vhd", ".vhdx", ".dll", ".sys"})
PROTECTED_DIRECTORY_NAMES = frozenset({".git", ".venv", "node_modules"})


@dataclass(frozen=True)
class PolicyDecision:
    level: RiskLevel
    code: str
    reason: str


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _path_key(path: Path) -> str:
    return os.path.normcase(str(_absolute(path)))


def _under(path: Path, root: Path) -> bool:
    path_value = _path_key(path)
    root_value = _path_key(root)
    try:
        return os.path.commonpath((path_value, root_value)) == root_value
    except ValueError:
        return False


def _default_system_roots() -> tuple[Path, ...]:
    names = ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "APPDATA", "LOCALAPPDATA")
    roots = {_path_key(Path(value)): Path(value) for name in names if (value := os.environ.get(name))}
    return tuple(roots.values())


class AutomationPolicy:
    """Classify a file for a future action without modifying it."""

    def __init__(
        self,
        *,
        protected_paths: tuple[Path, ...] = (),
        system_roots: tuple[Path, ...] | None = None,
        local_data_root: Path | None = None,
        workspace_root: Path | None = None,
        large_file_threshold: int = LARGE_FILE_THRESHOLD,
    ) -> None:
        if large_file_threshold < 0:
            raise ValueError("El umbral de archivos grandes no puede ser negativo.")
        roots = _default_system_roots() if system_roots is None else system_roots
        local_root = data_directory() if local_data_root is None else local_data_root
        workspace = application_directory() if workspace_root is None else workspace_root
        self.protected_paths = tuple(_absolute(path) for path in (*roots, local_root, workspace, *protected_paths))
        self.large_file_threshold = large_file_threshold

    @staticmethod
    def _decision(level: RiskLevel, code: str, reason: str) -> PolicyDecision:
        return PolicyDecision(level, code, reason)

    @staticmethod
    def _has_reserved_segment(path: Path) -> bool:
        return any(part.casefold() in PROTECTED_DIRECTORY_NAMES for part in _absolute(path).parts)

    @staticmethod
    def _inside_git_repository(path: Path) -> bool:
        current = _absolute(path).parent
        while True:
            if (current / ".git").exists():
                return True
            if current.parent == current:
                return False
            current = current.parent

    @staticmethod
    def _has_untrusted_reparse(path: Path, watched_root: Path) -> bool:
        current = _absolute(path)
        stop = _absolute(watched_root).parent
        while current != stop:
            try:
                info = os.lstat(current)
            except OSError:
                return False
            attributes = int(getattr(info, "st_file_attributes", 0))
            reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
            if stat.S_ISLNK(info.st_mode) or attributes & reparse_flag:
                return True
            if current.parent == current:
                break
            current = current.parent
        return False

    def evaluate(self, file: DetectedFile, folder: WatchedFolder) -> PolicyDecision:
        path = _absolute(file.path)
        watched_root = _absolute(folder.path)

        if file.index_state != "active":
            return self._decision(RiskLevel.PROTECTED, "inactive_record", "El registro no está activo en el índice.")
        if file.status != "Pendiente":
            return self._decision(RiskLevel.PROTECTED, "not_pending", "El archivo no está pendiente de organización.")
        if not path.is_file():
            return self._decision(RiskLevel.PROTECTED, "missing_file", "El archivo ya no existe en su ruta registrada.")
        if file.extension.casefold() in PROTECTED_EXTENSIONS or path.suffix.casefold() in PROTECTED_EXTENSIONS:
            return self._decision(RiskLevel.PROTECTED, "protected_extension", "Este tipo de archivo está protegido por seguridad.")
        if not _under(path, watched_root) or (not folder.include_subfolders and path.parent != watched_root):
            return self._decision(RiskLevel.PROTECTED, "outside_watched_folder", "La ruta está fuera del ámbito vigilado.")
        if any(_under(path, root) for root in self.protected_paths):
            return self._decision(RiskLevel.PROTECTED, "protected_path", "La ruta pertenece a una ubicación protegida.")
        if self._has_reserved_segment(path):
            return self._decision(RiskLevel.PROTECTED, "protected_directory", "La ruta contiene una carpeta protegida.")
        if self._inside_git_repository(path):
            return self._decision(RiskLevel.PROTECTED, "git_repository", "Los repositorios Git están protegidos.")
        if self._has_untrusted_reparse(path, watched_root):
            return self._decision(RiskLevel.PROTECTED, "untrusted_reparse_point", "La ruta atraviesa un enlace o punto de reanálisis no confiable.")
        if file.size > self.large_file_threshold:
            return self._decision(RiskLevel.REVIEW_REQUIRED, "large_file", "El archivo supera 500 MB y requiere revisión manual.")
        return self._decision(RiskLevel.NORMAL, "eligible", "El archivo cumple la política de automatización.")

    def evaluate_destination_root(self, root: Path) -> PolicyDecision:
        """Accept only configured roots that do not enter protected locations."""
        path = _absolute(root)
        if any(_under(path, protected) for protected in self.protected_paths):
            return self._decision(RiskLevel.PROTECTED, "protected_destination_root", "La raíz de destino pertenece a una ubicación protegida.")
        if self._has_reserved_segment(path) or self._inside_git_repository(path):
            return self._decision(RiskLevel.PROTECTED, "protected_destination_root", "La raíz de destino pertenece a una carpeta protegida.")
        return self._decision(RiskLevel.NORMAL, "trusted_destination_root", "La raíz procede de la configuración de la carpeta vigilada.")

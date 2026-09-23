"""Destination choices shared by services, settings and safe plans."""

from pathlib import Path, PureWindowsPath

from ordenia.core.file_utils import is_inside

INSIDE = "inside"
CENTRAL = "central"
CUSTOM = "custom"
STRATEGIES = frozenset({INSIDE, CENTRAL, CUSTOM})
_INVALID_WINDOWS_CHARS = frozenset('<>:"|?*%')
_RESERVED_WINDOWS_NAMES = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))})


def validate_relative_destination(value: str | Path) -> str:
    """Return a canonical safe relative directory using forward slashes."""
    raw = str(value)
    windows = PureWindowsPath(raw)
    if not raw or raw != raw.strip() or windows.is_absolute() or windows.drive or windows.root:
        raise ValueError("La ruta de destino debe ser relativa.")
    parts = tuple(part for part in raw.replace("\\", "/").split("/") if part)
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError("La ruta de destino no puede salir de la raíz configurada.")
    for part in parts:
        if part != part.strip() or part.endswith((".", " ")):
            raise ValueError("La ruta de destino contiene espacios o puntos no seguros.")
        if any(ord(character) < 32 or character in _INVALID_WINDOWS_CHARS for character in part):
            raise ValueError("La ruta de destino contiene caracteres no permitidos.")
        if part.split(".", 1)[0].casefold() in _RESERVED_WINDOWS_NAMES:
            raise ValueError("La ruta de destino usa un nombre reservado de Windows.")
    return "/".join(parts)


def destination_directory(root: Path, relative_group: str | Path) -> Path:
    """Resolve a safe relative group below a destination root.

    Extension rules may pass one category and V0.4 suggestions may pass a
    multi-part relative path without changing the destination strategy.
    """
    group = Path(*validate_relative_destination(relative_group).split("/"))
    directory = root / group
    if not is_inside(directory, root):
        raise ValueError("La ruta sugerida debe estar dentro del destino de OrdenIA.")
    return directory


def destination_root(
    watched_root: Path, strategy: str, central_root: Path,
    custom_root: Path | None = None,
) -> Path:
    if strategy == INSIDE:
        root = watched_root / "OrdenIA"
    elif strategy == CENTRAL:
        root = central_root
    elif strategy == CUSTOM and custom_root is not None:
        root = custom_root
    else:
        raise ValueError("Selecciona un destino válido.")
    root = root.expanduser().resolve()
    if is_inside(watched_root, root):
        raise ValueError("El destino no puede contener toda la carpeta vigilada.")
    return root

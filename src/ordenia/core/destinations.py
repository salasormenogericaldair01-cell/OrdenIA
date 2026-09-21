"""Destination choices shared by the service and folder settings."""

from pathlib import Path

from ordenia.core.file_utils import is_inside

INSIDE = "inside"
CENTRAL = "central"
CUSTOM = "custom"
STRATEGIES = frozenset({INSIDE, CENTRAL, CUSTOM})


def destination_directory(root: Path, relative_group: str | Path) -> Path:
    """Resolve a safe relative group below a destination root.

    Extension rules may pass one category and V0.4 suggestions may pass a
    multi-part relative path without changing the destination strategy.
    """
    group = Path(relative_group)
    if not group.parts or group.is_absolute() or group.drive or ".." in group.parts:
        raise ValueError("La ruta sugerida debe estar dentro del destino de OrdenIA.")
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

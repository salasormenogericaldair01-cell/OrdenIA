"""Destination choices shared by the service and folder settings."""

from pathlib import Path

from ordenia.core.file_utils import is_inside

INSIDE = "inside"
CENTRAL = "central"
CUSTOM = "custom"
STRATEGIES = frozenset({INSIDE, CENTRAL, CUSTOM})


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

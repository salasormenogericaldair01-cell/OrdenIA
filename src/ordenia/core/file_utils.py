"""Validaciones y nombres libres para movimientos seguros."""

from pathlib import Path

TEMP_SUFFIXES = frozenset({".tmp", ".part", ".crdownload", ".download", ".swp", ".swo", ".bak"})


def is_temporary(path: Path) -> bool:
    name = path.name.lower()
    return (
        name.startswith("~$")
        or name.startswith(".")
        or name.endswith("~")
        or path.suffix.lower() in TEMP_SUFFIXES
        or any(part.startswith(".ordenia-") for part in path.parts)
    )


def available_path(target: Path) -> Path:
    """Return the first free sibling name without changing the filesystem."""
    if not target.exists() and not target.is_symlink():
        return target
    suffix = "".join(target.suffixes)
    stem = target.name[: -len(suffix)] if suffix else target.name
    index = 1
    while True:
        candidate = target.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        index += 1


def is_inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False

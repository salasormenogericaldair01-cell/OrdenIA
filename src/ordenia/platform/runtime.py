"""Runtime paths that work from source and from a frozen Windows build."""

import os
import sys
from collections.abc import Mapping
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def application_directory() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def data_directory(environ: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environ is None else environ
    base = values.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / "AppData" / "Local") / "OrdenIA"

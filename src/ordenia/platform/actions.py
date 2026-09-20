"""Windows Explorer integration with graceful alternatives."""

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths


def default_central_root() -> Path:
    documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return (Path(documents) if documents else Path.home() / "Documents") / "OrdenIA"


def open_location(path: Path) -> None:
    target = path.resolve()
    if not target.exists():
        raise FileNotFoundError(f"La ruta ya no existe: {target}")
    if sys.platform == "win32":
        if target.is_file():
            subprocess.Popen(["explorer.exe", f"/select,{target}"])
        else:
            subprocess.Popen(["explorer.exe", str(target)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(target)] if target.is_file() else ["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target.parent if target.is_file() else target)])


def open_file(path: Path) -> None:
    target = path.resolve()
    if not target.is_file():
        raise FileNotFoundError(f"El archivo ya no existe: {target}")
    if sys.platform == "win32":
        os.startfile(str(target))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])

# PyInstaller one-folder build for OrdenIA on Windows.
import os
from pathlib import Path

project_root = Path(SPECPATH).resolve().parent
entry_point = project_root / "src" / "ordenia" / "main.py"
icon_candidate = project_root / "packaging" / "assets" / "ordenia.ico"
version_candidate = Path(os.environ.get(
    "ORDENIA_VERSION_FILE", str(project_root / "build" / "ordenia_version_info.txt")
))

a = Analysis(
    [str(entry_point)],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OrdenIA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(icon_candidate) if icon_candidate.is_file() else None,
    version=str(version_candidate) if version_candidate.is_file() else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="OrdenIA",
)

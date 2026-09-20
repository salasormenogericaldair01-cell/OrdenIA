from pathlib import Path
from types import SimpleNamespace

from ordenia.platform import actions


def test_open_location_selects_file_in_windows_explorer(tmp_path: Path, monkeypatch) -> None:
    file = tmp_path / "hello world.pdf"
    file.write_text("hello")
    calls: list[list[str]] = []
    monkeypatch.setattr(actions, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(actions.subprocess, "Popen", lambda args: calls.append(args))
    actions.open_location(file)
    assert calls == [["explorer.exe", f"/select,{file}"]]

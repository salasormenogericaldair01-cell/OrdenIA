"""Deterministic safety decisions for V0.5 organization plans."""

from dataclasses import replace
from pathlib import Path

import pytest

from ordenia.automation.models import RiskLevel
from ordenia.automation.policy import AutomationPolicy, LARGE_FILE_THRESHOLD
from ordenia.database.models import DetectedFile, WatchedFolder


def _folder(root: Path, *, recursive: bool = True) -> WatchedFolder:
    return WatchedFolder(1, root, True, recursive)


def _file(path: Path, *, size: int | None = None, state: str = "active", status: str = "Pendiente") -> DetectedFile:
    stat = path.stat() if path.exists() else None
    return DetectedFile(
        1, 1, path, str(path).casefold(), path.parent, path.name, path.suffix.lower(),
        size if size is not None else (stat.st_size if stat else 0), "Documentos",
        "2026-01-01", "2026-01-01", status, state, stat.st_mtime_ns if stat else 0,
    )


def _policy(tmp_path: Path, **changes: object) -> AutomationPolicy:
    options: dict[str, object] = {
        "system_roots": (),
        "local_data_root": tmp_path / "guard" / "data",
        "workspace_root": tmp_path / "guard" / "workspace",
    }
    options.update(changes)
    return AutomationPolicy(**options)


def test_normal_pdf_is_eligible(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "report.pdf"
    path.write_text("safe")
    decision = _policy(tmp_path).evaluate(_file(path), _folder(root))
    assert decision.level is RiskLevel.NORMAL
    assert decision.code == "eligible"


def test_file_over_500_mb_requires_review(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "large.pdf"
    with path.open("wb") as stream:
        stream.truncate(LARGE_FILE_THRESHOLD + 1)
    decision = _policy(tmp_path).evaluate(_file(path), _folder(root))
    assert decision.level is RiskLevel.REVIEW_REQUIRED
    assert decision.code == "large_file"


@pytest.mark.parametrize("suffix", [".iso", ".img", ".vhd", ".vhdx", ".dll", ".sys"])
def test_sensitive_disk_and_system_extensions_are_protected(tmp_path: Path, suffix: str) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / f"system{suffix}"
    path.write_bytes(b"x")
    assert _policy(tmp_path).evaluate(_file(path), _folder(root)).code == "protected_extension"


@pytest.mark.parametrize("directory", [".git", ".venv", "node_modules"])
def test_sensitive_directories_are_protected(tmp_path: Path, directory: str) -> None:
    root = tmp_path / "watched"
    path = root / directory / "file.txt"
    path.parent.mkdir(parents=True)
    path.write_text("x")
    decision = _policy(tmp_path).evaluate(_file(path), _folder(root))
    assert decision.level is RiskLevel.PROTECTED
    assert decision.code == "protected_directory"


def test_git_repository_is_protected(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    (root / "project" / ".git").mkdir(parents=True)
    path = root / "project" / "notes.txt"
    path.write_text("x")
    assert _policy(tmp_path).evaluate(_file(path), _folder(root)).code == "git_repository"


@pytest.mark.parametrize("name", ["Windows", "Program Files", "Program Files (x86)", "ProgramData", "AppData"])
def test_injected_windows_system_roots_are_protected(tmp_path: Path, name: str) -> None:
    system_root = tmp_path / name
    watched = system_root / "watched"
    watched.mkdir(parents=True)
    path = watched / "file.txt"
    path.write_text("x")
    policy = _policy(tmp_path, system_roots=(system_root,))
    assert policy.evaluate(_file(path), _folder(watched)).code == "protected_path"


def test_workspace_and_ordenia_data_roots_are_protected(tmp_path: Path) -> None:
    for name, argument in (("workspace", "workspace_root"), ("ordenia-data", "local_data_root")):
        root = tmp_path / name
        root.mkdir()
        path = root / "file.txt"
        path.write_text("x")
        policy = _policy(tmp_path, **{argument: root})
        assert policy.evaluate(_file(path), _folder(root)).code == "protected_path"


def test_custom_protected_path_is_protected(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    protected = root / "private"
    protected.mkdir(parents=True)
    path = protected / "file.txt"
    path.write_text("x")
    policy = _policy(tmp_path, protected_paths=(protected,))
    assert policy.evaluate(_file(path), _folder(root)).code == "protected_path"


def test_untrusted_reparse_point_is_protected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "linked.txt"
    path.write_text("x")
    policy = _policy(tmp_path)
    monkeypatch.setattr(policy, "_has_untrusted_reparse", lambda *_args: True)
    assert policy.evaluate(_file(path), _folder(root)).code == "untrusted_reparse_point"


def test_missing_inactive_and_outside_records_are_protected(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    path = root / "file.txt"
    path.write_text("x")
    record = _file(path)
    policy = _policy(tmp_path)
    assert policy.evaluate(replace(record, index_state="missing"), _folder(root)).code == "inactive_record"
    path.unlink()
    assert policy.evaluate(record, _folder(root)).code == "missing_file"
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    assert policy.evaluate(replace(record, path=outside), _folder(root)).code == "outside_watched_folder"


def test_non_recursive_folder_rejects_nested_file(tmp_path: Path) -> None:
    root = tmp_path / "watched"
    path = root / "nested" / "file.txt"
    path.parent.mkdir(parents=True)
    path.write_text("x")
    assert _policy(tmp_path).evaluate(_file(path), _folder(root, recursive=False)).code == "outside_watched_folder"

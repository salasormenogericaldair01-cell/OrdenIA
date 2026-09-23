"""Bounded discovery of existing relative destination directories."""

import os
from pathlib import Path


def existing_relative_paths(
    roots: tuple[Path, ...], *, limit: int = 40, maximum_depth: int = 3,
) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for configured_root in roots:
        try:
            root = configured_root.resolve()
            if not root.is_dir():
                continue
        except OSError:
            continue
        for current, directories, _files in os.walk(root, followlinks=False):
            current_path = Path(current)
            try:
                depth = len(current_path.relative_to(root).parts)
            except ValueError:
                continue
            directories[:] = sorted(directories, key=str.casefold) if depth < maximum_depth else []
            for directory in directories:
                relative = (current_path / directory).relative_to(root).as_posix()
                key = relative.casefold()
                if key in seen:
                    continue
                seen.add(key)
                result.append(relative)
                if len(result) >= limit:
                    return tuple(result)
    return tuple(result)

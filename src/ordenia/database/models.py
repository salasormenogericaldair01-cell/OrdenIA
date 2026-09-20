from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WatchedFolder:
    id: int
    path: Path
    enabled: bool
    include_subfolders: bool = True
    destination_strategy: str = "inside"
    custom_destination: Path | None = None


@dataclass(frozen=True)
class DetectedFile:
    id: int
    watched_folder_id: int
    path: Path
    path_key: str
    source_directory: Path
    name: str
    extension: str
    size: int
    category: str
    detected_at: str
    modified_at: str
    status: str
    index_state: str = "active"


@dataclass(frozen=True)
class IndexedEntry:
    path: Path
    size: int
    category: str
    modified_at: str


@dataclass(frozen=True)
class ScanSummary:
    found: int
    new: int
    existing: int
    active: int
    missing: int
    excluded: int


@dataclass(frozen=True)
class Operation:
    id: int
    file_id: int
    original_path: Path
    destination_path: Path
    created_at: str
    operation_type: str
    status: str
    error_message: str | None
    undone_at: str | None
    restored_path: Path | None

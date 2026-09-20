"""Values shared by extractors, workers and the content index."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AnalysisLimits:
    max_file_bytes: int = 50 * 1024 * 1024
    max_office_file_bytes: int = 20 * 1024 * 1024
    max_zip_uncompressed_bytes: int = 100 * 1024 * 1024
    max_zip_members: int = 10_000
    max_extracted_characters: int = 250_000
    max_xlsx_cells: int = 50_000
    max_xlsx_columns: int = 200
    max_pdf_pages: int = 250
    max_pptx_slides: int = 300
    max_keywords: int = 15
    max_preview_characters: int = 500


@dataclass(frozen=True)
class ExtractedContent:
    text: str
    extractor: str
    title: str = ""
    author: str = ""
    subject: str = ""
    page_count: int | None = None
    slide_count: int | None = None
    truncated: bool = False


@dataclass(frozen=True)
class AnalysisOutcome:
    status: str
    extractor: str = ""
    text: str = ""
    title: str = ""
    author: str = ""
    subject: str = ""
    page_count: int | None = None
    slide_count: int | None = None
    keywords: tuple[str, ...] = ()
    error: str = ""
    truncated: bool = False


@dataclass(frozen=True)
class AnalysisRecord:
    file_id: int
    status: str = "pending"
    extractor: str = ""
    analyzed_at: str = ""
    title: str = ""
    author: str = ""
    subject: str = ""
    page_count: int | None = None
    slide_count: int | None = None
    character_count: int = 0
    text: str = ""
    keywords: tuple[str, ...] = field(default_factory=tuple)
    error: str = ""
    fingerprint_size: int = 0
    fingerprint_mtime_ns: int = 0
    truncated: bool = False

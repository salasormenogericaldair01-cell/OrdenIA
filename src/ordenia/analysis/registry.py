"""Replaceable extension-to-extractor mapping."""

from pathlib import Path

from .extractors.base import Extractor
from .extractors.docx import DocxExtractor
from .extractors.pdf import PdfExtractor
from .extractors.plain_text import PlainTextExtractor
from .extractors.pptx import PptxExtractor
from .extractors.source_code import SourceCodeExtractor
from .extractors.xlsx import XlsxExtractor

TEXT_EXTENSIONS = frozenset({".txt", ".md", ".log", ".csv"})
CODE_EXTENSIONS = frozenset({".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp",
                             ".h", ".hpp", ".ino", ".html", ".css", ".json", ".yaml", ".yml",
                             ".toml", ".sql", ".sh", ".ps1", ".bat", ".xml"})
LEGACY_EXTENSIONS = frozenset({".doc", ".xls", ".ppt"})


class ExtractorRegistry:
    def __init__(self) -> None:
        text = PlainTextExtractor()
        code = SourceCodeExtractor()
        self.extractors: dict[str, Extractor] = {
            **{suffix: text for suffix in TEXT_EXTENSIONS},
            **{suffix: code for suffix in CODE_EXTENSIONS},
            ".pdf": PdfExtractor(), ".docx": DocxExtractor(),
            ".xlsx": XlsxExtractor(), ".pptx": PptxExtractor(),
        }

    def get(self, path: Path) -> Extractor | None:
        return self.extractors.get(path.suffix.casefold())

    def supports(self, path: Path) -> bool:
        return self.get(path) is not None

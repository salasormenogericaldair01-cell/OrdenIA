"""Read a bounded text prefix; retry Windows text encodings without crashing."""

from pathlib import Path

from ordenia.analysis.models import AnalysisLimits, ExtractedContent


class PlainTextExtractor:
    def __init__(self, label: str = "Texto") -> None:
        self.label = label

    def extract(self, path: Path, limits: AnalysisLimits) -> ExtractedContent:
        amount = limits.max_extracted_characters + 1
        try:
            with path.open("r", encoding="utf-8-sig", errors="strict") as stream:
                text = stream.read(amount)
        except UnicodeDecodeError:
            with path.open("r", encoding="cp1252", errors="replace") as stream:
                text = stream.read(amount)
        return ExtractedContent(text[:limits.max_extracted_characters], self.label,
                                truncated=len(text) > limits.max_extracted_characters)

from pathlib import Path
from typing import Protocol

from ordenia.analysis.models import AnalysisLimits, ExtractedContent


class Extractor(Protocol):
    def extract(self, path: Path, limits: AnalysisLimits) -> ExtractedContent: ...
